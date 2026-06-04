"""
.. module:: Ventas

Ventas
*************

:Description: Ventas

 Este agente se encarga de gestionar las ventas de los productos. 

:Authors: Jaume
    

:Version: 

:Created on: 06/02/2018 8:21 

"""
import argparse
from FlaskServer import shutdown_server
from flask import Flask, request
from requests import ConnectionError
from Util import gethostname
import logging
import socket
from uuid import uuid4

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    availability_from_response,
    build_client_data_request,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_external_sale_request,
    build_line_request,
    build_lot_assignment_request,
    build_product_info_request,
    build_purchase_result,
    build_purchases_response,
    build_status_response,
    build_transfer_request,
    completed_purchase_from_request,
    delivery_address_from_purchase,
    directory_addresses_from_response,
    first_float,
    first_literal,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_product_info_response,
    products_from_line_request,
    purchase_from_content,
    response_ok,
    response_text,
    requested_client_id,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
)

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'ventas'
compras = {}
compras_finalizadas = {}
devoluciones = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def purchase_total(purchase):
    total = 0.0
    for item in purchase.get('items') or []:
        quantity = int(item.get('quantity', 1))
        price = item.get('line_price', item.get('price', 0.0))
        total += float(price or 0.0) * quantity
    return total


def product_name(item):
    return str(item.get('name') or item.get('id') or '').strip()


def is_external_product(item):
    if item.get('external') is not None:
        return bool(item.get('external'))
    seller = str(item.get('seller') or item.get('provider') or '').strip().lower()
    return bool(seller and seller != 'ecsdi store')


def is_warehouse_managed_product(item):
    if item.get('warehouse_managed') is not None:
        return bool(item.get('warehouse_managed'))
    return not is_external_product(item)


def item_identity(item):
    return (
        str(item.get('id') or '').strip().lower(),
        str(item.get('name') or '').strip().lower(),
        str(item.get('provider') or item.get('seller') or '').strip().lower(),
    )


def merge_purchase(existing, incoming):
    merged = dict(existing)
    for key in ('id', 'client_id', 'client_iban', 'delivery_address'):
        if incoming.get(key):
            merged[key] = incoming[key]

    items = [dict(item) for item in existing.get('items') or []]
    seen = {item_identity(item) for item in items}
    for item in incoming.get('items') or []:
        identity = item_identity(item)
        if identity not in seen:
            items.append(dict(item))
            seen.add(identity)
    merged['items'] = items
    return merged


def store_completed_purchase(purchase):
    purchase_id = purchase.get('id') or str(uuid4())
    purchase['id'] = purchase_id
    existing = compras_finalizadas.get(purchase_id)
    compras_finalizadas[purchase_id] = merge_purchase(existing, purchase) if existing else purchase
    compras.pop(purchase_id, None)
    log(f'Compra finalizada guardada: {purchase_id}')
    return purchase_id


def fallback_product_info(products):
    return [
        {
            'name': product,
            'quantity': quantity,
            'price': 0.0,
            'seller': 'ECSDI Store',
            'provider': 'ECSDI Store',
            'external': False,
            'warehouse_managed': True,
        }
        for product, quantity in products.items()
    ]


def fetch_product_info(products):
    addresses, error = query_directory_service('CATALOGADOR')
    if error:
        log(f'CATALOGADOR not found, using raw purchase lines: {error}')
        return fallback_product_info(products)

    try:
        response = send_graph_message(
            addresses[0],
            build_product_info_request(products, sender=log_prefix, receiver='CATALOGADOR')
        )
    except Exception as exc:
        log(f'CATALOGADOR product info failed, using raw purchase lines: {exc}')
        return fallback_product_info(products)

    if not response_ok(response):
        log(f'CATALOGADOR refused product info: {response_text(response, "ERROR")}')
        return fallback_product_info(products)

    products_info = products_from_product_info_response(response)
    found = {product_name(product).lower(): product for product in products_info}
    for name, quantity in products.items():
        key = str(name).lower()
        if key not in found:
            products_info.append(fallback_product_info({name: quantity})[0])
        else:
            found[key]['quantity'] = quantity
    return products_info


def send_to_tesorero(graph):
    addresses, error = query_directory_service('TESORERO')
    if error:
        log(f'TESORERO not found: {error}')
        return False
    try:
        response = send_graph_message(addresses[0], graph)
        return response_ok(response)
    except Exception as exc:
        log(f'TESORERO communication failed: {exc}')
        return False


