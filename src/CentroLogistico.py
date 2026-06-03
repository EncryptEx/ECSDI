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

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_unregister,
    build_existence_response,
    build_purchase_result,
    build_status_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_line_request,
    response_ok,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'logistico'
STOCK = {
    'Auriculares Inalambricos SoundGo': 6,
    'Mouse Ergonomico MX Lite': 8,
    'Bombillas LED Pack 6': 10,
}
LOTES_PENDIENTES = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


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
        })
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
