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
import requests
from flask import Flask, request
from requests import ConnectionError
from multiprocessing import Process
from collections import Counter
import logging

__author__ = 'bejar'

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'logistico'


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


@app.route("/message")
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    mess = request.args['message']
    log(f'Received: {mess}')

    if '|' not in mess:
        log(f'Invalid message (no |): {mess}')
        return 'ERROR: INVALID MESSAGE'
    else:
        # Sintaxis de los mensajes "TIPO|PARAMETROS"
        messtype, messparam = mess.split('|')

        if messtype not in ['PRODUCTOS_A_COMPRAR', 'BUY']:
            log(f'Unknown request: {messtype}')
            return 'ERROR: INVALID REQUEST'

        if messtype == 'PRODUCTOS_A_COMPRAR':
            log(f'PRODUCTOS_A_COMPRAR: {messparam}')
            # extract conjunto de productos y cantidades
            productos_cantidades = messparam.split(',')
            productos_cantidades = {productos_cantidades[i]: int(productos_cantidades[i+1]) for i in range(0, len(productos_cantidades), 2)}
            log(f'Products requested: {productos_cantidades}')
            return 'OK'

        elif messtype == 'BUY':
            log(f'BUY: {messparam}')
            # TODO: implement buy logic
            return 'OK'
            

@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


def solver(saddress, probid, prob):
    """
    Hace la resolucion de un problema

    :param param:
    :return:
    """
    try:
        res = ''.join([x for x, _ in Counter(prob).most_common(10)])
    except Exception:
        res = 'ERROR: NON ASCII CHARACTERS'
    requests.get(saddress + '/message', params={'message': f'SOLVED|{probid},{res}'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', help="Define si el servidor esta abierto al exterior o no", action='store_true',
                        default=False)
    parser.add_argument('--verbose', help="Genera un log de la comunicacion del servidor web", action='store_true',
                        default=False)
    parser.add_argument('--port', type=int, help="Puerto de comunicacion del agente")
    parser.add_argument('--dir', default=None, help="Direccion del servicio de directorio")

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
        hostaddr = gethostname()
    else:
        hostaddr = hostname = socket.gethostname()

    log_prefix = f'logistico-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    solverid = hostaddr.split('.')[0] + '-' + str(port)
    mess = f'REGISTER|{solverid},CENTRO_LOGISTICO,{solveradd}'

    done = False
    while not done:
        try:
            resp = requests.get(diraddress + '/message', params={'message': mess}).text
            done = True
        except ConnectionError:
            pass

    if 'OK' in resp:
        log(f'{solverid} successfully registered')
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{solverid} unregistering')
        mess = f'UNREGISTER|{solverid}'
        requests.get(diraddress + '/message', params={'message': mess})
    else:
        log('Unable to register')
