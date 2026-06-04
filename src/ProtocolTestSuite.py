"""
Robust protocol game for the ECSDI agents.

It routes RDF/FIPA messages through the real Flask /message handlers using
test clients, so it exercises the agent code without needing to start network
servers manually.
"""

import sys

import Catalogador
import CentroLogistico
import Client
import EmpresaVendedora
import EntidadBancaria
import Tesorero
import Transportista
import Valorador
import Ventas
from AgentCommunication import (
    ACL,
    DSO,
    ECSDI,
    availability_from_response,
    build_directory_search_response,
    build_feedback_response,
    build_line_request,
    build_purchase_request,
    build_user_purchases_request,
    build_week_purchases_request,
    get_message_properties,
    parse_graph,
    products_from_product_info_response,
    purchase_result_total,
    purchases_from_response,
    response_ok,
    response_text,
    serialize_graph,
)


ADDRESS_BOOK = {
    'CATALOGADOR': ['catalogador://test'],
    'CENTRO_LOGISTICO': ['logistico://test'],
    'CLIENTE': ['client://test'],
    'EMPRESA_VENDEDORA': ['empresa-vendedora://test'],
    'ENTIDAD_BANCARIA': ['banco://test'],
    'TESORERO': ['tesorero://test'],
    'TRANSPORTISTA': ['transportista://test'],
    'VALORADOR': ['valorador://test'],
    'VENTAS': ['ventas://test'],
}


def call_agent(app, graph):
    response = app.test_client().get('/message', query_string={'message': serialize_graph(graph)})
    parsed = parse_graph(response.data.decode())
    return parsed


def fake_send_graph_message(address, graph, timeout=None):
    if address == 'directory://test':
        props = get_message_properties(graph)
        content = props['content']
        agent_type = str(graph.value(content, DSO.AgentType) or '')
        return build_directory_search_response(
            ADDRESS_BOOK.get(agent_type, []),
            sender='DirectoryService',
            receiver=str(props.get('sender') or 'test'),
            agent_type=agent_type,
            conversation_id=str(props.get('conversation_id') or '')
        )

    if address == 'catalogador://test':
        return call_agent(Catalogador.app, graph)
    if address == 'client://test':
        return call_agent(Client.app, graph)
    if address == 'empresa-vendedora://test':
        return call_agent(EmpresaVendedora.app, graph)
    if address == 'banco://test':
        return call_agent(EntidadBancaria.app, graph)
    if address == 'logistico://test':
        return call_agent(CentroLogistico.app, graph)
    if address == 'tesorero://test':
        return call_agent(Tesorero.app, graph)
    if address == 'transportista://test':
        return call_agent(Transportista.app, graph)
    if address == 'valorador://test':
        return call_agent(Valorador.app, graph)
    if address == 'ventas://test':
        return call_agent(Ventas.app, graph)

    raise RuntimeError(f'Unknown fake address: {address}')


def patch_network():
    for module in (Catalogador, CentroLogistico, Client, EmpresaVendedora, EntidadBancaria, Tesorero, Transportista, Valorador, Ventas):
        module.send_graph_message = fake_send_graph_message
        module.diraddress = 'directory://test'

    Ventas.query_directory_service = lambda agent_type, all_agents=False: (
        ADDRESS_BOOK.get(agent_type, []),
        None if ADDRESS_BOOK.get(agent_type) else 'NOT FOUND'
    )


def reset_state():
    CentroLogistico.STOCK = {
        'Auriculares Inalambricos SoundGo': 4,
        'Teclado Mecanico K85': 3,
        'Mouse Ergonomico MX Lite': 2,
        'Monitor 27 IPS 2K': 1,
        'Bombillas LED Pack 6': 5,
    }
    CentroLogistico.LOTES_PENDIENTES.clear()
    Client.client_notifications.clear()
    Client.notification_counter = 0
    Tesorero.CLIENTES.clear()
    Tesorero.PROVEEDORES.clear()
    Tesorero.PAGOS_EN_CURSO.clear()
    Tesorero.REGISTRO_PAGOS.clear()
    EntidadBancaria.TRANSFERENCIAS.clear()
    EntidadBancaria.FAILURE_RATE = 0.0
    EmpresaVendedora.SELLER_NAME = 'HomePlus'
    EmpresaVendedora.SELLER_IBAN = 'IBAN-HOMEPLUS-TEST'
    EmpresaVendedora.PRODUCTS = [
        {
            'id': 'EXT-HOME-TEST',
            'name': 'Robot Aspirador HomePlus Delegado',
            'brand': 'HomePlus',
            'seller': 'HomePlus',
            'provider': 'HomePlus',
            'external': True,
            'warehouse_managed': False,
            'price': 189.9,
            'tags': ['hogar', 'externo'],
        }
    ]
    EmpresaVendedora.VENTAS_EXTERNAS.clear()
    EmpresaVendedora.REGISTRATION_RESULTS.clear()
    Transportista.TRANSPORT_NAME = 'CheapMove Test'
    Transportista.BASE_PRICE = 18.0
    Transportista.PRICE_FACTOR = 1.0
    Transportista.MIN_PRICE = 12.0
    Transportista.CONCESSION_STEP = 2.0
    Transportista.NEGOTIATIONS.clear()
    Transportista.ACCEPTED.clear()
    Ventas.compras.clear()
    Ventas.compras_finalizadas.clear()
    Ventas.devoluciones.clear()
    Valorador.FEEDBACK_REQUESTED.clear()
    Valorador.RECOMMENDATIONS_SENT.clear()


