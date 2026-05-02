"""
.. module:: Cercador

Cercador
*************

:Description: Cercador

 Agente de busqueda de productos por filtros.

:Authors: Jaume

:Version:

:Created on: 01/05/2026

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

log_prefix = 'cercador'
diraddress = ''


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


catalog = [
    {
        'id': 'P1001',
        'name': 'Auriculares Inalambricos SoundGo',
        'brand': 'SoundGo',
        'seller': 'ECSDI Store',
        'price': 39.99,
        'tags': ['audio', 'bluetooth', 'auriculares']
    },
    {
        'id': 'P1002',
        'name': 'Teclado Mecanico K85',
        'brand': 'TypeMaster',
        'seller': 'TechHub',
        'price': 79.95,
        'tags': ['teclado', 'gaming', 'mecanico']
    },
    {
        'id': 'P1003',
        'name': 'Mouse Ergonomico MX Lite',
        'brand': 'PointPro',
        'seller': 'ECSDI Store',
        'price': 24.5,
        'tags': ['mouse', 'ergonomico', 'oficina']
    },
    {
        'id': 'P1004',
        'name': 'Monitor 27 IPS 2K',
        'brand': 'ViewSky',
        'seller': 'ScreenWorld',
        'price': 229.0,
        'tags': ['monitor', '2k', 'oficina']
    },
    {
        'id': 'P1005',
        'name': 'Cafetera Espresso Compacta',
        'brand': 'CasaViva',
        'seller': 'HomePlus',
        'price': 119.0,
        'tags': ['hogar', 'cocina', 'cafe']
    },
    {
        'id': 'P1006',
        'name': 'Mochila Urbana 20L',
        'brand': 'UrbanTrail',
        'seller': 'BagStore',
        'price': 45.0,
        'tags': ['mochila', 'viaje', 'accesorios']
    },
    {
        'id': 'P1007',
        'name': 'Bombillas LED Pack 6',
        'brand': 'LumiHome',
        'seller': 'ECSDI Store',
        'price': 16.75,
        'tags': ['hogar', 'iluminacion', 'led']
    },
    {
        'id': 'P1008',
        'name': 'Libro Python para Agentes',
        'brand': 'EdTech',
        'seller': 'BookPlanet',
        'price': 31.2,
        'tags': ['libro', 'python', 'agentes']
    }
]

HARDCODED_RATINGS = {
    'P1001': 4.3,
    'P1002': 4.7,
    'P1003': 4.1,
    'P1004': 4.6,
    'P1005': 4.4,
    'P1006': 4.0,
    'P1007': 4.2,
    'P1008': 4.8
}


def fetch_ratings(products):
    product_ids = [str(p.get('id', '')).strip() for p in products if str(p.get('id', '')).strip()]
    ratings = {}

    if product_ids and diraddress:
        # TODO: Replace this hardcoded integration with real VALORADOR protocol once the agent exists.
        try:
            rating_agent = requests.get(diraddress + '/message', params={'message': 'SEARCH|VALORADOR'}).text
            if rating_agent.startswith('OK: '):
                rating_addr = rating_agent[4:]
                payload = json.dumps({'product_ids': product_ids})
                rating_resp = requests.get(
                    rating_addr + '/message',
                    params={'message': f'OBTENER_VALORACIONES|{payload}'}
                ).text
                if rating_resp.startswith('OK: '):
                    parsed = json.loads(rating_resp[4:])
                    if isinstance(parsed, dict):
                        for pid, val in parsed.items():
                            try:
                                ratings[pid] = float(val)
                            except (TypeError, ValueError):
                                pass
        except Exception as exc:
            log(f'VALORADOR lookup failed, using local ratings: {exc}')

    for pid in product_ids:
        if pid not in ratings:
            ratings[pid] = HARDCODED_RATINGS.get(pid, 3.5)

    return ratings


def enrich_with_ratings(products):
    ratings = fetch_ratings(products)
    enriched = []
    for product in products:
        pid = str(product.get('id', '')).strip()
        rating = ratings.get(pid, 3.5)
        pdata = dict(product)
        pdata['rating'] = round(float(rating), 2)
        enriched.append(pdata)
    return enriched


def matches_product(product, filters):
    name = str(filters.get('name', '')).strip().lower()
    brand = str(filters.get('brand', '')).strip().lower()
    seller = str(filters.get('seller', '')).strip().lower()
    raw_tags = filters.get('tags', [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(',') if t.strip()]
    tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()]
    min_price = filters.get('min_price')
    max_price = filters.get('max_price')

    if name and name not in product['name'].lower():
        return False
    if brand and brand not in product['brand'].lower():
        return False
    if seller and seller not in product['seller'].lower():
        return False

    product_tags = [t.lower() for t in product['tags']]
    for tag in tags:
        if tag not in product_tags:
            return False

    if min_price is not None and product['price'] < float(min_price):
        return False
    if max_price is not None and product['price'] > float(max_price):
        return False

    return True


def search_catalog(filters):
    return [p for p in catalog if matches_product(p, filters)]


@app.route('/message')
def message():
    mess = request.args['message']

    if '|' not in mess:
        log(f'Invalid message (no |): {mess}')
        return 'ERROR: INVALID MESSAGE'

    messtype, messparam = mess.split('|', 1)

    if messtype not in ['BUSCAR_PRODUCTOS']:
        log(f'Unknown request type: {messtype}')
        return 'ERROR: INVALID REQUEST'

    if messtype == 'BUSCAR_PRODUCTOS':
        try:
            filters = json.loads(messparam)
            if not isinstance(filters, dict):
                return 'ERROR: INVALID FILTERS'

            results = search_catalog(filters)
            rated_results = enrich_with_ratings(results)
            log(f'BUSCAR_PRODUCTOS filters={filters} -> {len(rated_results)} resultados')
            return 'OK: ' + json.dumps(rated_results)
        except Exception as e:
            log(f'BUSCAR_PRODUCTOS failed: {e}')
            return 'ERROR: INVALID FILTERS'


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
        port = 9040
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'cercador-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    agentadd = f'http://{hostaddr}:{port}'
    agentid = hostaddr.split('.')[0] + '-' + str(port)
    mess = f'REGISTER|{agentid},CERCADOR,{agentadd}'

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
