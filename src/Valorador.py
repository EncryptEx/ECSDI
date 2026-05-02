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
import json
import logging
import socket

import requests
from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

__author__ = 'bejar'

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


def parse_product_ids(payload):
    if isinstance(payload, dict):
        if 'product_ids' in payload and isinstance(payload['product_ids'], list):
            return [str(pid).strip() for pid in payload['product_ids'] if str(pid).strip()]
        if 'product_id' in payload and str(payload['product_id']).strip():
            return [str(payload['product_id']).strip()]
        return []

    if isinstance(payload, list):
        return [str(pid).strip() for pid in payload if str(pid).strip()]

    return []


def get_ratings(product_ids):
    return {pid: float(RATINGS.get(pid, 3.5)) for pid in product_ids}


@app.route('/message')
def message():
    mess = request.args['message']

    if '|' not in mess:
        log(f'Invalid message (no |): {mess}')
        return 'ERROR: INVALID MESSAGE'

    messtype, messparam = mess.split('|', 1)

    if messtype not in ['OBTENER_VALORACIONES']:
        log(f'Unknown request type: {messtype}')
        return 'ERROR: INVALID REQUEST'

    if messtype == 'OBTENER_VALORACIONES':
        try:
            payload = json.loads(messparam)
            product_ids = parse_product_ids(payload)
            if not product_ids:
                return 'ERROR: INVALID PRODUCT IDS'

            ratings = get_ratings(product_ids)
            log(f'OBTENER_VALORACIONES product_ids={product_ids} -> {len(ratings)} ratings')
            return 'OK: ' + json.dumps(ratings)
        except Exception as exc:
            log(f'OBTENER_VALORACIONES failed: {exc}')
            return 'ERROR: INVALID PAYLOAD'


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
    mess = f'REGISTER|{agentid},VALORADOR,{agentadd}'

    done = False
    while not done:
        try:
            resp = requests.get(diraddress + '/message', params={'message': mess}).text
            done = True
        except ConnectionError:
            pass

    if 'OK' in resp:
        log(f'{agentid} successfully registered')
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        mess = f'UNREGISTER|{agentid}'
        requests.get(diraddress + '/message', params={'message': mess})
    else:
        log('Unable to register')