def assert_ok(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f'OK - {message}')


def products_by_name(products):
    return {product['name']: product for product in products}


def test_catalog_product_kinds():
    request = build_line_request(
        ECSDI.PeticionInfoProductosComprados,
        {
            'Auriculares Inalambricos SoundGo': 1,
            'Teclado Mecanico K85': 1,
            'Cafetera Espresso Compacta': 1,
        },
        sender='test',
        receiver='CATALOGADOR',
        performative=ACL['query-ref'],
        message_name='conjunto-productos-comprados'
    )
    response = call_agent(Catalogador.app, request)
    assert_ok(response_ok(response), 'Catalogador responds to product-info query-ref')
    products = products_by_name(products_from_product_info_response(response))

    assert_ok(products['Auriculares Inalambricos SoundGo']['external'] is False, 'internal product is owned by ECSDI')
    assert_ok(products['Auriculares Inalambricos SoundGo']['warehouse_managed'] is True, 'internal product is warehouse-managed')
    assert_ok(products['Teclado Mecanico K85']['external'] is True, 'hybrid product requires external provider payment')
    assert_ok(products['Teclado Mecanico K85']['warehouse_managed'] is True, 'hybrid product is shipped by our logistics')
    assert_ok(products['Cafetera Espresso Compacta']['external'] is True, 'fully external product requires external provider payment')
    assert_ok(products['Cafetera Espresso Compacta']['warehouse_managed'] is False, 'fully external product is not queried in logistics')


def test_logistics_stock_query():
    request = build_line_request(
        ECSDI.PeticionExisteLineaComanda,
        {
            'Auriculares Inalambricos SoundGo': 1,
            'Teclado Mecanico K85': 1,
            'Cafetera Espresso Compacta': 1,
        },
        sender='test',
        receiver='CENTRO_LOGISTICO',
        performative=ACL['query-if'],
        message_name='existe-producto'
    )
    response = call_agent(CentroLogistico.app, request)
    availability = availability_from_response(response)
    assert_ok(availability['Auriculares Inalambricos SoundGo'] is True, 'logistics confirms internal product stock')
    assert_ok(availability['Teclado Mecanico K85'] is True, 'logistics confirms hybrid product stock')
    assert_ok(availability['Cafetera Espresso Compacta'] is False, 'logistics does not stock fully external product')


def test_purchase_requires_user_billing_data():
    request = build_purchase_request(
        {'Auriculares Inalambricos SoundGo': 1},
        delivery_address='Carrer de la Prova 1',
        sender='CLI-NO-IBAN',
        receiver='VENTAS',
        client_id='CLI-NO-IBAN'
    )
    response = call_agent(Ventas.app, request)
    assert_ok(not response_ok(response), 'Ventas rejects purchases without client IBAN')
    assert_ok('BANCARIOS' in response_text(response), 'Ventas explains missing billing data')


