"""
.. module:: Client

Client
*************

:Description: Client

    Cliente del resolvedor distribuido

:Authors: bejar
    

:Version: 

:Created on: 06/02/2018 8:21 

"""

from Util import gethostname
import argparse
import json
from FlaskServer import shutdown_server
import requests
from requests import ConnectionError
from flask import Flask, request, render_template, url_for, redirect
import logging
import socket

__author__ = 'bejar'

app = Flask(__name__)

problems = {}
probcounter = 0
clientid = ''
diraddress = ''
log_prefix = 'client'


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


@app.route("/message", methods=['GET', 'POST'])
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    global problems

    # if request.form.has_key('message'):
    if 'product[]' in request.form:
        products_list = request.form.getlist('product[]')
        quantities_list = request.form.getlist('quantity[]')
        log(f'Buy request: {list(zip(products_list, quantities_list))}')
        send_message(products_list, quantities_list)
        return redirect(url_for('.iface'))
    else:
        return 'OK'


@app.route('/info')
def info():
    """
    Entrada que da informacion sobre el agente a traves de una pagina web
    """
    global problems

    return render_template('clientinteractions.html', probs=problems)


@app.route('/iface')
def iface():
    """
    Interfaz con el cliente a traves de una pagina de web
    """
    return render_template('iface.html')


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


def send_message(products_list, quantities_list):
    """
    Envia una solicitud de compra al agente de ventas

    mensaje:

    PRODUCTOS_A_COMPRAR|{"product": quantity, ...}

    :param products_list: lista de nombres de productos
    :param quantities_list: lista de cantidades correspondientes
    :return:
    """
    global probcounter
    global clientid
    global diraddress
    global port
    global problems

    probid = f'{clientid}-{probcounter:03}'
    probcounter += 1

    products = {p: int(q) for p, q in zip(products_list, quantities_list)}
    log(f'New order {probid}: {products}')

    # Busca el agente de ventas en el servicio de directorio
    log('Searching for VENTAS in directory service')
    ventasaddr = requests.get(diraddress + '/message', params={'message': 'SEARCH|VENTAS'}).text
    # Agente de ventas encontrado
    if 'OK' in ventasaddr:
        ventasaddr = ventasaddr[4:]
        log(f'Found VENTAS at {ventasaddr}')

        problems[probid] = [products, 'PENDING']
        mess = f'PRODUCTOS_A_COMPRAR|{json.dumps(products)}'
        log(f'Sending to VENTAS: {mess}')
        try:
            resp = requests.get(ventasaddr + '/message', params={'message': mess}).text
            if 'ERROR' not in resp:
                problems[probid][1] = 'SENT'
                log(f'{probid} sent successfully')
            else:
                problems[probid][1] = 'FAILED VENTAS'
                log(f'{probid} VENTAS returned error: {resp}')
        except ConnectionError:
            problems[probid][1] = 'FAILED CONNECTION'
            log(f'{probid} connection error to VENTAS at {ventasaddr}')
    # Agente de ventas no encontrado
    else:
        problems[probid] = [products, 'FAILED DS']
        log(f'{probid} VENTAS not found in directory service')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', help="Define si el servidor esta abierto al exterior o no", action='store_true',
                        default=False)
    parser.add_argument('--verbose', help="Genera un log de la comunicacion del servidor web", action='store_true',
                        default=False)
    parser.add_argument('--port', default=None, type=int, help="Puerto de comunicacion del agente")
    parser.add_argument('--dir', default=None, help="Direccion del servicio de directorio")
    parser.add_argument('--hostaddr', default=None, help="Direccion del agente anunciada al exterior (sobreescribe la deteccion automatica)")

    # parsing de los parametros de la linea de comandos
    args = parser.parse_args()
    if not args.verbose:
        _wlog = logging.getLogger('werkzeug')
        _wlog.setLevel(logging.ERROR)

    # Configuration stuff
    if args.port is None:
        port = 9001
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'client-{port}'
    log(f'DS Hostname = {hostaddr}')

    clientadd = f'http://{hostaddr}:{port}'
    clientid = hostaddr.split('.')[0] + '-' + str(port)

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    log(f'Starting at {clientadd}, directory={diraddress}')
    # Ponemos en marcha el servidor Flask
    app.run(host=hostname, port=port, debug=False, use_reloader=False)
