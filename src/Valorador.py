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
import logging
import os
import socket
from collections import Counter
from datetime import datetime, timedelta

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
    build_feedback_request,
    build_recommendation_notice,
    build_ratings_response,
    build_search_request,
    build_status_response,
    build_user_purchases_request,
    build_week_purchases_request,
    directory_addresses_from_response,
    feedback_from_request,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    product_ids_from_ratings_request,
    products_from_search_response,
    purchases_from_response,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
)
from RuntimeInfo import render_runtime_info, rows_from_mapping, rows_from_sequence, table_section

app = Flask(__name__)

log_prefix = 'valorador'
diraddress = ''
FEEDBACK_REQUESTED = set()
RECOMMENDATIONS_SENT = set()
FEEDBACK_DELAY_SECONDS = int(os.environ.get('ECSDI_FEEDBACK_DELAY_SECONDS', str(7 * 24 * 60 * 60)))
BAYES_PRIOR_ALPHA = float(os.environ.get('ECSDI_BAYES_PRIOR_ALPHA', '2.0'))
BAYES_PRIOR_BETA = float(os.environ.get('ECSDI_BAYES_PRIOR_BETA', '2.0'))


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


RECOMMENDATION_CATALOG_FALLBACK = [
    {
        'id': 'P1001',
        'name': 'Auriculares Inalambricos SoundGo',
        'brand': 'SoundGo',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'warehouse_managed': True,
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
        'warehouse_managed': True,
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
        'warehouse_managed': True,
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
        'warehouse_managed': True,
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
        'warehouse_managed': False,
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
        'warehouse_managed': False,
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
        'warehouse_managed': True,
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
        'warehouse_managed': False,
        'price': 31.2,
        'tags': ['libro', 'python', 'agentes']
    },
    {
        'id': 'P1009',
        'name': 'Webcam Full HD FocusCam',
        'brand': 'ViewSky',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'warehouse_managed': True,
        'price': 54.9,
        'tags': ['oficina', 'video', 'teletrabajo']
    },
    {
        'id': 'P1010',
        'name': 'Hub USB-C 7 en 1',
        'brand': 'TechHub',
        'seller': 'TechHub',
        'provider': 'TechHub',
        'external': True,
        'warehouse_managed': True,
        'price': 49.95,
        'tags': ['usb-c', 'oficina', 'accesorios']
    },
    {
        'id': 'P1011',
        'name': 'Altavoz Bluetooth Mini',
        'brand': 'SoundGo',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'warehouse_managed': True,
        'price': 29.9,
        'tags': ['audio', 'bluetooth', 'altavoz']
    },
    {
        'id': 'P1012',
        'name': 'Set Cafes Especialidad 4 Origenes',
        'brand': 'CasaViva',
        'seller': 'HomePlus',
        'provider': 'HomePlus',
        'external': True,
        'warehouse_managed': False,
        'price': 27.5,
        'tags': ['hogar', 'cocina', 'cafe']
    },
    {
        'id': 'P1013',
        'name': 'Lampara Escritorio LED Flex',
        'brand': 'LumiHome',
        'seller': 'ECSDI Store',
        'provider': 'ECSDI Store',
        'external': False,
        'warehouse_managed': True,
        'price': 34.25,
        'tags': ['hogar', 'iluminacion', 'oficina']
    },
    {
        'id': 'P1014',
        'name': 'Guia Practica Multiagentes',
        'brand': 'EdTech',
        'seller': 'BookPlanet',
        'provider': 'BookPlanet',
        'external': True,
        'warehouse_managed': False,
        'price': 24.8,
        'tags': ['libro', 'agentes', 'rdf']
    }
]

RATING_EVENTS = {
    'P1001': [4.0, 4.5, 4.3],
    'P1002': [4.5, 4.8, 4.7],
    'P1003': [4.0, 4.2, 4.1],
    'P1004': [4.4, 4.6, 4.8],
    'P1005': [4.1, 4.4, 4.7],
    'P1006': [3.8, 4.0, 4.2],
    'P1007': [4.0, 4.3, 4.4],
    'P1008': [4.6, 4.8, 5.0],
    'P1009': [4.2, 4.5, 4.7],
    'P1010': [4.0, 4.3, 4.4],
    'P1011': [4.2, 4.4, 4.6],
    'P1012': [3.9, 4.1, 4.4],
    'P1013': [4.4, 4.6, 4.8],
    'P1014': [4.5, 4.7, 4.9],
}

