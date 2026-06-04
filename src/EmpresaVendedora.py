"""
External seller company agent.

It can register configured products in Catalogador and receive delegated sales
from Ventas for fully external products.
"""
import argparse
import json
import logging
import socket

from rdflib import Literal

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

from AgentCommunication import (
    ACL,
    ECSDI,
    add_product,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_message_with_content,
    build_status_response,
    directory_addresses_from_response,
    external_sale_from_content,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
)


app = Flask(__name__)

log_prefix = 'empresa-vendedora'
diraddress = ''
SELLER_NAME = 'Empresa Externa Demo'
SELLER_IBAN = 'IBAN-EMPRESA-EXTERNA'
PRODUCTS = []
VENTAS_EXTERNAS = []
REGISTRATION_RESULTS = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def load_profile(path, profile):
    if not path:
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    sellers = data.get('empresas_vendedoras', [])
    if profile:
        for item in sellers:
            if item.get('profile') == profile or item.get('name') == profile:
                return item
    return sellers[0] if sellers else {}


def catalogador_address():
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search('CATALOGADOR', sender=log_prefix)
        )
    except Exception as exc:
        return None, str(exc)
    if not response_ok(response):
        return None, response_text(response, 'NOT FOUND')
    addresses = directory_addresses_from_response(response)
    if not addresses:
        return None, 'NOT FOUND'
    return addresses[0], None


def build_new_product_request(product_data):
    graph, content = build_message_with_content(
        ECSDI.PeticionNuevoProducto,
        performative=ACL.request,
        sender=log_prefix,
        receiver='CATALOGADOR',
        message_name='Recepcion nuevo producto'
    )
    product = dict(product_data)
    product.setdefault('seller', SELLER_NAME)
    product.setdefault('provider', SELLER_NAME)
    product.setdefault('external', True)
    product.setdefault('warehouse_managed', False)
    product_node = add_product(graph, product)
    graph.add((content, ECSDI.contiene_productos, product_node))
    graph.add((content, ECSDI.nombreProveedor, Literal(SELLER_NAME)))
    graph.add((content, ECSDI.numeroIBAN, Literal(SELLER_IBAN)))
    return graph


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
        response = build_status_response(log_prefix, sender, ok=False, text='INVALID PERFORMATIVE',
                                         conversation_id=conversation_id)
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionNuevaVentaExterna) or has_type(graph, content, ECSDI.PeticionDelegarVentaExterna):
        sale = external_sale_from_content(graph, content)
        provider = str(sale.get('provider') or '').strip()
        if provider and provider.lower() != SELLER_NAME.lower():
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text=f'VENDEDOR NO GESTIONA PROVEEDOR {provider}',
                conversation_id=conversation_id
            )
            return serialize_graph(response)
        VENTAS_EXTERNAS.append(sale)
        purchase = sale.get('purchase') or {}
        log(f'Venta externa recibida compra={purchase.get("id", "")} lineas={len(purchase.get("items") or [])}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=True,
            text='VENTA EXTERNA RECIBIDA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    response = build_status_response(log_prefix, sender, ok=False, text='INVALID SELLER REQUEST',
                                     conversation_id=conversation_id)
    return serialize_graph(response)


@app.route('/tick/nuevo-producto')
def tick_nuevo_producto():
    address, error = catalogador_address()
    if error:
        text = f'CATALOGADOR ERROR: {error}'
        log(text)
        return text

    sent = 0
    for product in PRODUCTS:
        try:
            response = send_graph_message(address, build_new_product_request(product))
            ok = response_ok(response)
            REGISTRATION_RESULTS.append({'product': product.get('id') or product.get('name'), 'ok': ok})
            if ok:
                sent += 1
        except Exception as exc:
            REGISTRATION_RESULTS.append({'product': product.get('id') or product.get('name'), 'ok': False, 'error': str(exc)})
    text = f'PRODUCTOS_EXTERNOS_REGISTRADOS={sent}'
    log(text)
    return text


@app.route('/stop')
def stop():
    log('Stopping server')
    shutdown_server()
    return 'Parando Servidor'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true', default=False)
    parser.add_argument('--verbose', action='store_true', default=False)
    parser.add_argument('--port', type=int, default=9090)
    parser.add_argument('--dir', default=None)
    parser.add_argument('--hostaddr', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--profile', default=None)
    parser.add_argument('--name', default=None)
    parser.add_argument('--iban', default=None)
    args = parser.parse_args()

    if not args.verbose:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)

    cfg = load_profile(args.config, args.profile)
    SELLER_NAME = args.name or cfg.get('name', SELLER_NAME)
    SELLER_IBAN = args.iban or cfg.get('iban', SELLER_IBAN)
    PRODUCTS = cfg.get('products', PRODUCTS)

    hostname = '0.0.0.0' if args.open else socket.gethostname()
    hostaddr = args.hostaddr if args.hostaddr else (gethostname() if args.open else hostname)
    log_prefix = f'empresa-vendedora-{args.port}'

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    diraddress = args.dir

    agentadd = f'http://{hostaddr}:{args.port}'
    agentid = f'{SELLER_NAME.replace(" ", "_")}-{args.port}'
    mess = build_directory_register(agentid, 'EMPRESA_VENDEDORA', agentadd, sender=agentid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{agentid} successfully registered')
        try:
            logger_resp = send_graph_message(diraddress, build_directory_search('LOGGER', sender=agentid))
            if response_ok(logger_resp):
                addresses = directory_addresses_from_response(logger_resp)
                if addresses:
                    set_tracer_url(addresses[0])
        except Exception:
            pass
        app.run(host=hostname, port=args.port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        send_graph_message(diraddress, build_directory_unregister(agentid, sender=agentid))
    else:
        log('Unable to register')
