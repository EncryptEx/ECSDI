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
import json
from FlaskServer import shutdown_server
import requests
from flask import Flask, request
from requests import ConnectionError
from multiprocessing import Process
from Util import gethostname
import logging
import socket

__author__ = 'bejar'

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
    mess = request.args['message']

    if '|' not in mess:
        log(f'Invalid message (no |): {mess}')
        return 'ERROR: INVALID MESSAGE'
    else:
        # Sintaxis de los mensajes "TIPO|PARAMETROS"
        messtype, messparam = mess.split('|')
        log(f'Received {messtype}')

        if messtype not in ['PRODUCTOS_A_COMPRAR']:
            log(f'Unknown request type: {messtype}')
            return 'ERROR: INVALID REQUEST'
        else:
            if messtype == 'PRODUCTOS_A_COMPRAR':
                products_to_buy = json.loads(messparam)
                log(f'Processing PRODUCTOS_A_COMPRAR: {products_to_buy}')

                centros_logisticos = []
                response = query_directory_service('QUERY|LOGISTIC')
                if response != 'ERROR: CONNECTION ERROR':
                    centros_logisticos = json.loads(response)
                log(f'Logistics centers available: {centros_logisticos}')

                for centro in centros_logisticos:
                    response = query_directory_service(f'QUERY|{centro}')
                    if response != 'ERROR: CONNECTION ERROR':
                        productos_disponibles = json.loads(response)
                        log(f'Center {centro} has: {productos_disponibles}')

                        productos_a_comprar = {}
                        for producto in list(products_to_buy.keys()):
                            if producto in productos_disponibles:
                                productos_a_comprar[producto] = products_to_buy[producto]
                                del products_to_buy[producto]
                        if len(productos_a_comprar) > 0:
                            log(f'Buying from {centro}: {productos_a_comprar}')
                            response = query_directory_service(f'BUY|{centro},{json.dumps(productos_a_comprar)}')
                            if response != 'ERROR: CONNECTION ERROR':
                                log(f'Purchase from {centro} OK')
                            else:
                                log(f'Purchase from {centro} FAILED: connection error')
                        else:
                            log(f'Center {centro} has none of the required products, skipping')
                    else:
                        log(f'Center {centro} unreachable')
                if len(products_to_buy) > 0:
                    log(f'Purchase incomplete, remaining: {products_to_buy}')
                else:
                    log('All products purchased successfully')
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
        res = eval(prob)
    except Exception:
        res = 'ERROR: SYNTAX ERROR'

    requests.get(saddress + '/message', params={'message': f'SOLVED|{probid},{res}'})

def query_directory_service(mess):
    """
    Función auxiliar para enviar mensajes al servicio de directorio

    :param mess: mensaje a enviar
    :return: respuesta del servicio de directorio
    """
    try:
        resp = requests.get(diraddress + '/message', params={'message': mess}).text
        return resp
    except ConnectionError:
        return 'ERROR: CONNECTION ERROR'

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
        port = 9020
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = gethostname()
    else:
        hostaddr = hostname = socket.gethostname()

    log_prefix = f'ventas-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    solverid = hostaddr.split('.')[0] + '-' + str(port)
    mess = f'REGISTER|{solverid},VENTAS,{solveradd}'

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
