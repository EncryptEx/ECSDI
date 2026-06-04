"""
External transport company agent.

The agent negotiates shipping prices with CentroLogistico. It is configurable
so the demo can show cheap/expensive transporters with different concession
behaviour.
"""
import argparse
import json
import logging
import socket

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_shipping_offer_response,
    build_status_response,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    response_ok,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
    shipping_request_from_content,
)
from RuntimeInfo import render_runtime_info, rows_from_mapping, rows_from_sequence, table_section


app = Flask(__name__)

log_prefix = 'transportista'
diraddress = ''

TRANSPORT_NAME = 'Transportista Demo'
BASE_PRICE = 20.0
PRICE_FACTOR = 1.0
MIN_PRICE = 14.0
CONCESSION_STEP = 2.0
NEGOTIATIONS = {}
ACCEPTED = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def load_profile(path, profile):
    if not path:
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    transportistas = data.get('transportistas', [])
    if profile:
        for item in transportistas:
            if item.get('profile') == profile or item.get('name') == profile:
                return item
    return transportistas[0] if transportistas else {}


def purchase_id(shipping_request):
    purchase = shipping_request.get('purchase') or {}
    return purchase.get('id') or 'sin-compra'


def item_count(shipping_request):
    purchase = shipping_request.get('purchase') or {}
    return sum(int(item.get('quantity', 1)) for item in purchase.get('items') or []) or 1


def initial_offer(shipping_request):
    weight = float(shipping_request.get('weight') or 1.0)
    units = item_count(shipping_request)
    return round(max(MIN_PRICE, (BASE_PRICE + units * 1.5 + weight * 0.75) * PRICE_FACTOR), 2)


def next_offer(pid, requested_offer):
    state = NEGOTIATIONS.setdefault(pid, {'last_offer': None, 'rounds': 0})
    last_offer = state['last_offer']
    if last_offer is None:
        last_offer = initial_offer({'purchase': {'items': []}, 'weight': 1.0})

    state['rounds'] += 1
    if requested_offer is not None and requested_offer >= MIN_PRICE and requested_offer >= last_offer - CONCESSION_STEP:
        state['last_offer'] = round(float(requested_offer), 2)
        return state['last_offer'], True

    lowered = round(max(MIN_PRICE, last_offer - CONCESSION_STEP), 2)
    state['last_offer'] = lowered
    return lowered, False


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

    if has_type(graph, content, ECSDI.PeticionPresupuestoEnvioLote):
        shipping_request = shipping_request_from_content(graph, content)
        pid = purchase_id(shipping_request)
        price = initial_offer(shipping_request)
        NEGOTIATIONS[pid] = {'last_offer': price, 'rounds': 0}
        log(f'Oferta inicial compra={pid} precio={price:.2f}')
        response = build_shipping_offer_response(
            price,
            TRANSPORT_NAME,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id,
            text='OFERTA'
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionContraOfertaLote):
        shipping_request = shipping_request_from_content(graph, content)
        pid = purchase_id(shipping_request)
        requested = shipping_request.get('counter_offer')
        price, accepted = next_offer(pid, requested)
        text = 'ACEPTADA' if accepted else 'CONTRAOFERTA'
        log(f'Contraoferta compra={pid} solicitada={requested:.2f} respuesta={price:.2f} {text}')
        response = build_shipping_offer_response(
            price,
            TRANSPORT_NAME,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id,
            accepted=accepted,
            final=not accepted,
            text=text
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionAceptarOfertaLote):
        shipping_request = shipping_request_from_content(graph, content)
        pid = purchase_id(shipping_request)
        offer = shipping_request.get('counter_offer') or 0.0
        ACCEPTED.append({'purchase_id': pid, 'price': offer, 'transportista': TRANSPORT_NAME})
        log(f'Oferta aceptada compra={pid} precio={offer:.2f}')
        response = build_shipping_offer_response(
            offer,
            TRANSPORT_NAME,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id,
            accepted=True,
            final=True,
            text='ACEPTADA'
        )
        return serialize_graph(response)

    response = build_status_response(log_prefix, sender, ok=False, text='INVALID TRANSPORT REQUEST',
                                     conversation_id=conversation_id)
    return serialize_graph(response)


@app.route('/stop')
def stop():
    log('Stopping server')
    shutdown_server()
    return 'Parando Servidor'


@app.route('/info')
def info():
    config_rows = [{
        'transportista': TRANSPORT_NAME,
        'base_price': BASE_PRICE,
        'price_factor': PRICE_FACTOR,
        'min_price': MIN_PRICE,
        'concession_step': CONCESSION_STEP,
    }]
    stats = [
        {'label': 'Transportista', 'value': TRANSPORT_NAME},
        {'label': 'Regateos', 'value': len(NEGOTIATIONS)},
        {'label': 'Envios aceptados', 'value': len(ACCEPTED)},
    ]
    sections = [
        table_section('Configuracion de precios', config_rows),
        table_section('Regateos por compra', rows_from_mapping(NEGOTIATIONS, id_key='purchase_id'), empty='No hay regateos registrados'),
        table_section('Envios aceptados', rows_from_sequence(ACCEPTED), empty='No hay envios aceptados'),
    ]
    return render_runtime_info('Transportista', log_prefix, stats=stats, sections=sections)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true', default=False)
    parser.add_argument('--verbose', action='store_true', default=False)
    parser.add_argument('--port', type=int, default=9071)
    parser.add_argument('--dir', default=None)
    parser.add_argument('--hostaddr', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--profile', default=None)
    parser.add_argument('--name', default=None)
    parser.add_argument('--base-price', type=float, default=None)
    parser.add_argument('--price-factor', type=float, default=None)
    parser.add_argument('--min-price', type=float, default=None)
    parser.add_argument('--concession-step', type=float, default=None)
    args = parser.parse_args()

    if not args.verbose:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)

    cfg = load_profile(args.config, args.profile)
    TRANSPORT_NAME = args.name or cfg.get('name', TRANSPORT_NAME)
    BASE_PRICE = float(args.base_price if args.base_price is not None else cfg.get('base_price', BASE_PRICE))
    PRICE_FACTOR = float(args.price_factor if args.price_factor is not None else cfg.get('price_factor', PRICE_FACTOR))
    MIN_PRICE = float(args.min_price if args.min_price is not None else cfg.get('min_price', MIN_PRICE))
    CONCESSION_STEP = float(args.concession_step if args.concession_step is not None else cfg.get('concession_step', CONCESSION_STEP))

    hostname = '0.0.0.0' if args.open else socket.gethostname()
    hostaddr = args.hostaddr if args.hostaddr else (gethostname() if args.open else hostname)
    log_prefix = f'transportista-{args.port}'

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    diraddress = args.dir

    agentadd = f'http://{hostaddr}:{args.port}'
    agentid = f'{TRANSPORT_NAME.replace(" ", "_")}-{args.port}'
    mess = build_directory_register(agentid, 'TRANSPORTISTA', agentadd, sender=agentid)

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
