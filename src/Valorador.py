"""
.. module:: Valorador

Valorador
*************

:Description: Valorador

 Agente que devuelve valoraciones de productos.

:Authors: Jaume

:Version:

:Created on: 02/05/2026

"""

import argparse
import logging
import socket

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_feedback_request,
    build_recommendation_notice,
    build_ratings_response,
    build_status_response,
    build_user_purchases_request,
    build_week_purchases_request,
    directory_addresses_from_response,
    feedback_from_request,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    product_ids_from_ratings_request,
    purchases_from_response,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
)

app = Flask(__name__)

log_prefix = 'valorador'
diraddress = ''
FEEDBACK_REQUESTED = set()
RECOMMENDATIONS_SENT = set()


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


RATINGS = {
    'P1001': 4.3,
    'P1002': 4.7,
    'P1003': 4.1,
    'P1004': 4.6,
    'P1005': 4.4,
    'P1006': 4.0,
    'P1007': 4.2,
    'P1008': 4.8
}

RATING_EVENTS = {
    product_id: [rating]
    for product_id, rating in RATINGS.items()
}


def get_ratings(product_ids):
    return {pid: float(RATINGS.get(pid, 3.5)) for pid in product_ids}


def directory_addresses(agent_type, all_agents=False):
    if not diraddress:
        return [], 'DIRECTORY NOT CONFIGURED'
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except ConnectionError:
        return [], 'CONNECTION ERROR'
    except Exception:
        return [], 'INVALID DIRECTORY RESPONSE'

    if not response_ok(response):
        return [], response_text(response, 'NOT FOUND')
    addresses = directory_addresses_from_response(response)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None


def query_ventas_purchases(week_only=False):
    addresses, error = directory_addresses('VENTAS')
    if error:
        return [], error
    request_graph = (
        build_week_purchases_request(sender=log_prefix, receiver='VENTAS')
        if week_only
        else build_user_purchases_request('', sender=log_prefix, receiver='VENTAS')
    )
    try:
        response = send_graph_message(addresses[0], request_graph)
    except ConnectionError:
        return [], 'VENTAS CONNECTION ERROR'
    except Exception:
        return [], 'VENTAS INVALID RESPONSE'
    if not response_ok(response):
        return [], response_text(response, 'VENTAS ERROR')
    return purchases_from_response(response), None


def send_to_clients(graph):
    addresses, error = directory_addresses('CLIENTE', all_agents=True)
    if error:
        log(f'CLIENTE not found: {error}')
        return 0
    sent = 0
    for address in addresses:
        try:
            response = send_graph_message(address, graph)
            if response_ok(response):
                sent += 1
        except Exception as exc:
            log(f'CLIENTE proactive message failed at {address}: {exc}')
    return sent


def recommendations_for_purchase(purchase):
    purchased_ids = {
        str(item.get('id') or '').strip()
        for item in purchase.get('items') or []
        if item.get('id')
    }
    candidates = [
        {'id': product_id, 'name': f'Producto recomendado {product_id}', 'rating': rating}
        for product_id, rating in sorted(RATINGS.items(), key=lambda item: item[1], reverse=True)
        if product_id not in purchased_ids
    ]
    return candidates[:3]


def save_feedback(graph, content):
    feedback = feedback_from_request(graph, content)
    product_id = feedback['product_id']
    rating = float(feedback['rating'])
    if not product_id:
        return False, 'PRODUCTO INVALIDO'
    if rating < 0.0 or rating > 5.0:
        return False, 'VALORACION INVALIDA'
    values = RATING_EVENTS.setdefault(product_id, [])
    values.append(rating)
    RATINGS[product_id] = sum(values) / len(values)
    log(f'Feedback recibido producto={product_id} rating={rating:.2f}')
    return True, 'FEEDBACK GUARDADO'


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

    allowed_performatives = {ACL.request, ACL['query-ref'], ACL.inform}
    if props['performative'] not in allowed_performatives:
        log('Unknown request type')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.RespuestaFeedbackCliente):
        ok, text = save_feedback(graph, content)
        response = build_status_response(
            log_prefix,
            sender,
            ok=ok,
            text=text,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not has_type(graph, content, ECSDI.PeticionValoracionesProducto):
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
        product_ids = product_ids_from_ratings_request(graph, content)
        if not product_ids:
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text='INVALID PRODUCT IDS',
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        ratings = get_ratings(product_ids)
        log(f'PeticionValoracionesProducto product_ids={product_ids} -> {len(ratings)} ratings')
        response = build_ratings_response(
            ratings,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)
    except Exception as exc:
        log(f'PeticionValoracionesProducto failed: {exc}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PAYLOAD',
            conversation_id=conversation_id
        )
        return serialize_graph(response)


@app.route('/tick/feedback')
def tick_feedback():
    purchases, error = query_ventas_purchases(week_only=True)
    if error:
        text = f'FEEDBACK TIMER ERROR: {error}'
        log(text)
        return text

    sent = 0
    for purchase in purchases:
        items = purchase.get('items') or []
        if not items:
            continue
        item = items[0]
        product_key = item.get('id') or item.get('name') or ''
        key = (purchase.get('id', ''), product_key)
        if key in FEEDBACK_REQUESTED:
            continue
        graph = build_feedback_request(purchase, item, sender=log_prefix, receiver='CLIENTE')
        delivered = send_to_clients(graph)
        if delivered:
            FEEDBACK_REQUESTED.add(key)
            sent += delivered

    text = f'FEEDBACK REQUESTS={sent}'
    log(text)
    return text


@app.route('/tick/recomendaciones')
def tick_recomendaciones():
    purchases, error = query_ventas_purchases(week_only=False)
    if error:
        text = f'RECOMENDACION TIMER ERROR: {error}'
        log(text)
        return text

    sent = 0
    by_client = {}
    for purchase in purchases:
        client_id = purchase.get('client_id') or ''
        if client_id:
            by_client.setdefault(client_id, []).append(purchase)

    for client_id, client_purchases in by_client.items():
        if client_id in RECOMMENDATIONS_SENT:
            continue
        recommendations = recommendations_for_purchase(client_purchases[-1])
        if not recommendations:
            continue
        graph = build_recommendation_notice(
            client_id,
            recommendations,
            sender=log_prefix,
            receiver='CLIENTE',
            message='Recomendaciones generadas por el temporizador de recomendaciones'
        )
        delivered = send_to_clients(graph)
        if delivered:
            RECOMMENDATIONS_SENT.add(client_id)
            sent += delivered

    text = f'RECOMENDACIONES={sent}'
    log(text)
    return text


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
        port = 9050
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'valorador-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    agentadd = f'http://{hostaddr}:{port}'
    agentid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(agentid, 'VALORADOR', agentadd, sender=agentid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{agentid} successfully registered')
        # Try to connect to Logger for packet tracing
        try:
            _lr = send_graph_message(diraddress, build_directory_search('LOGGER', sender=agentid))
            if response_ok(_lr):
                _la = directory_addresses_from_response(_lr)
                if _la:
                    set_tracer_url(_la[0])
                    log(f'Packet tracing enabled → {_la[0]}')
        except Exception:
            pass
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        mess = build_directory_unregister(agentid, sender=agentid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
