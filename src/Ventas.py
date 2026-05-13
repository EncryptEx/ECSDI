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

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    availability_from_response,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_line_request,
    build_purchase_result,
    build_status_response,
    delivery_address_from_purchase,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_line_request,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'ventas'


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

    if props['performative'] != ACL.request or not has_type(graph, content, ECSDI.PeticionCompra):
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

    delivery_address = delivery_address_from_purchase(graph, content)
    log(f'Processing PeticionCompra: {products_to_buy}')
    if delivery_address:
        log(f'Delivery address: {delivery_address}')

    centros_logisticos, error = query_directory_service('CENTRO_LOGISTICO', all_agents=True)
    if error:
        log('No logistics centers found in directory service')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='NO LOGISTICS CENTERS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    log(f'Logistics centers available: {centros_logisticos}')
    for centro_addr in centros_logisticos:
        if not products_to_buy:
            break

        try:
            exists_request = build_line_request(
                ECSDI.PeticionExisteLineaComanda,
                products_to_buy,
                sender=log_prefix,
                receiver='CENTRO_LOGISTICO'
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

        to_buy_here = {p: products_to_buy[p] for p, ok in availability.items() if ok and p in products_to_buy}
        if not to_buy_here:
            log(f'Center {centro_addr} cannot handle any remaining product, skipping')
            continue

        try:
            log(f'Attempting to buy from {centro_addr}: {to_buy_here}')
            buy_request = build_line_request(
                ECSDI.PeticionGuardarCompra,
                to_buy_here,
                sender=log_prefix,
                receiver='CENTRO_LOGISTICO'
            )
            buy_resp = send_graph_message(centro_addr, buy_request)
            if response_ok(buy_resp):
                log(f'Bought from {centro_addr}: {to_buy_here}')
                for product in to_buy_here:
                    del products_to_buy[product]
            else:
                log(f'BUY from {centro_addr} failed: {response_text(buy_resp, "ERROR")}')
        except ConnectionError:
            log(f'Center {centro_addr} unreachable during BUY')
        except Exception as exc:
            log(f'Center {centro_addr} returned invalid BUY response: {exc}')

    if products_to_buy:
        log(f'Purchase incomplete, remaining: {products_to_buy}')
        response = build_purchase_result(
            False,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
    else:
        log('All products purchased successfully')
        response = build_purchase_result(
            True,
            sender=log_prefix,
            receiver=sender,
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
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{solverid} unregistering')
        mess = build_directory_unregister(solverid, sender=solverid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