def test_mixed_purchase_flow():
    request = build_purchase_request(
        {
            'Auriculares Inalambricos SoundGo': 1,
            'Teclado Mecanico K85': 1,
            'Cafetera Espresso Compacta': 1,
        },
        delivery_address='Carrer de la Prova 1',
        sender='CLI-TEST',
        receiver='VENTAS',
        client_id='CLI-TEST',
        client_iban='IBAN-CLI-TEST'
    )
    response = call_agent(Ventas.app, request)
    assert_ok(response_ok(response), 'Ventas processes mixed internal/hybrid/fully-external purchase')
    assert_ok(purchase_result_total(response) > 0.0, 'Ventas returns invoice total to the user')
    assert_ok(Tesorero.CLIENTES['CLI-TEST']['iban'] == 'IBAN-CLI-TEST', 'Tesorero stores client IBAN from user purchase')
    assert_ok(Tesorero.CLIENTES['CLI-TEST']['address'] == 'Carrer de la Prova 1', 'Tesorero stores client delivery address')
    assert_ok(CentroLogistico.STOCK['Auriculares Inalambricos SoundGo'] == 3, 'internal product stock is decremented')
    assert_ok(CentroLogistico.STOCK['Teclado Mecanico K85'] == 2, 'hybrid product stock is decremented')
    assert_ok('Cafetera Espresso Compacta' not in CentroLogistico.STOCK, 'fully external product never enters logistics stock')
    assert_ok(EmpresaVendedora.VENTAS_EXTERNAS, 'EmpresaVendedora receives delegated fully external sale')

    payment_kinds = [payment['kind'] for payment in Tesorero.REGISTRO_PAGOS]
    assert_ok(payment_kinds.count('lote') == 0, 'warehouse lot charge is not requested before transport assignment')
    assert_ok(payment_kinds.count('cli') >= 1, 'Tesorero records delegated external-sale client charge')
    assert_ok(payment_kinds.count('ext') == 1, 'Tesorero pays fully external provider before logistics timer')
    bank_kinds = [transfer['kind'] for transfer in EntidadBancaria.TRANSFERENCIAS]
    assert_ok(bank_kinds.count('cli') >= 1 and bank_kinds.count('ext') >= 1, 'EntidadBancaria confirms external sale transfers')
    assert_ok(Ventas.compras_finalizadas, 'Ventas keeps completed purchase history')

    tick_response = CentroLogistico.app.test_client().get('/tick/envios')
    assert_ok(tick_response.status_code == 200, 'Logistics shipping timer can be accelerated manually')
    payment_kinds = [payment['kind'] for payment in Tesorero.REGISTRO_PAGOS]
    assert_ok(payment_kinds.count('lote') >= 1, 'Tesorero records warehouse lot charge after transport assignment')
    assert_ok(payment_kinds.count('ext') >= 2, 'Tesorero records provider payments for hybrid and fully external products')
    assert_ok(Transportista.NEGOTIATIONS, 'Transportista participates in budget and counter-offer negotiation')
    assert_ok(Transportista.ACCEPTED, 'CentroLogistico accepts the selected transport offer')
    assert_ok(
        any(notification['kind'] == 'envio' for notification in Client.client_notifications),
        'Client receives shipping data after lot timer'
    )
    assert_ok(
        any((notification.get('data') or {}).get('transportista') == 'CheapMove Test'
            for notification in Client.client_notifications if notification['kind'] == 'envio'),
        'Client shipping data includes selected transportista'
    )


def test_history_and_feedback_queries():
    history_request = build_user_purchases_request('CLI-TEST', sender='valorador', receiver='VENTAS')
    history_response = call_agent(Ventas.app, history_request)
    history = purchases_from_response(history_response)
    assert_ok(bool(history), 'Valorador can query user purchase history via query-ref')

    week_request = build_week_purchases_request(sender='valorador', receiver='VENTAS')
    week_response = call_agent(Ventas.app, week_request)
    week_history = purchases_from_response(week_response)
    assert_ok(bool(week_history), 'Valorador can query one-week feedback candidates via query-ref')

    feedback = build_feedback_response('P1001', 5.0, client_id='CLI-TEST', sender='cliente', receiver='VALORADOR')
    feedback_response = call_agent(Valorador.app, feedback)
    assert_ok(response_ok(feedback_response), 'Valorador stores customer feedback')


def test_proactive_timer_notifications():
    feedback_tick = Valorador.app.test_client().get('/tick/feedback')
    assert_ok(feedback_tick.status_code == 200, 'Feedback timer can be accelerated manually')
    assert_ok(
        any(notification['kind'] == 'feedback' for notification in Client.client_notifications),
        'Client receives proactive feedback request'
    )

    recommendation_tick = Valorador.app.test_client().get('/tick/recomendaciones')
    assert_ok(recommendation_tick.status_code == 200, 'Recommendation timer can be accelerated manually')
    assert_ok(
        any(notification['kind'] == 'recomendacion' for notification in Client.client_notifications),
        'Client receives proactive recommendations'
    )


def test_external_product_registration():
    EmpresaVendedora.SELLER_NAME = 'ActionWorld'
    EmpresaVendedora.SELLER_IBAN = 'IBAN-ACTIONWORLD'
    EmpresaVendedora.PRODUCTS = [
        {
            'id': 'EXT-TEST-01',
            'name': 'Camara Accion Delegada',
            'brand': 'ActionCam',
            'seller': 'ActionWorld',
            'provider': 'ActionWorld',
            'external': True,
            'warehouse_managed': False,
            'price': 149.9,
            'tags': ['video', 'deporte'],
        }
    ]
    response = EmpresaVendedora.app.test_client().get('/tick/nuevo-producto')
    assert_ok(response.status_code == 200, 'EmpresaVendedora can proactively register a configured product')
    assert_ok(any(p['id'] == 'EXT-TEST-01' for p in Catalogador.catalog), 'registered external product is in catalog')
    assert_ok('ActionWorld' in Tesorero.PROVEEDORES, 'Catalogador forwards provider banking data to Tesorero')


def main():
    patch_network()
    reset_state()
    test_catalog_product_kinds()
    test_logistics_stock_query()
    test_purchase_requires_user_billing_data()
    test_mixed_purchase_flow()
    test_history_and_feedback_queries()
    test_proactive_timer_notifications()
    test_external_product_registration()
    print('ALL PROTOCOL TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
