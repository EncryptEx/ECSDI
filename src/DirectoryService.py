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
import json
from FlaskServer import shutdown_server

from flask import Flask, request, render_template
import numpy as np
import time
from random import randint
from uuid import uuid4
import logging

__author__ = 'bejar'

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

    mess = request.args['message']


    if '|' not in mess:
        return 'ERROR: INVALID MESSAGE'
    else:
        # Sintaxis de los mensajes "TIPO|PARAMETROS"
        messtype, messparam = mess.split('|')

        if messtype not in ['REGISTER', 'SEARCH', 'SEARCHALL', 'UNREGISTER']:
            return 'ERROR: NO SUCH ACTION'
        else:
            # parametros mensaje REGISTER = "ID,TIPO,ADDRESS"
            if messtype == 'REGISTER':
                param = messparam.split(',')
                if len(param) == 3:
                    serid, sertype, seraddress = param
                    if serid not in directory:
                        directory[serid] = (sertype, seraddress, time.strftime('%Y-%m-%d %H:%M'))
                        loadbalance[serid] = 0
                        log(f'REGISTER  {serid} type={sertype} @ {seraddress}')
                        return 'OK: REGISTER SUCCESS'
                    else:
                        log(f'REGISTER FAILED: {serid} already registered')
                        return 'ERROR: ID ALREADY REGISTERED'
                else:
                    return 'ERROR: REGISTER INVALID PARAMETERS'
            # parametros del mensaje SEARCH = 'TIPO'
            elif messtype == 'SEARCH':
                sertype = messparam
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
                    return 'OK: ' + found[pos][1]
                else:
                    log(f'SEARCH    {sertype} -> NOT FOUND')
                    return 'ERROR: NOT FOUND'
            # parametros del mensaje SEARCHALL = 'TIPO' -> devuelve todas las direcciones del tipo
            elif messtype == 'SEARCHALL':
                sertype = messparam
                found = [directory[id][1] for id in directory if directory[id][0] == sertype]
                log(f'SEARCHALL {sertype} -> {len(found)} found')
                if found:
                    return 'OK: ' + json.dumps(found)
                else:
                    return 'ERROR: NOT FOUND'
            # parametros del mensaje UNREGISTER = 'ID'
            elif messtype == 'UNREGISTER':
                serid = messparam
                if serid in directory:
                    log(f'UNREGISTER {serid}')
                    del directory[serid]
                    return 'OK: UNREGISTER SUCCESS'
                else:
                    log(f'UNREGISTER FAILED: {serid} not registered')
                    return 'ERROR: NOT REGISTERED'


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
