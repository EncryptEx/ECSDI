"""
.. module:: CentroLogistico

WordCounter
*************

:Description: CentroLogistico

    Dummy agent that simulates a logistics center.
    De momento hace random de si los tiene o no;

:Authors: bejar
    

:Version: 

:Created on: 06/02/2018 15:58 

"""

from Util import gethostname
import socket
import argparse
from FlaskServer import shutdown_server
from flask import Flask, request
from requests import ConnectionError
import random
import logging
import os
from datetime import datetime, timedelta

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_existence_response,
    build_purchase_result,
    build_shipping_notice,
    build_status_response,
    build_transfer_request,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    line_items_from_content,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_line_request,
    purchase_from_content,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'logistico'
diraddress = ''
WAREHOUSE_MANAGED_PRODUCTS = [
    'Auriculares Inalambricos SoundGo',
    'Teclado Mecanico K85',
    'Mouse Ergonomico MX Lite',
    'Monitor 27 IPS 2K',
    'Bombillas LED Pack 6',
]


def build_initial_stock():
    seed = os.environ.get('ECSDI_STOCK_SEED')
    rng = random.Random(f'{seed}-{os.getpid()}') if seed else random.Random()
    stock = {}

    for product in WAREHOUSE_MANAGED_PRODUCTS:
        if rng.random() < 0.65:
            stock[product] = rng.randint(1, 8)

    if not stock:
        product = rng.choice(WAREHOUSE_MANAGED_PRODUCTS)
        stock[product] = rng.randint(1, 8)

    return stock


STOCK = build_initial_stock()
LOTES_PENDIENTES = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def item_total(item):
    quantity = int(item.get('quantity', 1))
    price = item.get('line_price', item.get('price', 0.0))
    return float(price or 0.0) * quantity


def is_external_item(item):
    if item.get('external') is not None:
        return bool(item.get('external'))
    seller = str(item.get('seller') or item.get('provider') or '').strip().lower()
    return bool(seller and seller != 'ecsdi store')


def send_to_tesorero(graph):
    if not diraddress:
        log('TESORERO not queried because directory address is not configured')
        return False
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search('TESORERO', sender=log_prefix)
        )
    except Exception as exc:
        log(f'TESORERO directory lookup failed: {exc}')
        return False

    if not response_ok(response):
        log(f'TESORERO not found: {response_text(response, "NOT FOUND")}')
        return False

    addresses = directory_addresses_from_response(response)
    if not addresses:
        log('TESORERO not found: empty address list')
        return False

    try:
        transfer_response = send_graph_message(addresses[0], graph)
        return response_ok(transfer_response)
    except Exception as exc:
        log(f'TESORERO communication failed: {exc}')
        return False


def directory_addresses(agent_type, all_agents=False):
    if not diraddress:
        return [], 'DIRECTORY NOT CONFIGURED'
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except Exception as exc:
        return [], str(exc)

    if not response_ok(response):
        return [], response_text(response, 'NOT FOUND')
    addresses = directory_addresses_from_response(response)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None


def notify_clients_shipping(purchase):
    addresses, error = directory_addresses('CLIENTE', all_agents=True)
    if error:
        log(f'CLIENTE not found for shipping notice: {error}')
        return 0

    delivery_date = (datetime.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
    tracking_id = f'{log_prefix}-{purchase.get("id", "compra")}'
    graph = build_shipping_notice(
        purchase,
        sender=log_prefix,
        receiver='CLIENTE',
        transportista=f'Transportista demo {log_prefix}',
        delivery_date=delivery_date,
        tracking_id=tracking_id,
        message='Datos de envio generados por el temporizador de lotes'
    )

    sent = 0
    for address in addresses:
        try:
            response = send_graph_message(address, graph)
            if response_ok(response):
                sent += 1
        except Exception as exc:
            log(f'CLIENTE shipping notice failed at {address}: {exc}')
    return sent


def notify_financials_for_assigned_lot(graph, content):
    purchase = purchase_from_content(graph, content)
    purchase['items'] = purchase.get('items') or line_items_from_content(graph, content)
    items = purchase.get('items') or []
    if not items:
        return

    total = sum(item_total(item) for item in items)
    if total > 0:
        send_to_tesorero(build_transfer_request(
            'lote',
            total,
            sender=log_prefix,
            receiver='TESORERO',
            participant=purchase.get('client_id', ''),
            purchase=purchase,
            message_name='cobrar-envios-lote'
        ))

    for item in items:
        if not is_external_item(item):
            continue
        amount = item_total(item)
        if amount <= 0:
            continue
        provider = item.get('provider') or item.get('seller') or ''
        provider_purchase = dict(purchase)
        provider_purchase['items'] = [item]
        send_to_tesorero(build_transfer_request(
            'ext',
            amount,
            sender=log_prefix,
            receiver='TESORERO',
            participant=provider,
            provider=provider,
            purchase=provider_purchase,
            message_name='pagar-valor-producto'
        ))


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

    allowed_performatives = {ACL.request, ACL['query-if']}
    if props['performative'] not in allowed_performatives:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionExisteLineaComanda):
        requested = products_from_line_request(graph, content)
        log(f'PeticionExisteLineaComanda query for: {requested}')
        availability = {
            product: STOCK.get(product, 0) >= int(quantity)
            for product, quantity in requested.items()
        }
        log(f'PeticionExisteLineaComanda response: {availability}')
        response = build_existence_response(
            availability,
            requested,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionAsignarLoteProducto) or has_type(graph, content, ECSDI.PeticionGuardarCompra):
        products = products_from_line_request(graph, content)
        log(f'PeticionAsignarLoteProducto: {products}')
        unavailable = [
            product for product, quantity in products.items()
            if STOCK.get(product, 0) < int(quantity)
        ]
        if unavailable:
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text=f'SIN STOCK: {", ".join(unavailable)}',
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        for product, quantity in products.items():
            STOCK[product] = STOCK.get(product, 0) - int(quantity)
        LOTES_PENDIENTES.append({
            'sender': sender,
            'products': dict(products),
            'purchase': purchase_from_content(graph, content),
            'shipping_sent': False,
        })
        notify_financials_for_assigned_lot(graph, content)
        response = build_purchase_result(
            True,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    log('Unknown request')
    response = build_status_response(
        log_prefix,
        sender,
        ok=False,
        text='INVALID REQUEST',
        conversation_id=conversation_id
    )
    return serialize_graph(response)
            

@app.route("/tick/envios")
def tick_envios():
    """
    Temporizador manual para la demo: procesa lotes pendientes y envia datos
    de envio al cliente.
    """
    processed = 0
    notified = 0
    for lot in LOTES_PENDIENTES:
        if lot.get('shipping_sent'):
            continue
        purchase = lot.get('purchase') or {'items': []}
        sent = notify_clients_shipping(purchase)
        if sent > 0:
            lot['shipping_sent'] = True
            processed += 1
            notified += sent
    text = f'ENVIOS PROCESADOS={processed} NOTIFICACIONES={notified}'
    log(text)
    return text


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


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
        port = 9030
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'logistico-{port}'
    log(f'DS Hostname = {hostaddr}')
    log(f'Initial stock = {STOCK}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    solverid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(solverid, 'CENTRO_LOGISTICO', solveradd, sender=solverid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{solverid} successfully registered')
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{solverid} unregistering')
        mess = build_directory_unregister(solverid, sender=solverid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
