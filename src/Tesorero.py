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
    build_completed_purchase_request,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
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
    send_graph_message,
    serialize_graph,
    transfer_from_request,
)


app = Flask(__name__)

log_prefix = 'tesorero'
diraddress = ''

CLIENTES = {}
PROVEEDORES = {}
PAGOS_EN_CURSO = {}
REGISTRO_PAGOS = []


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


def process_transfer(graph, content):
    transfer = transfer_from_request(graph, content)
    payment_id = str(uuid4())
    payment = {
        'id': payment_id,
        'kind': transfer['kind'],
        'amount': transfer['amount'],
        'participant': transfer['participant'],
        'provider': transfer['provider'],
        'iban': transfer['iban'],
        'purchase': transfer['purchase'],
        'status': 'confirmed'
    }
    PAGOS_EN_CURSO[payment_id] = payment
    REGISTRO_PAGOS.append(payment)
    log(
        f'Transferencia {payment_id} tipo={payment["kind"]} '
        f'importe={payment["amount"]:.2f} confirmada'
    )

    if payment['kind'] in ('cli', 'lote'):
        notify_completed_purchase(transfer)

    return True, 'TRANSFERENCIA CONFIRMADA'


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
    agentid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(agentid, 'TESORERO', agentadd, sender=agentid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{agentid} successfully registered')
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        mess = build_directory_unregister(agentid, sender=agentid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
