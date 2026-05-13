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
    build_directory_unregister,
    build_ratings_response,
    build_status_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    product_ids_from_ratings_request,
    response_ok,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

log_prefix = 'valorador'


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


def get_ratings(product_ids):
    return {pid: float(RATINGS.get(pid, 3.5)) for pid in product_ids}


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

    if props['performative'] != ACL.request or not has_type(graph, content, ECSDI.PeticionValoracionesProducto):
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
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        mess = build_directory_unregister(agentid, sender=agentid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
