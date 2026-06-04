"""
.. module:: Tesorero

Tesorero
********

:Description: Financial agent for the ECSDI shop.

 Handles the PDTool protocols that centralize client billing, refunds,
 provider payments and banking data registration.
"""

import argparse
import logging
import socket
from uuid import uuid4

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

from AgentCommunication import (
    ACL,
    ECSDI,
    build_bank_transfer_request,
    build_completed_purchase_request,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_shipping_notice,
    build_status_response,
    client_data_from_request,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    provider_data_from_request,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
    transfer_from_request,
)
from RuntimeInfo import purchase_row, render_runtime_info, rows_from_mapping, table_section


app = Flask(__name__)

log_prefix = 'tesorero'
diraddress = ''

CLIENTES = {}
PROVEEDORES = {}
PAGOS_EN_CURSO = {}
REGISTRO_PAGOS = []
TREASURY_IBAN = 'IBAN-ECSDI-TESORERIA'


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def register_client(graph, content):
    data = client_data_from_request(graph, content)
    if not data['id']:
        return False, 'CLIENTE SIN ID'
    CLIENTES[data['id']] = data
    log(f'Registrado cliente {data["id"]}')
    return True, 'CLIENTE REGISTRADO'


def register_provider(graph, content):
    data = provider_data_from_request(graph, content)
    if not data['name']:
        return False, 'PROVEEDOR SIN NOMBRE'
    PROVEEDORES[data['name']] = data
    log(f'Registrado proveedor {data["name"]}')
    return True, 'PROVEEDOR REGISTRADO'


def notify_completed_purchase(transfer):
    purchase = transfer.get('purchase') or {}
    if not purchase.get('items'):
        return

    try:
        ventas_resp = send_graph_message(
            diraddress,
            build_directory_search('VENTAS', sender=log_prefix)
        )
    except Exception as exc:
        log(f'No se pudo buscar VENTAS para historico: {exc}')
        return

    if not response_ok(ventas_resp):
        log('VENTAS no encontrado para historico')
        return

    addresses = directory_addresses_from_response(ventas_resp)
    if not addresses:
        return

    try:
        send_graph_message(
            addresses[0],
            build_completed_purchase_request(purchase, sender=log_prefix, receiver='VENTAS')
        )
        log(f'Notificada compra finalizada {purchase.get("id", "")}')
    except Exception as exc:
        log(f'No se pudo notificar compra finalizada: {exc}')


def enrich_transfer_purchase_with_shipping(transfer):
    notice = transfer.get('shipping_notice') or {}
    if not notice:
        return
    purchase = transfer.get('purchase') or {}
    if notice.get('delivery_date'):
        purchase['delivery_date'] = notice['delivery_date']
    if notice.get('transportista'):
        purchase['transportista'] = notice['transportista']
    if notice.get('tracking_id'):
        purchase['tracking_id'] = notice['tracking_id']
    transfer['purchase'] = purchase


def notify_shipping_data_to_client(transfer):
    notice = transfer.get('shipping_notice') or {}
    if not notice:
        return 0

    try:
        client_resp = send_graph_message(
            diraddress,
            build_directory_search('CLIENTE', sender=log_prefix, all_agents=True)
        )
    except Exception as exc:
        log(f'No se pudo buscar CLIENTE para datos de envio: {exc}')
        return 0

    if not response_ok(client_resp):
        log('CLIENTE no encontrado para datos de envio')
        return 0

    addresses = directory_addresses_from_response(client_resp)
    sent = 0
    purchase = notice.get('purchase') or transfer.get('purchase') or {}
    for address in addresses:
        try:
            response = send_graph_message(
                address,
                build_shipping_notice(
                    purchase,
                    sender=log_prefix,
                    receiver='CLIENTE',
                    transportista=notice.get('transportista', ''),
                    delivery_date=notice.get('delivery_date', ''),
                    tracking_id=notice.get('tracking_id', ''),
                    message=notice.get('message', '')
                )
            )
            if response_ok(response):
                sent += 1
        except Exception as exc:
            log(f'No se pudo enviar datos de envio a {address}: {exc}')
    if sent:
        log(f'Datos de envio enviados por Tesorero compra={purchase.get("id", "")} clientes={sent}')
    return sent