def register_client_in_tesorero(client_id, client_iban, delivery_address):
    graph = build_client_data_request(
        client_id,
        client_iban,
        delivery_address,
        sender=log_prefix,
        receiver='TESORERO'
    )
    return send_to_tesorero(graph)


def request_client_charge(purchase):
    total = purchase_total(purchase)
    if total <= 0:
        return False
    graph = build_transfer_request(
        'cli',
        total,
        sender=log_prefix,
        receiver='TESORERO',
        participant=purchase.get('client_id', ''),
        purchase=purchase,
        iban=purchase.get('client_iban', ''),
        message_name='quiero cobrar al usuario'
    )
    return send_to_tesorero(graph)


def request_external_provider_payments(purchase):
    ok = True
    for item in purchase.get('items') or []:
        if not is_external_product(item):
            continue
        amount = float(item.get('price', item.get('line_price', 0.0)) or 0.0) * int(item.get('quantity', 1))
        provider = item.get('provider') or item.get('seller') or ''
        graph = build_transfer_request(
            'ext',
            amount,
            sender=log_prefix,
            receiver='TESORERO',
            participant=provider,
            provider=provider,
            purchase={'id': purchase.get('id'), 'client_id': purchase.get('client_id'), 'items': [item]},
            message_name='pagar-esta-cantidad'
        )
        ok = send_to_tesorero(graph) and ok
    return ok


def group_external_items_by_provider(items):
    grouped = {}
    for item in items:
        provider = item.get('provider') or item.get('seller') or ''
        grouped.setdefault(provider, []).append(item)
    return grouped


def notify_external_seller(purchase):
    addresses, error = query_directory_service('EMPRESA_VENDEDORA', all_agents=True)
    if error:
        log(f'EMPRESA_VENDEDORA not found: {error}')
        return False

    ok = True
    grouped_items = group_external_items_by_provider(purchase.get('items') or [])
    for provider, items in grouped_items.items():
        provider_purchase = dict(purchase)
        provider_purchase['items'] = items
        delivered = False
        for address in addresses:
            try:
                response = send_graph_message(
                    address,
                    build_external_sale_request(
                        provider,
                        provider_purchase,
                        sender=log_prefix,
                        receiver='EMPRESA_VENDEDORA'
                    )
                )
            except Exception as exc:
                log(f'EMPRESA_VENDEDORA communication failed at {address}: {exc}')
                continue
            if response_ok(response):
                delivered = True
                break
        if not delivered:
            log(f'No external seller accepted delegated sale for provider={provider}')
        ok = delivered and ok
    return ok


def purchases_for_client(client_id):
    if not client_id:
        return list(compras_finalizadas.values())
    return [
        purchase for purchase in compras_finalizadas.values()
        if str(purchase.get('client_id', '')) == str(client_id)
    ]


