"""
.. module:: Logger

Logger
*************

:Description: Logger

    Registra y genera una grafica de los problemas resueltos y quien los ha resuelto

:Authors: bejar
    

:Version: 

:Created on: 06/02/2018 8:21 

"""

from Util import gethostname
import socket
import argparse
from FlaskServer import shutdown_server
from requests import ConnectionError
from flask import Flask, request, render_template, jsonify
import time
import logging
from rdflib import RDF

__author__ = 'bejar'

from AgentCommunication import (
    build_directory_register,
    build_directory_unregister,
    build_status_response,
    get_message_properties,
    message_conversation,
    message_sender,
    parse_graph,
    response_ok,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

workers_logging = {}
message_log = []
log_counter = 0


@app.route("/message")
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    global workers_logging

    try:
        graph = parse_graph(request.args['message'])
        props = get_message_properties(graph)
        sender = message_sender(props)
        conversation_id = message_conversation(props)
        content = props['content']
        content_types = [str(t).split('#')[-1] for t in graph.objects(content, RDF.type)]
        prob = content_types[0] if content_types else 'Comunicacion'
    except Exception:
        response = build_status_response('LOGGER', 'unknown', ok=False, text='INVALID RDF/FIPA MESSAGE')
        return serialize_graph(response)

    if sender in workers_logging:
        workers_logging[sender][prob] = workers_logging[sender].get(prob, 0) + 1
    else:
        workers_logging[sender] = {prob: 1}

    response = build_status_response('LOGGER', sender, ok=True, text='LOGGED', conversation_id=conversation_id)
    return serialize_graph(response)


@app.route('/trace')
def trace():
    """Lightweight packet trace endpoint - called by send_graph_message on each agent."""
    global log_counter
    from_agent = request.args.get('from', 'unknown')
    to_agent = request.args.get('to', 'unknown')
    msg_type = request.args.get('type', 'Message')
    performative = request.args.get('performative', 'inform')
    conv_id = request.args.get('conversation_id', '')
    msg_name = request.args.get('msg_name', '')
    phase = request.args.get('phase', '')
    ts_raw = request.args.get('ts', None)
    try:
        ts = float(ts_raw) if ts_raw else time.time()
    except (ValueError, TypeError):
        ts = time.time()

    log_counter += 1
    message_log.append({
        'id': log_counter,
        'ts': ts,
        'ts_fmt': time.strftime('%H:%M:%S', time.localtime(ts)),
        'from': from_agent,
        'to': to_agent,
        'type': msg_type,
        'msg_name': msg_name,
        'phase': phase,
        'performative': performative,
        'conversation_id': conv_id,
    })
    return 'OK'


@app.route('/api/messages')
def api_messages():
    """Return the full trace log as JSON for the frontend."""
    ordered = sorted(message_log, key=lambda item: (item.get('ts', 0), item.get('id', 0)))
    return jsonify({'messages': ordered})


@app.route('/info')
def info():
    """
    Entrada que da informacion sobre el agente a traves de una pagina web
    """
    return render_template('logview.html')


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
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
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

    # Configuration stuff
    if args.port is None:
        port = 9100
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    print('DS Hostname =', hostaddr)

    # Registramos el solver aritmetico en el servicio de directorio
    loggeradd = f'http://{hostaddr}:{port}'
    loggerid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(loggerid, 'LOGGER', loggeradd, sender=loggerid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            print
            pass

    if response_ok(resp):
        print(f'LOGGER successfully registered')
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        mess = build_directory_unregister(loggerid, sender=loggerid)
        send_graph_message(diraddress, mess)
    else:
        print('Unable to register')
        
        # todo all terminal logs must go to the logger as well.
        # todo implement a node-based graph and see packets flying between agents in real time, with the possibility to click on them and see the content of the messages.
        