RATINGS = {
    product_id: sum(values) / len(values)
    for product_id, values in RATING_EVENTS.items()
}


def get_ratings(product_ids):
    return {pid: float(RATINGS.get(pid, 3.5)) for pid in product_ids}


def directory_addresses(agent_type, all_agents=False):
    if not diraddress:
        return [], 'DIRECTORY NOT CONFIGURED'
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except ConnectionError:
        return [], 'CONNECTION ERROR'
    except Exception:
        return [], 'INVALID DIRECTORY RESPONSE'

    if not response_ok(response):
        return [], response_text(response, 'NOT FOUND')
    addresses = directory_addresses_from_response(response)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None


def query_ventas_purchases(week_only=False):
    addresses, error = directory_addresses('VENTAS')
    if error:
        return [], error
    request_graph = (
        build_week_purchases_request(sender=log_prefix, receiver='VENTAS')
        if week_only
        else build_user_purchases_request('', sender=log_prefix, receiver='VENTAS')
    )
    try:
        response = send_graph_message(addresses[0], request_graph)
    except ConnectionError:
        return [], 'VENTAS CONNECTION ERROR'
    except Exception:
        return [], 'VENTAS INVALID RESPONSE'
    if not response_ok(response):
        return [], response_text(response, 'VENTAS ERROR')
    return purchases_from_response(response), None


def query_catalog_products():
    addresses, error = directory_addresses('CATALOGADOR')
    if error:
        return [dict(product) for product in RECOMMENDATION_CATALOG_FALLBACK], error

    try:
        response = send_graph_message(
            addresses[0],
            build_search_request({}, sender=log_prefix, receiver='CATALOGADOR')
        )
    except ConnectionError:
        return [dict(product) for product in RECOMMENDATION_CATALOG_FALLBACK], 'CATALOGADOR CONNECTION ERROR'
    except Exception:
        return [dict(product) for product in RECOMMENDATION_CATALOG_FALLBACK], 'CATALOGADOR INVALID RESPONSE'

    if not response_ok(response):
        return [dict(product) for product in RECOMMENDATION_CATALOG_FALLBACK], response_text(response, 'CATALOGADOR ERROR')

    products = products_from_search_response(response)
    if not products:
        return [dict(product) for product in RECOMMENDATION_CATALOG_FALLBACK], 'CATALOGADOR EMPTY'
    return [normalize_product(product) for product in products], None


def send_to_clients(graph):
    addresses, error = directory_addresses('CLIENTE', all_agents=True)
    if error:
        log(f'CLIENTE not found: {error}')
        return 0
    sent = 0
    for address in addresses:
        try:
            response = send_graph_message(address, graph)
            if response_ok(response):
                sent += 1
        except Exception as exc:
            log(f'CLIENTE proactive message failed at {address}: {exc}')
    return sent


def normalize_product(product):
    pdata = dict(product)
    product_id = str(pdata.get('id') or '').strip()
    if pdata.get('rating') is None and product_id:
        pdata['rating'] = round(float(RATINGS.get(product_id, 3.5)), 2)
    pdata.setdefault('tags', [])
    return pdata


def product_keys(product):
    keys = set()
    for field in ('id', 'name'):
        value = str(product.get(field) or '').strip().lower()
        if value:
            keys.add(value)
    return keys


def catalog_index(products):
    index = {}
    for product in products:
        for key in product_keys(product):
            index[key] = product
    return index


def iter_purchase_items(purchases):
    for purchase in purchases:
        for item in purchase.get('items') or []:
            yield item


def client_tag_profile(client_purchases, index):
    tags = Counter()
    for item in iter_purchase_items(client_purchases):
        source = next((index[key] for key in product_keys(item) if key in index), item)
        for tag in source.get('tags') or []:
            normalized = str(tag).strip().lower()
            if normalized:
                tags[normalized] += int(item.get('quantity', 1) or 1)
    return tags


def purchase_popularity(all_purchases):
    counts = Counter()
    total = 0
    for item in iter_purchase_items(all_purchases):
        quantity = int(item.get('quantity', 1) or 1)
        if quantity <= 0:
            quantity = 1
        item_keys = product_keys(item)
        for key in item_keys:
            counts[key] += quantity
        if item_keys:
            total += quantity
    return counts, total


