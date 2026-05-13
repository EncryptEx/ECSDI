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

from io import BytesIO
from Util import gethostname
import socket
import argparse
from FlaskServer import shutdown_server
from requests import ConnectionError
from flask import Flask, request, render_template
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
import numpy as np
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


@app.route('/info')
def info():
    """
    Entrada que da informacion sobre el agente a traves de una pagina web
    """
    global workers_logging

    types = set()
    solvers = workers_logging.keys()
    for solv in workers_logging:
        for tp in workers_logging[solv]:
            types.add(tp)

    lbars = []
    for t in types:
        bar = []
        for solv in workers_logging:
            if t in workers_logging[solv]:
                bar.append(workers_logging[solv][t])
            else:
                bar.append(0)
        lbars.append(bar)

    img = BytesIO()
    index = np.arange(len(solvers))
    bar_width = 0.35
    fig = plt.figure(figsize=(5, 8), dpi=100)
    for i, data, type in zip(range(len(lbars)), lbars, types):
        plt.barh(index + (i * bar_width), data, bar_width, alpha=0.4, label=type)

    plt.ylabel('Solver')
    plt.xlabel('Num probs')
    plt.title(f"Resuelto desde {time.strftime('%Y-%m-%d %H:%M')}")
    ids = [f'Solver-{i + 1}' for i in range(len(solvers))]
    plt.yticks(index + bar_width / 2, ids)
    plt.legend()

    plt.tight_layout()
    plt.savefig(img, format='png')
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    return render_template('logview.html', plot_url=plot_url)


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
        
