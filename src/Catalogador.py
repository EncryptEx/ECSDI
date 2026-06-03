"""
.. module:: Catalogador

Catalogador
*************

:Description: Catalogador

 Agente de busqueda de productos por filtros.

:Authors: Jaume

:Version:

:Created on: 01/05/2026

"""

import argparse
import logging
import socket

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_new_product_response,
    build_product_info_response,
    build_provider_data_request,
    build_ratings_request,
    build_search_response,
    build_status_response,
    directory_addresses_from_response,
    filters_from_search_request,
    first_literal,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    product_from_graph,
    ratings_from_response,
    products_from_line_request,
    response_ok,
    send_graph_message,
    serialize_graph,
)

app = Flask(__name__)

log_prefix = 'catalogador'
diraddress = ''


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


catalog = [
    {
        'id': 'P1001',
        'name': 'Auriculares Inalambricos SoundGo',
        'brand': 'SoundGo',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'price': 39.99,
        'tags': ['audio', 'bluetooth', 'auriculares']
    },
    {
        'id': 'P1002',
        'name': 'Teclado Mecanico K85',
        'brand': 'TypeMaster',
        'seller': 'TechHub',
        'provider': 'TechHub',
        'external': True,
        'price': 79.95,
        'tags': ['teclado', 'gaming', 'mecanico']
    },
    {
        'id': 'P1003',
        'name': 'Mouse Ergonomico MX Lite',
        'brand': 'PointPro',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'price': 24.5,
        'tags': ['mouse', 'ergonomico', 'oficina']
    },
    {
        'id': 'P1004',
        'name': 'Monitor 27 IPS 2K',
        'brand': 'ViewSky',
        'seller': 'ScreenWorld',
        'provider': 'ScreenWorld',
        'external': True,
        'price': 229.0,
        'tags': ['monitor', '2k', 'oficina']
    },
    {
        'id': 'P1005',
        'name': 'Cafetera Espresso Compacta',
        'brand': 'CasaViva',
        'seller': 'HomePlus',
        'provider': 'HomePlus',
        'external': True,
        'price': 119.0,
        'tags': ['hogar', 'cocina', 'cafe']
    },
    {
        'id': 'P1006',
        'name': 'Mochila Urbana 20L',
        'brand': 'UrbanTrail',
        'seller': 'BagStore',
        'provider': 'BagStore',
        'external': True,
        'price': 45.0,
        'tags': ['mochila', 'viaje', 'accesorios']
    },
    {
        'id': 'P1007',
        'name': 'Bombillas LED Pack 6',
        'brand': 'LumiHome',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'price': 16.75,
        'tags': ['hogar', 'iluminacion', 'led']
    },
    {
        'id': 'P1008',
        'name': 'Libro Python para Agentes',
        'brand': 'EdTech',
        'seller': 'BookPlanet',
        'provider': 'BookPlanet',
        'external': True,
        'price': 31.2,
        'tags': ['libro', 'python', 'agentes']
    }
]

DEFAULT_RATING = 3.5


def fetch_ratings(products):
    product_ids = [str(p.get('id', '')).strip() for p in products if str(p.get('id', '')).strip()]
    ratings = {}

    if not product_ids:
        return ratings

    if product_ids and diraddress:
        try:
            rating_agent = send_graph_message(
                diraddress,
                build_directory_search('VALORADOR', sender=log_prefix)
            )
            if response_ok(rating_agent):
                addresses = directory_addresses_from_response(rating_agent)
                if addresses:
                    rating_addr = addresses[0]
                    rating_resp = send_graph_message(
                        rating_addr,
                        build_ratings_request(product_ids, sender=log_prefix, receiver='VALORADOR')
                    )
                    if response_ok(rating_resp):
                        ratings.update(ratings_from_response(rating_resp))
            else:
                log('VALORADOR not found in directory service; using default ratings')
        except Exception as exc:
            log(f'VALORADOR lookup failed, using default ratings: {exc}')
    else:
        log('Directory service address missing; using default ratings')

    for pid in product_ids:
        if pid not in ratings:
            ratings[pid] = DEFAULT_RATING

    return ratings