def bank_address():
    if not diraddress:
        return None, 'DIRECTORY NOT CONFIGURED'
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search('ENTIDAD_BANCARIA', sender=log_prefix)
        )
    except Exception as exc:
        return None, str(exc)

    if not response_ok(response):
        return None, response_text(response, 'NOT FOUND')

    addresses = directory_addresses_from_response(response)
    if not addresses:
        return None, 'NOT FOUND'
    return addresses[0], None


def enrich_transfer_with_known_ibans(transfer):
    kind = transfer.get('kind', '')
    participant = transfer.get('participant', '')
    provider = transfer.get('provider') or participant

    if kind in ('cli', 'lote') and not transfer.get('iban'):
        client_data = CLIENTES.get(participant) or {}
        if client_data.get('iban'):
            transfer['iban'] = client_data['iban']
    if kind == 'ext' and not transfer.get('iban'):
        provider_data = PROVEEDORES.get(provider) or {}
        if provider_data.get('iban'):
            transfer['iban'] = provider_data['iban']

    if kind in ('cli', 'lote'):
        transfer['origin_iban'] = transfer.get('iban') or participant
        transfer['destination_iban'] = TREASURY_IBAN
    elif kind in ('ext', 'dev'):
        transfer['origin_iban'] = TREASURY_IBAN
        transfer['destination_iban'] = transfer.get('iban') or provider or participant
    return transfer


def request_bank_confirmation(transfer):
    address, error = bank_address()
    if error:
        log(f'ENTIDAD_BANCARIA no encontrada: {error}')
        return False, 'ENTIDAD BANCARIA NO ENCONTRADA'

    try:
        enrich_transfer_with_known_ibans(transfer)
        response = send_graph_message(
            address,
            build_bank_transfer_request(transfer, sender=log_prefix, receiver='ENTIDAD_BANCARIA')
        )
    except Exception as exc:
        log(f'Error solicitando transferencia bancaria: {exc}')
        return False, 'ERROR TRANSFERENCIA BANCARIA'

    if not response_ok(response):
        return False, response_text(response, 'TRANSFERENCIA RECHAZADA')
    return True, response_text(response, 'TRANSFERENCIA CONFIRMADA')


def process_transfer(graph, content):
    transfer = transfer_from_request(graph, content)
    if transfer['amount'] <= 0:
        return False, 'IMPORTE INVALIDO'

    ok, bank_text = request_bank_confirmation(transfer)
    if not ok:
        return False, bank_text

    payment_id = str(uuid4())
    payment = {
        'id': payment_id,
        'kind': transfer['kind'],
        'amount': transfer['amount'],
        'participant': transfer['participant'],
        'provider': transfer['provider'],
        'iban': transfer['iban'],
        'origin_iban': transfer.get('origin_iban', ''),
        'destination_iban': transfer.get('destination_iban', ''),
        'purchase': transfer['purchase'],
        'status': 'confirmed'
    }
    PAGOS_EN_CURSO[payment_id] = payment
    REGISTRO_PAGOS.append(payment)
    log(
        f'Transferencia {payment_id} tipo={payment["kind"]} '
        f'importe={payment["amount"]:.2f} confirmada por entidad bancaria'
    )

    if payment['kind'] in ('cli', 'lote'):
        if payment['kind'] == 'lote':
            enrich_transfer_purchase_with_shipping(transfer)
        notify_completed_purchase(transfer)
    if payment['kind'] == 'lote':
        notify_shipping_data_to_client(transfer)

    return True, bank_text


