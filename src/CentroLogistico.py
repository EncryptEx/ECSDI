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
from flask import Flask, request
from requests import ConnectionError
import random
import logging
import os
from datetime import datetime, timedelta

from GeoUtils import location_for_logistics_port
from RuntimeInfo import render_runtime_info, summarize_items, table_section

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_existence_response,
    build_purchase_result,
    build_shipping_accept_offer_request,
    build_shipping_counter_offer_request,
    build_shipping_quote_request,
    build_status_response,
    build_transfer_request,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_line_request,
    purchase_from_content,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
    shipping_offer_from_response,
)

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'logistico'
diraddress = ''
DELIVERY_DELAY_SECONDS = int(os.environ.get('ECSDI_DELIVERY_DELAY_SECONDS', str(2 * 24 * 60 * 60)))
LOT_FULL_UNITS = int(os.environ.get('ECSDI_LOT_FULL_UNITS', '1'))
URGENT_WINDOW_SECONDS = int(os.environ.get('ECSDI_URGENT_WINDOW_SECONDS', '3600'))
CENTER_LOCATION = location_for_logistics_port(9030)
WAREHOUSE_MANAGED_PRODUCTS = [
    'Auriculares Inalambricos SoundGo',
    'Teclado Mecanico K85',
    'Mouse Ergonomico MX Lite',
    'Monitor 27 IPS 2K',
    'Bombillas LED Pack 6',
    'Webcam Full HD FocusCam',
    'Hub USB-C 7 en 1',
    'Altavoz Bluetooth Mini',
    'Lampara Escritorio LED Flex',
]


def build_initial_stock():
    seed = os.environ.get('ECSDI_STOCK_SEED')
    rng = random.Random(f'{seed}-{os.getpid()}') if seed else random.Random()
    stock = {}

    for product in WAREHOUSE_MANAGED_PRODUCTS:
        if rng.random() < 0.65:
            stock[product] = rng.randint(1, 8)

    if not stock:
        product = rng.choice(WAREHOUSE_MANAGED_PRODUCTS)
        stock[product] = rng.randint(1, 8)

    return stock


STOCK = build_initial_stock()
LOTES_PENDIENTES = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def item_total(item):
    quantity = int(item.get('quantity', 1))
    price = item.get('line_price', item.get('price', 0.0))
    return float(price or 0.0) * quantity


def is_external_item(item):
    if item.get('external') is not None:
        return bool(item.get('external'))
    seller = str(item.get('seller') or item.get('provider') or '').strip().lower()
    return bool(seller and seller != 'ecsdi store')


def send_to_tesorero(graph):
    if not diraddress:
        log('TESORERO not queried because directory address is not configured')
        return False
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search('TESORERO', sender=log_prefix)
        )
    except Exception as exc:
        log(f'TESORERO directory lookup failed: {exc}')
        return False

    if not response_ok(response):
        log(f'TESORERO not found: {response_text(response, "NOT FOUND")}')
        return False

    addresses = directory_addresses_from_response(response)
    if not addresses:
        log('TESORERO not found: empty address list')
        return False

    try:
        transfer_response = send_graph_message(addresses[0], graph)
        return response_ok(transfer_response)
    except Exception as exc:
        log(f'TESORERO communication failed: {exc}')
        return False


def directory_addresses(agent_type, all_agents=False):
    if not diraddress:
        return [], 'DIRECTORY NOT CONFIGURED'
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except Exception as exc:
        return [], str(exc)

    if not response_ok(response):
        return [], response_text(response, 'NOT FOUND')
    addresses = directory_addresses_from_response(response)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None


def lot_weight(purchase):
    units = sum(int(item.get('quantity', 1)) for item in purchase.get('items') or []) or 1
    return round(1.0 + units * 0.5, 2)