def enrich_with_ratings(products):
    ratings = fetch_ratings(products)
    enriched = []
    for product in products:
        pid = str(product.get('id', '')).strip()
        rating = ratings.get(pid, DEFAULT_RATING)
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


def find_product(product_key):
    key = str(product_key).strip().lower()
    if not key:
        return None
    for product in catalog:
        if key == str(product.get('id', '')).lower() or key == str(product.get('name', '')).lower():
            return dict(product)
    for product in catalog:
        if key in str(product.get('name', '')).lower():
            return dict(product)
    return None


def product_info_for_request(graph, content):
    requested = products_from_line_request(graph, content)
    products = []
    for key, quantity in requested.items():
        product = find_product(key)
        if product is None:
            continue
        product['quantity'] = quantity
        products.append(product)
    return products


def notify_provider_data(provider, iban):
    if not provider or not iban or not diraddress:
        return
    try:
        tesorero_resp = send_graph_message(
            diraddress,
            build_directory_search('TESORERO', sender=log_prefix)
        )
        if not response_ok(tesorero_resp):
            return
        addresses = directory_addresses_from_response(tesorero_resp)
        if not addresses:
            return
        send_graph_message(
            addresses[0],
            build_provider_data_request(provider, iban, sender=log_prefix, receiver='TESORERO')
        )
    except Exception as exc:
        log(f'TESORERO provider registration skipped: {exc}')


def external_product_from_request(graph, content):
    product_node = next(graph.objects(content, ECSDI.contiene_productos), None)
    product = product_from_graph(graph, product_node) if product_node is not None else product_from_graph(graph, content)
    provider = first_literal(graph, content, ECSDI.nombreProveedor, product.get('provider') or product.get('seller') or '')
    iban = first_literal(graph, content, ECSDI.numeroIBAN, '')
    if provider:
        product['provider'] = provider
        product.setdefault('seller', provider)
    product['external'] = True
    return product, iban


def apply_min_rating_filter(products, filters):
    min_rating = filters.get('min_rating')
    if min_rating is None or min_rating == '':
        return products

    threshold = float(min_rating)
    return [p for p in products if float(p.get('rating', DEFAULT_RATING)) >= threshold]


@app.route('/message')
def message():
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

    allowed_performatives = {ACL.request, ACL['query-ref']}
    if props['performative'] not in allowed_performatives:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionInfoProductosComprados):
        products = product_info_for_request(graph, content)
        log(f'conjunto-productos-comprados -> {len(products)} productos')
        response = build_product_info_response(
            products,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionNuevoProducto):
        try:
            product, iban = external_product_from_request(graph, content)
            if not product.get('id'):
                product['id'] = f'EXT{len(catalog) + 1:04}'
            if not product.get('name'):
                raise ValueError('missing product name')
            catalog.append(product)
            notify_provider_data(product.get('provider') or product.get('seller'), iban)
            log(f'Nuevo producto externo registrado: {product["id"]} {product["name"]}')
            response = build_new_product_response(
                True,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id,
                text='PRODUCTO REGISTRADO'
            )
            return serialize_graph(response)
        except Exception as exc:
            log(f'PeticionNuevoProducto failed: {exc}')
            response = build_new_product_response(
                False,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id,
                text='PRODUCTO INVALIDO'
            )
            return serialize_graph(response)

    if not has_type(graph, content, ECSDI.PeticionCerca):
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
        filters = filters_from_search_request(graph, content)
        results = search_catalog(filters)
        rated_results = enrich_with_ratings(results)
        filtered_results = apply_min_rating_filter(rated_results, filters)
        log(f'PeticionCerca filters={filters} -> {len(filtered_results)} resultados')
        response = build_search_response(
            filtered_results,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)
    except Exception as e:
        log(f'PeticionCerca failed: {e}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID FILTERS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)


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

    log_prefix = f'catalogador-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    agentadd = f'http://{hostaddr}:{port}'
    agentid = hostaddr.split('.')[0] + '-' + str(port)
    mess = build_directory_register(agentid, 'CATALOGADOR', agentadd, sender=agentid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{agentid} successfully registered')
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        mess = build_directory_unregister(agentid, sender=agentid)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
