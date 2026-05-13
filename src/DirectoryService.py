"""
.. module:: DirectoryService

DirectoryService
*************

:Description: DirectoryService

 Registra los agentes/servicios activos y reparte la carga de las busquedas mediante
 un round robin

:Authors: bejar
    

:Version: 

:Created on: 06/02/2018 8:20 

"""
from Util import gethostname
import socket
import argparse
from FlaskServer import shutdown_server

from flask import Flask, request, render_template
import numpy as np
import time
from random import randint
from uuid import uuid4
import logging
from rdflib import RDF

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    DIRECTORY_AGENT,
    DSO,
    build_directory_search_response,
    build_status_response,
    directory_register_values,
    directory_search_type,
    directory_unregister_id,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    serialize_graph,
)

def obscure(dir):
    """
    Hide real hostnames
    """
    odir = {}
    for d in dir:
        _,_,port = dir[d][1].split(':')
        odir[d] = (dir[d][0], f'{uuid4()}:{port}', dir[d][2])

    return odir

app = Flask(__name__)

directory = {}
loadbalance = {}
schedule = 'equaljobs'
log_prefix = 'directorio'


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


@app.route("/message")
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    global directory
    global loadbalance

    try:
        graph = parse_graph(request.args['message'])
        props = get_message_properties(graph)
        sender = message_sender(props)
        conversation_id = message_conversation(props)
        content = props['content']
    except Exception as exc:
        log(f'Invalid RDF/FIPA message: {exc}')
        response = build_status_response(
            DIRECTORY_AGENT,
            'unknown',
            ok=False,
            text='INVALID RDF/FIPA MESSAGE'
        )
        return serialize_graph(response)

    if props['performative'] != ACL.request:
        response = build_status_response(
            DIRECTORY_AGENT,
            sender,
            ok=False,
            text='DIRECTORY ONLY ACCEPTS REQUEST PERFORMATIVES',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, DSO.Register):
        serid, sertype, seraddress = directory_register_values(graph, content)
        if serid and sertype and seraddress:
            if serid not in directory:
                directory[serid] = (sertype, seraddress, time.strftime('%Y-%m-%d %H:%M'))
                loadbalance[serid] = 0
                log(f'REGISTER  {serid} type={sertype} @ {seraddress}')
                response = build_status_response(
                    DIRECTORY_AGENT,
                    sender,
                    ok=True,
                    text='REGISTER SUCCESS',
                    conversation_id=conversation_id
                )
            else:
                log(f'REGISTER FAILED: {serid} already registered')
                response = build_status_response(
                    DIRECTORY_AGENT,
                    sender,
                    ok=False,
                    text='ID ALREADY REGISTERED',
                    conversation_id=conversation_id
                )
        else:
            response = build_status_response(
                DIRECTORY_AGENT,
                sender,
                ok=False,
                text='REGISTER INVALID PARAMETERS',
                conversation_id=conversation_id
            )
        return serialize_graph(response)

    if has_type(graph, content, DSO.Search):
        sertype = directory_search_type(graph, content)
        found = [(id, directory[id][1]) for id in directory if directory[id][0] == sertype]
        if len(found) != 0:
            if schedule == 'equaljobs':
                # balanceo por igual numero de jobs
                bal = [loadbalance[id] for id, _ in found]
                pos = np.argmin(bal)
            elif schedule == 'random':
                pos = randint(0, len(found) - 1)
            else:
                pos = 0
            loadbalance[found[pos][0]] += 1
            log(f'SEARCH    {sertype} -> {found[pos][0]} @ {found[pos][1]}')
            response = build_directory_search_response(
                [found[pos][1]],
                DIRECTORY_AGENT,
                sender,
                agent_type=sertype,
                conversation_id=conversation_id
            )
        else:
            log(f'SEARCH    {sertype} -> NOT FOUND')
            response = build_status_response(
                DIRECTORY_AGENT,
                sender,
                ok=False,
                text='NOT FOUND',
                conversation_id=conversation_id
            )
        return serialize_graph(response)

    if has_type(graph, content, DSO.SearchAll):
        sertype = directory_search_type(graph, content)
        found = [directory[id][1] for id in directory if directory[id][0] == sertype]
        log(f'SEARCHALL {sertype} -> {len(found)} found')
        if found:
            response = build_directory_search_response(
                found,
                DIRECTORY_AGENT,
                sender,
                agent_type=sertype,
                conversation_id=conversation_id
            )
        else:
            response = build_status_response(
                DIRECTORY_AGENT,
                sender,
                ok=False,
                text='NOT FOUND',
                conversation_id=conversation_id
            )
        return serialize_graph(response)

    if has_type(graph, content, DSO.Unregister):
        serid = directory_unregister_id(graph, content)
        if serid in directory:
            log(f'UNREGISTER {serid}')
            del directory[serid]
            loadbalance.pop(serid, None)
            response = build_status_response(
                DIRECTORY_AGENT,
                sender,
                ok=True,
                text='UNREGISTER SUCCESS',
                conversation_id=conversation_id
            )
        else:
            log(f'UNREGISTER FAILED: {serid} not registered')
            response = build_status_response(
                DIRECTORY_AGENT,
                sender,
                ok=False,
                text='NOT REGISTERED',
                conversation_id=conversation_id
            )
        return serialize_graph(response)

    unknown_types = [str(t).split('#')[-1] for t in graph.objects(content, RDF.type)]
    log(f'Unknown directory action: {unknown_types}')
    response = build_status_response(
        DIRECTORY_AGENT,
        sender,
        ok=False,
        text='NO SUCH ACTION',
        conversation_id=conversation_id
    )
    return serialize_graph(response)


@app.route('/info')
def info():
    """
    Entrada que da informacion sobre el agente a traves de una pagina web
    """
    global directory
    global loadbalance

    return render_template('directory.html', dir=obscure(directory), bal=loadbalance)


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
    parser.add_argument('--schedule', default='random', choices=['equaljobs', 'random'],
                        help="Algoritmo de reparto de carga")
    parser.add_argument('--hostaddr', default=None, help="Direccion del agente anunciada al exterior (sobreescribe la deteccion automatica)")

    # parsing de los parametros de la linea de comandos
    args = parser.parse_args()

    if not args.verbose:
        _wlog = logging.getLogger('werkzeug')
        _wlog.setLevel(logging.ERROR)

    # Configuration stuff
    if args.port is None:
        port = 9000
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    schedule = args.schedule
    log_prefix = f'directorio-{port}'
    log(f'DS Hostname = {hostaddr}, schedule={schedule}')
    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port, debug=False, use_reloader=False)