@app.route("/message")
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    try:
        graph = parse_graph(request.args['message'])
        props = get_message_properties(graph)
        sender = message_sender(props)
        conversation_id = message_conversation(props)
        content = props['content']
    except Exception as exc:
        log(f'Invalid RDF/FIPA message: {exc}')
        response = build_status_response(log_prefix, 'unknown', ok=False, text='INVALID RDF/FIPA MESSAGE')
        return serialize_graph(response)

    if props['performative'] == ACL['query-ref']:
        if has_type(graph, content, ECSDI.PeticionProductosCompradosUsuario):
            client_id = requested_client_id(graph, content)
            purchases = purchases_for_client(client_id)
            log(f'pedir-productos-comprados-por-usuario client={client_id or "*"} -> {len(purchases)}')
            response = build_purchases_response(
                purchases,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        if has_type(graph, content, ECSDI.PeticionComprasSemana):
            purchases = list(compras_finalizadas.values())
            log(f'pedir-compras-que-ya-han-pasado-una-semana -> {len(purchases)}')
            response = build_purchases_response(
                purchases,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id,
                content_type=ECSDI.ResultadoComprasSemana,
                message_name='envio-compras-correspondientes'
            )
            return serialize_graph(response)

        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID QUERY',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if props['performative'] != ACL.request:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionGuardarCompraRealizada):
        purchase = completed_purchase_from_request(graph, content)
        store_completed_purchase(purchase)
        response = build_status_response(
            log_prefix,
            sender,
            ok=True,
            text='COMPRA HISTORICA GUARDADA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionDevolucion) or has_type(graph, content, ECSDI.PeticionDevolverDinero) or has_type(graph, content, ECSDI['PeticionDevolverDineroACliente']):
        purchase_id = first_literal(graph, content, ECSDI.idCompra, '')
        purchase = compras_finalizadas.get(purchase_id)
        if not purchase:
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text='COMPRA NO ENCONTRADA',
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        amount = first_float(graph, content, ECSDI.cantidadTransferencia, purchase_total(purchase))
        devoluciones.append({'purchase_id': purchase_id, 'amount': amount})
        send_to_tesorero(build_transfer_request(
            'dev',
            amount,
            sender=log_prefix,
            receiver='TESORERO',
            participant=purchase.get('client_id', ''),
            purchase=purchase,
            iban=purchase.get('client_iban', ''),
            message_name='quiero-devolver'
        ))
        response = build_status_response(
            log_prefix,
            sender,
            ok=True,
            text='DEVOLUCION PROCESADA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not has_type(graph, content, ECSDI.PeticionCompra):
        log('Unknown request type')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID REQUEST',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    try:
        products_to_buy = {
            str(prod): int(qty)
            for prod, qty in products_from_line_request(graph, content).items()
            if int(qty) > 0
        }
    except Exception:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PRODUCTS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not products_to_buy:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PRODUCTS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    incoming_purchase = purchase_from_content(graph, content)
    delivery_address = incoming_purchase.get('delivery_address') or delivery_address_from_purchase(graph, content)
    client_id = incoming_purchase.get('client_id') or sender
    client_iban = incoming_purchase.get('client_iban') or ''
    purchase_id = incoming_purchase.get('id') or str(uuid4())

    if not delivery_address:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='DIRECCION DE ENTREGA OBLIGATORIA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not client_iban:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='DATOS BANCARIOS CLIENTE OBLIGATORIOS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    product_info = fetch_product_info(products_to_buy)
    product_by_name = {product_name(product).lower(): product for product in product_info}
    warehouse_products = {
        product_name(product): int(product.get('quantity', products_to_buy.get(product_name(product), 1)))
        for product in product_info
        if product_name(product) and is_warehouse_managed_product(product)
    }
    fully_external_items = [
        dict(product, quantity=int(product.get('quantity', products_to_buy.get(product_name(product), 1))))
        for product in product_info
        if product_name(product) and not is_warehouse_managed_product(product)
    ]
    assigned_items = []

    log(f'Processing PeticionCompra: {products_to_buy}')
    if delivery_address:
        log(f'Delivery address: {delivery_address}')

    register_client_in_tesorero(client_id, client_iban, delivery_address)

    centros_logisticos, error = query_directory_service('CENTRO_LOGISTICO', all_agents=True)
    if error and warehouse_products:
        log('No logistics centers found in directory service')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='NO LOGISTICS CENTERS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    remaining_warehouse = dict(warehouse_products)
    log(f'Logistics centers available: {centros_logisticos}')
    for centro_addr in centros_logisticos:
        if not remaining_warehouse:
            break

        try:
            exists_request = build_line_request(
                ECSDI.PeticionExisteLineaComanda,
                remaining_warehouse,
                sender=log_prefix,
                receiver='CENTRO_LOGISTICO',
                performative=ACL['query-if'],
                message_name='existe-producto'
            )
            resp = send_graph_message(centro_addr, exists_request)
        except ConnectionError:
            log(f'Center {centro_addr} unreachable')
            continue
        except Exception as exc:
            log(f'Center {centro_addr} returned invalid existence response: {exc}')
            continue

        if not response_ok(resp):
            log(f'Center {centro_addr} refused existence request: {response_text(resp, "ERROR")}')
            continue

        availability = availability_from_response(resp)
        log(f'Center {centro_addr} availability: {availability}')

        to_buy_here = {p: remaining_warehouse[p] for p, ok in availability.items() if ok and p in remaining_warehouse}
        if not to_buy_here:
            log(f'Center {centro_addr} cannot handle any remaining product, skipping')
            continue

        try:
            log(f'Assigning lot in {centro_addr}: {to_buy_here}')
            assignment_payload = {}
            for product, quantity in to_buy_here.items():
                item = dict(product_by_name.get(product.lower(), {'name': product, 'price': 0.0}))
                item['quantity'] = quantity
                assignment_payload[product] = item
            buy_request = build_lot_assignment_request(
                assignment_payload,
                sender=log_prefix,
                receiver='CENTRO_LOGISTICO',
                delivery_address=delivery_address,
                purchase_id=purchase_id,
                client_id=client_id
            )
            buy_resp = send_graph_message(centro_addr, buy_request)
            if response_ok(buy_resp):
                log(f'Lot assigned in {centro_addr}: {to_buy_here}')
                for product, quantity in to_buy_here.items():
                    item = dict(assignment_payload[product])
                    item['quantity'] = quantity
                    assigned_items.append(item)
                    del remaining_warehouse[product]
            else:
                log(f'Lot assignment from {centro_addr} failed: {response_text(buy_resp, "ERROR")}')
        except ConnectionError:
            log(f'Center {centro_addr} unreachable during lot assignment')
        except Exception as exc:
            log(f'Center {centro_addr} returned invalid lot assignment response: {exc}')

    if remaining_warehouse:
        log(f'Purchase incomplete, remaining warehouse-managed products: {remaining_warehouse}')
        response = build_purchase_result(
            False,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
    else:
        purchase = {
            'id': purchase_id,
            'client_id': client_id,
            'client_iban': client_iban,
            'delivery_address': delivery_address,
            'items': assigned_items + fully_external_items
        }
        compras[purchase_id] = purchase

        if fully_external_items:
            external_purchase = {
                'id': purchase_id,
                'client_id': client_id,
                'client_iban': client_iban,
                'delivery_address': delivery_address,
                'items': fully_external_items
            }
            if not notify_external_seller(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='EMPRESA VENDEDORA EXTERNA NO DISPONIBLE',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)
            if not request_external_provider_payments(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='PAGO A VENDEDOR EXTERNO NO CONFIRMADO',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)
            if not request_client_charge(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='COBRO AL CLIENTE NO CONFIRMADO',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)

        store_completed_purchase(purchase)
        total = purchase_total(purchase)
        log(f'All products purchased successfully; total={total:.2f}')
        response = build_purchase_result(
            True,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id,
            total=total
        )
    return serialize_graph(response)


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


def query_directory_service(agent_type, all_agents=False):
    """
    Función auxiliar para enviar mensajes al servicio de directorio

    :param agent_type: tipo de agente a buscar
    :param all_agents: indica si se quieren todas las direcciones
    :return: par (direcciones, error)
    """
    try:
        resp = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except ConnectionError:
        return [], 'CONNECTION ERROR'
    except Exception:
        return [], 'INVALID RESPONSE'

    if not response_ok(resp):
        return [], response_text(resp, 'NOT FOUND')

    addresses = directory_addresses_from_response(resp)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', help="Define si el servidor esta abierto al exterior o no", action='store_true',
                        default=False)
    parser.add_argument('--verbose', help="Genera un log de la comunicacion del servidor web", action='store_true',
                        default=False)
    parser.add_argument('--port', type=int, help="Puerto de comunicacion del agente")
    parser.add_argument('--dir', default=None, help="Direccion del servicio de directorio")
    parser.add_argument('--hostaddr', default=None, help="Direccion del agente anunciada al exterior (sobreescribe la deteccion automatica)")

    # parsing de los parametros de la linea de comandos
    args = parser.parse_args()

    if not args.verbose:
        _wlog = logging.getLogger('werkzeug')
        _wlog.setLevel(logging.ERROR)

    # Configuration stuff
    if args.port is None:
        port = 9020
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'ventas-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    solverid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(solverid, 'VENTAS', solveradd, sender=solverid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{solverid} successfully registered')
        # Try to connect to Logger for packet tracing
        try:
            _lr = send_graph_message(diraddress, build_directory_search('LOGGER', sender=solverid))
            if response_ok(_lr):
                _la = directory_addresses_from_response(_lr)
                if _la:
                    set_tracer_url(_la[0])
                    log(f'Packet tracing enabled → {_la[0]}')
        except Exception:
            pass
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{solverid} unregistering')
        mess = build_directory_unregister(solverid, sender=solverid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