def parse_datetime(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def purchase_deadline(purchase):
    direct = parse_datetime(purchase.get('delivery_deadline'))
    if direct:
        return direct
    deadlines = []
    for item in purchase.get('items') or []:
        parsed = parse_datetime(item.get('delivery_deadline'))
        if parsed:
            deadlines.append(parsed)
    return min(deadlines) if deadlines else None


def lot_units(purchase):
    return sum(int(item.get('quantity', 1)) for item in purchase.get('items') or [])


def lot_ready_status(lot, now=None, force=False):
    if force:
        return True, 'forzado'
    purchase = lot.get('purchase') or {}
    units = lot_units(purchase)
    if units >= LOT_FULL_UNITS:
        return True, f'lote lleno ({units}/{LOT_FULL_UNITS} uds)'
    deadline = purchase_deadline(purchase)
    if deadline:
        now = now or datetime.now()
        remaining = (deadline - now).total_seconds()
        if remaining <= URGENT_WINDOW_SECONDS:
            return True, f'prioridad por plazo ({int(remaining)}s restantes)'
        return False, f'esperando llenar lote ({units}/{LOT_FULL_UNITS} uds, plazo en {int(remaining)}s)'
    return False, f'esperando llenar lote ({units}/{LOT_FULL_UNITS} uds, sin plazo maximo)'


def counter_offer_target(initial_price, current_price, round_index):
    floor_target = initial_price * 0.55
    step_target = initial_price - ((round_index + 1) * 2.0)
    target = min(current_price - 0.01, max(floor_target, step_target))
    return round(max(target, 0.01), 2)


def negotiate_with_transportista(address, purchase):
    weight = lot_weight(purchase)
    try:
        response = send_graph_message(
            address,
            build_shipping_quote_request(
                purchase,
                sender=log_prefix,
                receiver='TRANSPORTISTA',
                weight=weight
            )
        )
    except Exception as exc:
        log(f'TRANSPORTISTA quote failed at {address}: {exc}')
        return None

    if not response_ok(response):
        log(f'TRANSPORTISTA quote refused at {address}: {response_text(response, "ERROR")}')
        return None

    offer = shipping_offer_from_response(response)
    current_price = float(offer.get('price') or 0.0)
    if current_price <= 0:
        return None

    initial_price = current_price
    transportista = offer.get('transportista') or address
    rounds = []

    for round_index in range(8):
        target = counter_offer_target(initial_price, current_price, round_index)
        try:
            counter_response = send_graph_message(
                address,
                build_shipping_counter_offer_request(
                    purchase,
                    target,
                    sender=log_prefix,
                    receiver='TRANSPORTISTA',
                    weight=weight
                )
            )
        except Exception as exc:
            log(f'TRANSPORTISTA counter-offer failed at {address}: {exc}')
            break

        if not response_ok(counter_response):
            log(f'TRANSPORTISTA counter-offer refused at {address}: {response_text(counter_response, "ERROR")}')
            break

        counter_offer = shipping_offer_from_response(counter_response)
        new_price = float(counter_offer.get('price') or current_price)
        rounds.append({'requested': target, 'response': new_price})
        transportista = counter_offer.get('transportista') or transportista

        if counter_offer.get('accepted'):
            current_price = new_price
            break

        if round(new_price, 2) == round(current_price, 2):
            break

        current_price = new_price

    return {
        'address': address,
        'transportista': transportista,
        'price': round(current_price, 2),
        'initial_price': initial_price,
        'rounds': rounds,
        'weight': weight,
    }


def accept_transport_offer(offer, purchase):
    try:
        response = send_graph_message(
            offer['address'],
            build_shipping_accept_offer_request(
                purchase,
                offer['price'],
                sender=log_prefix,
                receiver='TRANSPORTISTA',
                transportista=offer.get('transportista', ''),
                weight=offer.get('weight', 1.0)
            )
        )
    except Exception as exc:
        log(f'TRANSPORTISTA accept failed at {offer["address"]}: {exc}')
        return False
    return response_ok(response)


def negotiate_transport(purchase):
    addresses, error = directory_addresses('TRANSPORTISTA', all_agents=True)
    if error:
        log(f'TRANSPORTISTA not found for lot negotiation: {error}')
        return None, []

    offers = []
    for address in addresses:
        offer = negotiate_with_transportista(address, purchase)
        if offer:
            offers.append(offer)

    if not offers:
        return None, []

    best = min(offers, key=lambda offer: offer['price'])
    if not accept_transport_offer(best, purchase):
        log(f'Best transport offer could not be accepted: {best}')
        return None, offers
    log(
        f'Transport selected compra={purchase.get("id", "")} '
        f'{best["transportista"]} price={best["price"]:.2f}'
    )
    return best, offers


def build_shipping_notice_payload(purchase, transport):
    now = datetime.now()
    planned_delivery = now + timedelta(seconds=DELIVERY_DELAY_SECONDS)
    deadline = purchase_deadline(purchase)
    priority_applied = False
    if deadline and planned_delivery > deadline:
        planned_delivery = deadline if deadline > now else now
        priority_applied = True
    delivery_date = planned_delivery.replace(microsecond=0).isoformat()
    tracking_id = f'{log_prefix}-{purchase.get("id", "compra")}'
    notice_purchase = dict(purchase)
    notice_purchase['delivery_date'] = delivery_date
    notice_purchase['transportista'] = transport.get('transportista') or 'Transportista asignado'
    notice_purchase['tracking_id'] = tracking_id
    message = f'Datos de envio generados por el temporizador de lotes. Precio transporte: {transport.get("price", 0.0):.2f}'
    if deadline:
        message += f'. Plazo maximo solicitado: {deadline.replace(microsecond=0).isoformat()}'
    if priority_applied:
        message += '. Envio priorizado para respetar el plazo maximo.'
    return {
        'purchase': notice_purchase,
        'transportista': notice_purchase['transportista'],
        'delivery_date': delivery_date,
        'tracking_id': tracking_id,
        'message': message,
    }


def notify_financials_for_purchase(purchase, transport=None):
    items = purchase.get('items') or []
    if not items:
        return True

    ok = True
    total = sum(item_total(item) for item in items)
    shipping_notice = build_shipping_notice_payload(purchase, transport) if transport else None
    if total > 0:
        ok = send_to_tesorero(build_transfer_request(
            'lote',
            total,
            sender=log_prefix,
            receiver='TESORERO',
            participant=purchase.get('client_id', ''),
            purchase=purchase,
            message_name='cobrar-envios-lote',
            shipping_notice=shipping_notice
        )) and ok

    for item in items:
        if not is_external_item(item):
            continue
        amount = item_total(item)
        if amount <= 0:
            continue
        provider = item.get('provider') or item.get('seller') or ''
        provider_purchase = dict(purchase)
        provider_purchase['items'] = [item]
        ok = send_to_tesorero(build_transfer_request(
            'ext',
            amount,
            sender=log_prefix,
            receiver='TESORERO',
            participant=provider,
            provider=provider,
            purchase=provider_purchase,
            message_name='pagar-valor-producto'
        )) and ok
    return ok


@app.route("/message")
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
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

    allowed_performatives = {ACL.request, ACL['query-if']}
    if props['performative'] not in allowed_performatives:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionExisteLineaComanda):
        requested = products_from_line_request(graph, content)
        log(f'PeticionExisteLineaComanda query for: {requested}')
        availability = {
            product: STOCK.get(product, 0) >= int(quantity)
            for product, quantity in requested.items()
        }
        log(f'PeticionExisteLineaComanda response: {availability}')
        response = build_existence_response(
            availability,
            requested,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id,
            center_location=CENTER_LOCATION
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionAsignarLoteProducto) or has_type(graph, content, ECSDI.PeticionGuardarCompra):
        products = products_from_line_request(graph, content)
        log(f'PeticionAsignarLoteProducto: {products}')
        unavailable = [
            product for product, quantity in products.items()
            if STOCK.get(product, 0) < int(quantity)
        ]
        if unavailable:
            response = build_status_response(
                log_prefix,
                sender,
                ok=False,
                text=f'SIN STOCK: {", ".join(unavailable)}',
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        log('Stock check passed; stock is not decremented in demo mode')
        LOTES_PENDIENTES.append({
            'sender': sender,
            'products': dict(products),
            'purchase': purchase_from_content(graph, content),
            'shipping_sent': False,
            'financials_sent': False,
            'transport': None,
            'transport_options': [],
            'ready_reason': '',
        })
        response = build_purchase_result(
            True,
            sender=log_prefix,
            receiver=sender,
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    log('Unknown request')
    response = build_status_response(
        log_prefix,
        sender,
        ok=False,
        text='INVALID REQUEST',
        conversation_id=conversation_id
    )
    return serialize_graph(response)
            

@app.route("/tick/envios")
def tick_envios():
    """
    Temporizador manual para la demo: procesa lotes pendientes y envia datos
    de envio al cliente.
    """
    processed = 0
    notified = 0
    negotiated = 0
    skipped = 0
    force = request.args.get('force', '').strip().lower() in {'1', 'true', 'yes', 'si'}
    now = datetime.now()
    for lot in LOTES_PENDIENTES:
        if lot.get('shipping_sent'):
            continue
        purchase = lot.get('purchase') or {'items': []}
        ready, reason = lot_ready_status(lot, now=now, force=force)
        lot['ready_reason'] = reason
        if not ready:
            skipped += 1
            log(f'Lot compra={purchase.get("id", "")} not sent: {reason}')
            continue
        transport = lot.get('transport')
        if not transport:
            transport, options = negotiate_transport(purchase)
            lot['transport_options'] = options
            if not transport:
                continue
            lot['transport'] = transport
            negotiated += 1
        if not lot.get('financials_sent'):
            if not notify_financials_for_purchase(purchase, transport):
                log(f'Financial operations failed for lot compra={purchase.get("id", "")}')
                continue
            lot['financials_sent'] = True
        lot['shipping_sent'] = True
        processed += 1
        notified += 1
    text = f'ENVIOS PROCESADOS={processed} NEGOCIACIONES={negotiated} NOTIFICACIONES={notified} OMITIDOS={skipped}'
    log(text)
    return text


@app.route('/info')
def info():
    lot_rows = []
    option_rows = []
    for index, lot in enumerate(LOTES_PENDIENTES, 1):
        purchase = lot.get('purchase') or {}
        transport = lot.get('transport') or {}
        lot_rows.append({
            '#': index,
            'purchase_id': purchase.get('id', ''),
            'client_id': purchase.get('client_id', ''),
            'delivery_address': purchase.get('delivery_address', ''),
            'delivery_deadline': purchase.get('delivery_deadline', ''),
            'products': summarize_items(purchase.get('items') or []),
            'ready_reason': lot.get('ready_reason', ''),
            'financials_sent': lot.get('financials_sent', False),
            'shipping_sent': lot.get('shipping_sent', False),
            'transportista': transport.get('transportista', ''),
            'transport_price': transport.get('price', ''),
        })
        for option in lot.get('transport_options') or []:
            option_rows.append({
                'purchase_id': purchase.get('id', ''),
                'transportista': option.get('transportista', ''),
                'price': option.get('price', ''),
                'accepted': option.get('accepted', ''),
                'final': option.get('final', ''),
            })

    stock_rows = [
        {'product': product, 'units': units}
        for product, units in sorted(STOCK.items())
    ]
    stats = [
        {'label': 'Stock entries', 'value': len(STOCK)},
        {'label': 'Lotes pendientes', 'value': len(LOTES_PENDIENTES)},
        {'label': 'Unidades para lote lleno', 'value': LOT_FULL_UNITS},
        {'label': 'Ventana urgencia segundos', 'value': URGENT_WINDOW_SECONDS},
        {'label': 'Direccion', 'value': CENTER_LOCATION.get('address', '')},
    ]
    sections = [
        table_section('Ubicacion del centro', [CENTER_LOCATION]),
        table_section('Stock demo', stock_rows, empty='Sin stock configurado'),
        table_section('Lotes pendientes / enviados', lot_rows, empty='No hay lotes registrados'),
        table_section('Ofertas de transportistas por lote', option_rows, empty='No hay negociaciones registradas'),
    ]
    return render_runtime_info('Centro Logistico', log_prefix, stats=stats, sections=sections)


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
    parser.add_argument('--dir', default=None, help="Direccion del servicio de directorio")
    parser.add_argument('--hostaddr', default=None, help="Direccion del agente anunciada al exterior (sobreescribe la deteccion automatica)")
    parser.add_argument('--delivery-delay-seconds', type=int, default=None,
                        help="Segundos hasta la fecha prevista de entrega (demo: 0)")
    parser.add_argument('--lot-full-units', type=int, default=None,
                        help="Unidades minimas para considerar lleno un lote")
    parser.add_argument('--urgent-window-seconds', type=int, default=None,
                        help="Segundos antes del plazo maximo que fuerzan el envio del lote")
    parser.add_argument('--center-address', default=None, help="Direccion fisica del centro logistico")
    parser.add_argument('--center-lat', type=float, default=None, help="Latitud del centro logistico")
    parser.add_argument('--center-lon', type=float, default=None, help="Longitud del centro logistico")

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
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'logistico-{port}'
    if args.delivery_delay_seconds is not None:
        DELIVERY_DELAY_SECONDS = max(0, int(args.delivery_delay_seconds))
    if args.lot_full_units is not None:
        LOT_FULL_UNITS = max(1, int(args.lot_full_units))
    if args.urgent_window_seconds is not None:
        URGENT_WINDOW_SECONDS = max(0, int(args.urgent_window_seconds))
    CENTER_LOCATION = location_for_logistics_port(port)
    if args.center_address:
        CENTER_LOCATION['address'] = args.center_address
        CENTER_LOCATION['label'] = args.center_address
    if args.center_lat is not None:
        CENTER_LOCATION['lat'] = float(args.center_lat)
    if args.center_lon is not None:
        CENTER_LOCATION['lon'] = float(args.center_lon)
    log(f'DS Hostname = {hostaddr}')
    log(f'Initial stock = {STOCK}')
    log(f'Center location = {CENTER_LOCATION}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    mess = build_directory_register(log_prefix, 'CENTRO_LOGISTICO', solveradd, sender=log_prefix)

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
        # Ponemos en marcha el servidor Flask
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{log_prefix} unregistering')
        mess = build_directory_unregister(log_prefix, sender=log_prefix)
        send_graph_message(diraddress, mess)
    else:
        log('Unable to register')