def bayesian_quality(product_id):
    events = [float(value) for value in RATING_EVENTS.get(product_id, [])]
    if not events and product_id in RATINGS:
        events = [float(RATINGS[product_id])]

    successes = sum(max(0.0, min(5.0, rating)) / 5.0 for rating in events)
    failures = len(events) - successes
    return (BAYES_PRIOR_ALPHA + successes) / (BAYES_PRIOR_ALPHA + BAYES_PRIOR_BETA + successes + failures)


def recommendation_affinity(product, tag_profile):
    product_tags = {
        str(tag).strip().lower()
        for tag in product.get('tags') or []
        if str(tag).strip()
    }
    if not product_tags:
        return 0.5
    if not tag_profile:
        return 0.5
    matched_weight = sum(tag_profile.get(tag, 0) for tag in product_tags)
    total_weight = sum(tag_profile.values()) or 1
    return matched_weight / total_weight


def recommendations_for_client(client_purchases, all_purchases):
    catalog_products, error = query_catalog_products()
    if error:
        log(f'Catalog fallback for recommendations: {error}')
    catalog_products = [normalize_product(product) for product in catalog_products]
    index = catalog_index(catalog_products)
    purchased = set()
    for item in iter_purchase_items(client_purchases):
        purchased.update(product_keys(item))

    tag_profile = client_tag_profile(client_purchases, index)
    popularity_counts, popularity_total = purchase_popularity(all_purchases)
    ranked = []

    for product in catalog_products:
        keys = product_keys(product)
        if keys & purchased:
            continue
        product_id = str(product.get('id') or '').strip()
        quality = bayesian_quality(product_id)
        affinity = recommendation_affinity(product, tag_profile)
        popularity_count = max([popularity_counts.get(key, 0) for key in keys] or [0])
        popularity = (1 + popularity_count) / (2 + max(popularity_total, 0))
        score = quality * (0.65 + 0.35 * affinity) * (0.85 + 0.15 * popularity)

        recommendation = dict(product)
        recommendation['rating'] = round(float(recommendation.get('rating', RATINGS.get(product_id, 3.5))), 2)
        recommendation['recommendation_score'] = round(score * 5.0, 2)
        ranked.append((score, recommendation['rating'], recommendation.get('name', ''), recommendation))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[3] for item in ranked[:3]]


def parse_delivery_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def feedback_due(purchase):
    delivery_date = parse_delivery_date(purchase.get('delivery_date'))
    if delivery_date is None:
        return False

    now = datetime.now(delivery_date.tzinfo) if delivery_date.tzinfo else datetime.now()
    return now >= delivery_date + timedelta(seconds=FEEDBACK_DELAY_SECONDS)