@app.route('/message')
def message():
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

    if props['performative'] != ACL.request:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionGuardarDatosFacturacionClientes):
        ok, text = register_client(graph, content)
        response = build_status_response(log_prefix, sender, ok=ok, text=text, conversation_id=conversation_id)
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionGuardarDatosBancariosProveedor) or has_type(graph, content, ECSDI['PeticionA\u00f1adirProveedor']):
        ok, text = register_provider(graph, content)
        response = build_status_response(log_prefix, sender, ok=ok, text=text, conversation_id=conversation_id)
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionSolicitarTransferencia):
        ok, text = process_transfer(graph, content)
        response = build_status_response(log_prefix, sender, ok=ok, text=text, conversation_id=conversation_id)
        return serialize_graph(response)

    response = build_status_response(
        log_prefix,
        sender,
        ok=False,
        text='INVALID REQUEST',
        conversation_id=conversation_id
    )
    return serialize_graph(response)


@app.route('/info')
def info():
    payment_rows = []
    for payment_id, payment in PAGOS_EN_CURSO.items():
        row = {
            'payment_id': payment_id,
            'kind': payment.get('kind', ''),
            'amount': payment.get('amount', ''),
            'participant': payment.get('participant', ''),
            'provider': payment.get('provider', ''),
            'iban': payment.get('iban', ''),
            'origin_iban': payment.get('origin_iban', ''),
            'destination_iban': payment.get('destination_iban', ''),
            'status': payment.get('status', ''),
        }
        purchase = payment.get('purchase') or {}
        row['purchase_id'] = purchase.get('id', '')
        payment_rows.append(row)

    stats = [
        {'label': 'Clientes', 'value': len(CLIENTES)},
        {'label': 'Proveedores', 'value': len(PROVEEDORES)},
        {'label': 'Pagos en curso', 'value': len(PAGOS_EN_CURSO)},
        {'label': 'Pagos historicos', 'value': len(REGISTRO_PAGOS)},
    ]
    completed_purchase_rows = [
        purchase_row(payment.get('purchase') or {})
        for payment in REGISTRO_PAGOS
        if (payment.get('purchase') or {}).get('id')
    ]
    sections = [
        table_section('Clientes y datos bancarios', rows_from_mapping(CLIENTES, id_key='client_id'), empty='No hay clientes registrados'),
        table_section('Proveedores y datos bancarios', rows_from_mapping(PROVEEDORES, id_key='provider'), empty='No hay proveedores registrados'),
        table_section('Pagos en curso / confirmados', payment_rows, empty='No hay pagos registrados'),
        table_section('Compras asociadas a pagos', completed_purchase_rows, empty='No hay compras asociadas'),
    ]
    return render_runtime_info('Tesorero', log_prefix, stats=stats, sections=sections)


@app.route('/stop')
def stop():
    log('Stopping server')
    shutdown_server()
    return 'Parando Servidor'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', help='Define si el servidor esta abierto al exterior o no', action='store_true',
                        default=False)
    parser.add_argument('--verbose', help='Genera un log de la comunicacion del servidor web', action='store_true',
                        default=False)
    parser.add_argument('--port', type=int, help='Puerto de comunicacion del agente')
    parser.add_argument('--dir', default=None, help='Direccion del servicio de directorio')
    parser.add_argument('--hostaddr', default=None,
                        help='Direccion del agente anunciada al exterior (sobreescribe la deteccion automatica)')

    args = parser.parse_args()

    if not args.verbose:
        _wlog = logging.getLogger('werkzeug')
        _wlog.setLevel(logging.ERROR)

    if args.port is None:
        port = 9060
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'tesorero-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    agentadd = f'http://{hostaddr}:{port}'
    mess = build_directory_register(log_prefix, 'TESORERO', agentadd, sender=log_prefix)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{log_prefix} successfully registered')
        # Try to connect to Logger for packet tracing
        try:
            _lr = send_graph_message(diraddress, build_directory_search('LOGGER', sender=log_prefix))
            if response_ok(_lr):
                _la = directory_addresses_from_response(_lr)
                if _la:
                    set_tracer_url(_la[0])
                    log(f'Packet tracing enabled → {_la[0]}')
        except Exception:
            pass
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{log_prefix} unregistering')
        mess = build_directory_unregister(log_prefix, sender=log_prefix)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