def save_feedback(graph, content):
    feedback = feedback_from_request(graph, content)
    product_id = feedback['product_id']
    rating = float(feedback['rating'])
    if not product_id:
        return False, 'PRODUCTO INVALIDO'
    if rating < 0.0 or rating > 5.0:
        return False, 'VALORACION INVALIDA'
    values = RATING_EVENTS.setdefault(product_id, [])
    values.append(rating)
    RATINGS[product_id] = sum(values) / len(values)
    log(f'Feedback recibido producto={product_id} rating={rating:.2f}')
    return True, 'FEEDBACK GUARDADO'


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

    allowed_performatives = {ACL.request, ACL['query-ref'], ACL.inform}
    if props['performative'] not in allowed_performatives:
        log('Unknown request type')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.RespuestaFeedbackCliente):
        ok, text = save_feedback(graph, content)
        response = build_status_response(
            log_prefix,
            sender,
            ok=ok,
            text=text,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not has_type(graph, content, ECSDI.PeticionValoracionesProducto):
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
        product_ids = product_ids_from_ratings_request(graph, content)
        if not product_ids:
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text='INVALID PRODUCT IDS',
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        ratings = get_ratings(product_ids)
        log(f'PeticionValoracionesProducto product_ids={product_ids} -> {len(ratings)} ratings')
        response = build_ratings_response(
            ratings,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)
    except Exception as exc:
        log(f'PeticionValoracionesProducto failed: {exc}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PAYLOAD',
            conversation_id=conversation_id
        )
        return serialize_graph(response)


@app.route('/tick/feedback')
def tick_feedback():
    purchases, error = query_ventas_purchases(week_only=True)
    if error:
        text = f'FEEDBACK TIMER ERROR: {error}'
        log(text)
        return text

    sent = 0
    for purchase in purchases:
        if not feedback_due(purchase):
            continue
        items = purchase.get('items') or []
        if not items:
            continue
        for item in items:
            product_key = item.get('id') or item.get('name') or ''
            key = (purchase.get('id', ''), product_key)
            if key in FEEDBACK_REQUESTED:
                continue
            graph = build_feedback_request(purchase, item, sender=log_prefix, receiver='CLIENTE')
            delivered = send_to_clients(graph)
            if delivered:
                FEEDBACK_REQUESTED.add(key)
                sent += delivered

    text = f'FEEDBACK REQUESTS={sent}'
    log(text)
    return text


@app.route('/tick/recomendaciones')
def tick_recomendaciones():
    purchases, error = query_ventas_purchases(week_only=False)
    if error:
        text = f'RECOMENDACION TIMER ERROR: {error}'
        log(text)
        return text

    sent = 0
    by_client = {}
    for purchase in purchases:
        client_id = purchase.get('client_id') or ''
        if client_id:
            by_client.setdefault(client_id, []).append(purchase)

    for client_id, client_purchases in by_client.items():
        if client_id in RECOMMENDATIONS_SENT:
            continue
        recommendations = recommendations_for_client(client_purchases, purchases)
        if not recommendations:
            continue
        product_names = [
            product.get('name') or product.get('id') or 'producto'
            for product in recommendations
        ]
        graph = build_recommendation_notice(
            client_id,
            recommendations,
            sender=log_prefix,
            receiver='CLIENTE',
            message='Recomendaciones bayesianas: ' + ', '.join(product_names)
        )
        delivered = send_to_clients(graph)
        if delivered:
            RECOMMENDATIONS_SENT.add(client_id)
            sent += delivered

    text = f'RECOMENDACIONES={sent}'
    log(text)
    return text


@app.route('/info')
def info():
    config_rows = [{
        'feedback_delay_seconds': FEEDBACK_DELAY_SECONDS,
        'bayes_prior_alpha': BAYES_PRIOR_ALPHA,
        'bayes_prior_beta': BAYES_PRIOR_BETA,
    }]
    stats = [
        {'label': 'Productos valorados', 'value': len(RATINGS)},
        {'label': 'Feedback solicitado', 'value': len(FEEDBACK_REQUESTED)},
        {'label': 'Clientes recomendados', 'value': len(RECOMMENDATIONS_SENT)},
    ]
    rating_rows = [
        {'product_id': product_id, 'rating': round(rating, 2), 'events': len(RATING_EVENTS.get(product_id, []))}
        for product_id, rating in sorted(RATINGS.items())
    ]
    sections = [
        table_section('Configuracion', config_rows),
        table_section('Valoraciones agregadas', rating_rows, empty='No hay valoraciones'),
        table_section('Eventos de rating', rows_from_mapping(RATING_EVENTS, id_key='product_id'), empty='No hay eventos'),
        table_section('Feedback ya solicitado', rows_from_sequence(sorted(FEEDBACK_REQUESTED)), empty='No hay feedback solicitado'),
        table_section('Clientes con recomendaciones enviadas', rows_from_sequence(sorted(RECOMMENDATIONS_SENT)), empty='No hay recomendaciones enviadas'),
    ]
    return render_runtime_info('Valorador', log_prefix, stats=stats, sections=sections)


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
    parser.add_argument('--feedback-delay-seconds', type=int, default=None,
                        help='Segundos despues de la fecha prevista de entrega para pedir feedback (demo: 30)')

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
    if args.feedback_delay_seconds is not None:
        FEEDBACK_DELAY_SECONDS = max(0, int(args.feedback_delay_seconds))
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    agentadd = f'http://{hostaddr}:{port}'
    mess = build_directory_register(log_prefix, 'VALORADOR', agentadd, sender=log_prefix)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{log_prefix} successfully registered')
        # Try to connect to Logger for packet tracing
        try:
            _lr = send_graph_message(diraddress, build_directory_search('LOGGER', sender=log_prefix))
            if response_ok(_lr):
                _la = directory_addresses_from_response(_lr)
                if _la:
                    set_tracer_url(_la[0])
                    log(f'Packet tracing enabled → {_la[0]}')
        except Exception:
            pass
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{log_prefix} unregistering')
        mess = build_directory_unregister(log_prefix, sender=log_prefix)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
