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
import re
from FlaskServer import shutdown_server
from flask import Flask, request
from requests import ConnectionError
from Util import gethostname
import logging
import socket
from datetime import datetime, timedelta
from uuid import uuid4

__author__ = 'bejar'

from AgentCommunication import (
    ACL,
    ECSDI,
    availability_from_response,
    build_client_data_request,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_external_sale_request,
    build_line_request,
    build_lot_assignment_request,
    build_product_info_request,
    build_purchase_result,
    build_purchases_response,
    build_return_resolution,
    build_status_response,
    build_transfer_request,
    completed_purchase_from_request,
    delivery_address_from_purchase,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    logistics_location_from_response,
    message_conversation,
    message_sender,
    parse_graph,
    products_from_product_info_response,
    products_from_line_request,
    purchase_from_content,
    response_ok,
    response_text,
    requested_client_id,
    return_request_from_content,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
)
from GeoUtils import distance_from_address_km, location_for_logistics_port
from RuntimeInfo import purchase_row, render_runtime_info, rows_from_sequence, table_section

app = Flask(__name__)

problems = {}
probcounter = 0
log_prefix = 'ventas'
compras = {}
compras_finalizadas = {}
devoluciones = []
RETURN_CENTER_ADDRESS = 'Centro de devoluciones ECSDI, UPC Campus Nord, Barcelona'
EXPECTATIONS_RETURN_DAYS = 15


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def purchase_total(purchase):
    total = 0.0
    for item in purchase.get('items') or []:
        quantity = int(item.get('quantity', 1))
        price = item.get('line_price', item.get('price', 0.0))
        total += float(price or 0.0) * quantity
    return total


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


def product_name(item):
    return str(item.get('name') or item.get('id') or '').strip()


def product_matches(item, key):
    needle = str(key or '').strip().lower()
    if not needle:
        return False
    candidates = {
        str(item.get('id') or '').strip().lower(),
        str(item.get('name') or '').strip().lower(),
    }
    return needle in candidates


def port_from_address(address):
    match = re.search(r':(\d+)(?:/|$)', str(address or ''))
    return int(match.group(1)) if match else None


def logistics_center_location_hint(address):
    port = port_from_address(address)
    return location_for_logistics_port(port) if port is not None else {}


def logistics_centers_by_distance(addresses, delivery_address):
    centers = []
    for address in addresses:
        location = logistics_center_location_hint(address)
        distance = distance_from_address_km(delivery_address, location)
        centers.append({
            'address': address,
            'location': location,
            'distance_km': distance,
        })
    centers.sort(key=lambda item: (
        item['distance_km'] if item['distance_km'] is not None else float('inf'),
        item['address']
    ))
    return centers


def is_external_product(item):
    if item.get('external') is not None:
        return bool(item.get('external'))
    seller = str(item.get('seller') or item.get('provider') or '').strip().lower()
    return bool(seller and seller != 'ecsdi store')


def is_warehouse_managed_product(item):
    if item.get('warehouse_managed') is not None:
        return bool(item.get('warehouse_managed'))
    return not is_external_product(item)


def item_identity(item):
    return (
        str(item.get('id') or '').strip().lower(),
        str(item.get('name') or '').strip().lower(),
        str(item.get('provider') or item.get('seller') or '').strip().lower(),
    )


def merge_purchase(existing, incoming):
    merged = dict(existing)
    for key in ('id', 'client_id', 'client_iban', 'delivery_address', 'delivery_date',
                'delivery_deadline', 'transportista', 'tracking_id'):
        if incoming.get(key):
            merged[key] = incoming[key]

    items = [dict(item) for item in existing.get('items') or []]
    seen = {item_identity(item) for item in items}
    for item in incoming.get('items') or []:
        identity = item_identity(item)
        if identity not in seen:
            items.append(dict(item))
            seen.add(identity)
    merged['items'] = items
    return merged


def store_completed_purchase(purchase):
    purchase_id = purchase.get('id') or str(uuid4())
    purchase['id'] = purchase_id
    existing = compras_finalizadas.get(purchase_id)
    compras_finalizadas[purchase_id] = merge_purchase(existing, purchase) if existing else purchase
    compras.pop(purchase_id, None)
    log(f'Compra finalizada guardada: {purchase_id}')
    return purchase_id


def fallback_product_info(products):
    return [
        {
            'name': product,
            'quantity': quantity,
            'price': 0.0,
            'seller': 'ECSDI Store',
            'provider': 'ECSDI Store',
            'external': False,
            'warehouse_managed': True,
        }
        for product, quantity in products.items()
    ]


def fetch_product_info(products):
    addresses, error = query_directory_service('CATALOGADOR')
    if error:
        log(f'CATALOGADOR not found, using raw purchase lines: {error}')
        return fallback_product_info(products)

    try:
        response = send_graph_message(
            addresses[0],
            build_product_info_request(products, sender=log_prefix, receiver='CATALOGADOR')
        )
    except Exception as exc:
        log(f'CATALOGADOR product info failed, using raw purchase lines: {exc}')
        return fallback_product_info(products)

    if not response_ok(response):
        log(f'CATALOGADOR refused product info: {response_text(response, "ERROR")}')
        return fallback_product_info(products)

    products_info = products_from_product_info_response(response)
    found = {product_name(product).lower(): product for product in products_info}
    for name, quantity in products.items():
        key = str(name).lower()
        if key not in found:
            products_info.append(fallback_product_info({name: quantity})[0])
        else:
            found[key]['quantity'] = quantity
    return products_info


def send_to_tesorero(graph):
    addresses, error = query_directory_service('TESORERO')
    if error:
        log(f'TESORERO not found: {error}')
        return False
    try:
        response = send_graph_message(addresses[0], graph)
        return response_ok(response)
    except Exception as exc:
        log(f'TESORERO communication failed: {exc}')
        return False


def register_client_in_tesorero(client_id, client_iban, delivery_address):
    graph = build_client_data_request(
        client_id,
        client_iban,
        delivery_address,
        sender=log_prefix,
        receiver='TESORERO'
    )
    return send_to_tesorero(graph)


def request_client_charge(purchase):
    total = purchase_total(purchase)
    if total <= 0:
        return False
    graph = build_transfer_request(
        'cli',
        total,
        sender=log_prefix,
        receiver='TESORERO',
        participant=purchase.get('client_id', ''),
        purchase=purchase,
        iban=purchase.get('client_iban', ''),
        message_name='quiero cobrar al usuario'
    )
    return send_to_tesorero(graph)


def request_external_provider_payments(purchase):
    ok = True
    for item in purchase.get('items') or []:
        if not is_external_product(item):
            continue
        amount = float(item.get('price', item.get('line_price', 0.0)) or 0.0) * int(item.get('quantity', 1))
        provider = item.get('provider') or item.get('seller') or ''
        graph = build_transfer_request(
            'ext',
            amount,
            sender=log_prefix,
            receiver='TESORERO',
            participant=provider,
            provider=provider,
            purchase={'id': purchase.get('id'), 'client_id': purchase.get('client_id'), 'items': [item]},
            message_name='pagar-esta-cantidad'
        )
        ok = send_to_tesorero(graph) and ok
    return ok


def notify_client_invoice(purchase, total, conversation_id=None):
    addresses, error = query_directory_service('CLIENTE', all_agents=True)
    if error:
        log(f'CLIENTE not found for invoice notification: {error}')
        return 0

    graph = build_purchase_result(
        True,
        sender=log_prefix,
        receiver='CLIENTE',
        conversation_id=conversation_id,
        total=total,
        purchase=purchase
    )
    sent = 0
    for address in addresses:
        try:
            response = send_graph_message(address, graph)
            if response_ok(response):
                sent += 1
        except Exception as exc:
            log(f'CLIENTE invoice notification failed at {address}: {exc}')
    if sent:
        log(f'Factura enviada a CLIENTE compra={purchase.get("id", "")} total={total:.2f}')
    return sent


def group_external_items_by_provider(items):
    grouped = {}
    for item in items:
        provider = item.get('provider') or item.get('seller') or ''
        grouped.setdefault(provider, []).append(item)
    return grouped


def notify_external_seller(purchase):
    addresses, error = query_directory_service('EMPRESA_VENDEDORA', all_agents=True)
    if error:
        log(f'EMPRESA_VENDEDORA not found: {error}')
        return False

    ok = True
    grouped_items = group_external_items_by_provider(purchase.get('items') or [])
    for provider, items in grouped_items.items():
        provider_purchase = dict(purchase)
        provider_purchase['items'] = items
        delivered = False
        for address in addresses:
            try:
                response = send_graph_message(
                    address,
                    build_external_sale_request(
                        provider,
                        provider_purchase,
                        sender=log_prefix,
                        receiver='EMPRESA_VENDEDORA'
                    )
                )
            except Exception as exc:
                log(f'EMPRESA_VENDEDORA communication failed at {address}: {exc}')
                continue
            if response_ok(response):
                delivered = True
                break
        if not delivered:
            log(f'No external seller accepted delegated sale for provider={provider}')
        ok = delivered and ok
    return ok


def purchases_for_client(client_id):
    if not client_id:
        return list(compras_finalizadas.values())
    return [
        purchase for purchase in compras_finalizadas.values()
        if str(purchase.get('client_id', '')) == str(client_id)
    ]


def return_transport_info(purchase_id):
    addresses, error = query_directory_service('TRANSPORTISTA', all_agents=True)
    transportista = 'Mensajeria ECSDI'
    if not error and addresses:
        port = port_from_address(addresses[0])
        transportista = f'Transportista-{port}' if port else addresses[0]
    return {
        'transportista': transportista,
        'tracking_id': f'DEV-{str(purchase_id or uuid4())[:8]}',
        'return_address': RETURN_CENTER_ADDRESS,
    }


def selected_return_items(purchase, product_key):
    items = [dict(item) for item in purchase.get('items') or []]
    if not product_key:
        return items
    return [item for item in items if product_matches(item, product_key)]


def already_returned(purchase_id, product_key):
    normalized_product = str(product_key or '*').strip().lower() or '*'
    for item in devoluciones:
        if str(item.get('purchase_id', '')) != str(purchase_id):
            continue
        if item.get('accepted') is not True:
            continue
        returned_product = str(item.get('product', '*')).strip().lower() or '*'
        if returned_product == '*' or normalized_product == '*' or returned_product == normalized_product:
            return True
    return False


def evaluate_return_request(return_data, sender):
    purchase_id = return_data.get('purchase_id') or ''
    purchase = compras_finalizadas.get(purchase_id)
    if not purchase:
        return False, 'COMPRA NO ENCONTRADA', None, [], 0.0

    requested_client = return_data.get('client_id') or sender
    if purchase.get('client_id') and requested_client and str(purchase.get('client_id')) != str(requested_client):
        return False, 'COMPRA NO PERTENECE AL CLIENTE', purchase, [], 0.0

    reason = return_data.get('reason') or ''
    if reason not in {'defectuoso', 'equivocado', 'expectativas'}:
        return False, 'MOTIVO DE DEVOLUCION INVALIDO', purchase, [], 0.0

    product_key = return_data.get('product') or ''
    items = selected_return_items(purchase, product_key)
    if not items:
        return False, 'PRODUCTO NO ENCONTRADO EN LA COMPRA', purchase, [], 0.0

    if already_returned(purchase_id, product_key):
        return False, 'DEVOLUCION YA REGISTRADA PARA ESTA COMPRA/PRODUCTO', purchase, [], 0.0

    if reason == 'expectativas':
        delivered_at = parse_datetime(purchase.get('delivery_date'))
        if not delivered_at:
            return False, 'NO CONSTA FECHA DE RECEPCION PARA DEVOLUCION POR EXPECTATIVAS', purchase, [], 0.0
        now = datetime.now()
        if now < delivered_at:
            return False, 'EL PEDIDO TODAVIA NO CONSTA COMO RECIBIDO', purchase, [], 0.0
        if now > delivered_at + timedelta(days=EXPECTATIONS_RETURN_DAYS):
            return False, 'PLAZO DE 15 DIAS SUPERADO PARA DEVOLUCION POR EXPECTATIVAS', purchase, [], 0.0

    refund = purchase_total({'items': items})
    if refund <= 0:
        return False, 'IMPORTE DE DEVOLUCION INVALIDO', purchase, items, 0.0

    return True, 'DEVOLUCION ACEPTADA', purchase, items, refund


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

    if props['performative'] == ACL['query-ref']:
        if has_type(graph, content, ECSDI.PeticionProductosCompradosUsuario):
            client_id = requested_client_id(graph, content)
            purchases = purchases_for_client(client_id)
            log(f'pedir-productos-comprados-por-usuario client={client_id or "*"} -> {len(purchases)}')
            response = build_purchases_response(
                purchases,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id
            )
            return serialize_graph(response)

        if has_type(graph, content, ECSDI.PeticionComprasSemana):
            purchases = list(compras_finalizadas.values())
            log(f'pedir-compras-que-ya-han-pasado-una-semana -> {len(purchases)}')
            response = build_purchases_response(
                purchases,
                sender=log_prefix,
                receiver=sender,
                conversation_id=conversation_id,
                content_type=ECSDI.ResultadoComprasSemana,
                message_name='envio-compras-correspondientes'
            )
            return serialize_graph(response)

        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID QUERY',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if props['performative'] != ACL.request:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PERFORMATIVE',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionGuardarCompraRealizada):
        purchase = completed_purchase_from_request(graph, content)
        store_completed_purchase(purchase)
        response = build_status_response(
            log_prefix,
            sender,
            ok=True,
            text='COMPRA HISTORICA GUARDADA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if has_type(graph, content, ECSDI.PeticionDevolucion) or has_type(graph, content, ECSDI.PeticionDevolverDinero) or has_type(graph, content, ECSDI['PeticionDevolverDineroACliente']):
        return_data = return_request_from_content(graph, content)
        ok, text, purchase, return_items, amount = evaluate_return_request(return_data, sender)
        return_data['client_id'] = return_data.get('client_id') or (purchase or {}).get('client_id') or sender
        transport = return_transport_info(return_data.get('purchase_id')) if ok else {}

        devolucion = {
            'purchase_id': return_data.get('purchase_id', ''),
            'client_id': return_data.get('client_id', ''),
            'product': return_data.get('product') or '*',
            'reason': return_data.get('reason', ''),
            'comment': return_data.get('comment', ''),
            'amount': amount,
            'accepted': ok,
            'resolution': text,
            'transportista': transport.get('transportista', ''),
            'tracking_id': transport.get('tracking_id', ''),
            'return_address': transport.get('return_address', ''),
        }
        devoluciones.append(devolucion)

        if ok:
            refund_purchase = dict(purchase)
            refund_purchase['items'] = return_items
            send_to_tesorero(build_transfer_request(
                'dev',
                amount,
                sender=log_prefix,
                receiver='TESORERO',
                participant=purchase.get('client_id', ''),
                purchase=refund_purchase,
                iban=purchase.get('client_iban', ''),
                message_name='quiero-devolver'
            ))

        response = build_return_resolution(
            ok,
            return_data,
            log_prefix,
            sender,
            conversation_id=conversation_id,
            text=text,
            amount=amount,
            transportista=transport.get('transportista', ''),
            tracking_id=transport.get('tracking_id', ''),
            return_address=transport.get('return_address', '')
        )
        return serialize_graph(response)

    if not has_type(graph, content, ECSDI.PeticionCompra):
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
        products_to_buy = {
            str(prod): int(qty)
            for prod, qty in products_from_line_request(graph, content).items()
            if int(qty) > 0
        }
    except Exception:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PRODUCTS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not products_to_buy:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID PRODUCTS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    incoming_purchase = purchase_from_content(graph, content)
    delivery_address = incoming_purchase.get('delivery_address') or delivery_address_from_purchase(graph, content)
    delivery_deadline = incoming_purchase.get('delivery_deadline') or ''
    client_id = incoming_purchase.get('client_id') or sender
    client_iban = incoming_purchase.get('client_iban') or ''
    purchase_id = incoming_purchase.get('id') or str(uuid4())

    if not delivery_address:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='DIRECCION DE ENTREGA OBLIGATORIA',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    if not client_iban:
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='DATOS BANCARIOS CLIENTE OBLIGATORIOS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    product_info = fetch_product_info(products_to_buy)
    if delivery_deadline:
        for product in product_info:
            product['delivery_deadline'] = delivery_deadline
    product_by_name = {product_name(product).lower(): product for product in product_info}
    warehouse_products = {
        product_name(product): int(product.get('quantity', products_to_buy.get(product_name(product), 1)))
        for product in product_info
        if product_name(product) and is_warehouse_managed_product(product)
    }
    fully_external_items = [
        dict(product, quantity=int(product.get('quantity', products_to_buy.get(product_name(product), 1))))
        for product in product_info
        if product_name(product) and not is_warehouse_managed_product(product)
    ]
    assigned_items = []

    log(f'Processing PeticionCompra: {products_to_buy}')
    if delivery_address:
        log(f'Delivery address: {delivery_address}')
    if delivery_deadline:
        log(f'Max delivery deadline: {delivery_deadline}')

    register_client_in_tesorero(client_id, client_iban, delivery_address)

    centros_logisticos, error = query_directory_service('CENTRO_LOGISTICO', all_agents=True)
    if error and warehouse_products:
        log('No logistics centers found in directory service')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='NO LOGISTICS CENTERS',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    remaining_warehouse = dict(warehouse_products)
    assignments_by_center = {}
    center_by_address = {
        center['address']: center
        for center in logistics_centers_by_distance(centros_logisticos, delivery_address)
    }
    sorted_centers = list(center_by_address.values())
    log(f'Logistics centers ordered by proximity: {[center["address"] for center in sorted_centers]}')

    for product, quantity in warehouse_products.items():
        for center in sorted_centers:
            centro_addr = center['address']
            try:
                exists_request = build_line_request(
                    ECSDI.PeticionExisteLineaComanda,
                    {product: quantity},
                    sender=log_prefix,
                    receiver='CENTRO_LOGISTICO',
                    performative=ACL['query-if'],
                    message_name='existe-producto',
                    delivery_address=delivery_address,
                    delivery_deadline=delivery_deadline
                )
                resp = send_graph_message(centro_addr, exists_request)
            except ConnectionError:
                log(f'Center {centro_addr} unreachable for product={product}')
                continue
            except Exception as exc:
                log(f'Center {centro_addr} returned invalid existence response for product={product}: {exc}')
                continue

            if not response_ok(resp):
                log(f'Center {centro_addr} refused existence request: {response_text(resp, "ERROR")}')
                continue

            availability = availability_from_response(resp)
            response_location = logistics_location_from_response(resp)
            if response_location.get('address') or response_location.get('lat') is not None:
                center['location'] = response_location
                center['distance_km'] = distance_from_address_km(delivery_address, response_location)
            distance = center['distance_km']
            distance_label = f'{distance:.2f} km' if distance is not None else 'distancia desconocida'
            available = bool(availability.get(product))
            log(f'Center {centro_addr} availability product={product}: {available}; distance={distance_label}')
            if available:
                assignments_by_center.setdefault(centro_addr, {})[product] = quantity
                break

        if product not in {p for assignment in assignments_by_center.values() for p in assignment.keys()}:
            log(f'No center with stock found for product={product}')

    for centro_addr, to_buy_here in assignments_by_center.items():
        try:
            center = center_by_address.get(centro_addr, {'distance_km': None})
            distance = center['distance_km']
            distance_label = f'{distance:.2f} km' if distance is not None else 'distancia desconocida'
            log(f'Assigning lot in {centro_addr} ({distance_label}): {to_buy_here}')
            assignment_payload = {}
            for product, quantity in to_buy_here.items():
                item = dict(product_by_name.get(product.lower(), {'name': product, 'price': 0.0}))
                item['quantity'] = quantity
                if delivery_deadline:
                    item['delivery_deadline'] = delivery_deadline
                assignment_payload[product] = item
            buy_request = build_lot_assignment_request(
                assignment_payload,
                sender=log_prefix,
                receiver='CENTRO_LOGISTICO',
                delivery_address=delivery_address,
                purchase_id=purchase_id,
                client_id=client_id,
                delivery_deadline=delivery_deadline
            )
            buy_resp = send_graph_message(centro_addr, buy_request)
            if response_ok(buy_resp):
                log(f'Lot assigned in {centro_addr}: {to_buy_here}')
                for product, quantity in to_buy_here.items():
                    item = dict(assignment_payload[product])
                    item['quantity'] = quantity
                    assigned_items.append(item)
                    del remaining_warehouse[product]
            else:
                log(f'Lot assignment from {centro_addr} failed: {response_text(buy_resp, "ERROR")}')
        except ConnectionError:
            log(f'Center {centro_addr} unreachable during lot assignment')
        except Exception as exc:
            log(f'Center {centro_addr} returned invalid lot assignment response: {exc}')

    if remaining_warehouse:
        log(f'Purchase incomplete, remaining warehouse-managed products: {remaining_warehouse}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text=f'PRODUCTOS SIN STOCK: {", ".join(remaining_warehouse.keys())}',
            conversation_id=conversation_id
        )
    else:
        purchase = {
            'id': purchase_id,
            'client_id': client_id,
            'client_iban': client_iban,
            'delivery_address': delivery_address,
            'delivery_deadline': delivery_deadline,
            'items': assigned_items + fully_external_items
        }
        compras[purchase_id] = purchase

        if fully_external_items:
            external_purchase = {
                'id': purchase_id,
                'client_id': client_id,
                'client_iban': client_iban,
                'delivery_address': delivery_address,
                'delivery_deadline': delivery_deadline,
                'items': fully_external_items
            }
            if not notify_external_seller(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='EMPRESA VENDEDORA EXTERNA NO DISPONIBLE',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)
            if not request_external_provider_payments(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='PAGO A VENDEDOR EXTERNO NO CONFIRMADO',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)
            if not request_client_charge(external_purchase):
                response = build_status_response(
                    log_prefix,
                    sender,
                    ok=False,
                    text='COBRO AL CLIENTE NO CONFIRMADO',
                    conversation_id=conversation_id
                )
                return serialize_graph(response)

        store_completed_purchase(purchase)
        total = purchase_total(purchase)
        notify_client_invoice(purchase, total, conversation_id=conversation_id)
        log(f'All products purchased successfully; total={total:.2f}')
        response = build_status_response(
            log_prefix,
            sender,
            ok=True,
            text='COMPRA PROCESADA; FACTURA ENVIADA AL CLIENTE',
            conversation_id=conversation_id,
        )
    return serialize_graph(response)


@app.route('/info')
def info():
    in_progress_rows = [purchase_row(purchase) for purchase in compras.values()]
    completed_rows = [purchase_row(purchase) for purchase in compras_finalizadas.values()]
    stats = [
        {'label': 'Compras en proceso', 'value': len(compras)},
        {'label': 'Compras finalizadas', 'value': len(compras_finalizadas)},
        {'label': 'Devoluciones', 'value': len(devoluciones)},
    ]
    sections = [
        table_section('Compras en proceso', in_progress_rows, empty='No hay compras en proceso'),
        table_section('Compras finalizadas', completed_rows, empty='No hay compras finalizadas'),
        table_section('Devoluciones', rows_from_sequence(devoluciones), empty='No hay devoluciones registradas'),
    ]
    return render_runtime_info('Ventas', log_prefix, stats=stats, sections=sections)


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


def query_directory_service(agent_type, all_agents=False):
    """
    Función auxiliar para enviar mensajes al servicio de directorio

    :param agent_type: tipo de agente a buscar
    :param all_agents: indica si se quieren todas las direcciones
    :return: par (direcciones, error)
    """
    try:
        resp = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=log_prefix, all_agents=all_agents)
        )
    except ConnectionError:
        return [], 'CONNECTION ERROR'
    except Exception:
        return [], 'INVALID RESPONSE'

    if not response_ok(resp):
        return [], response_text(resp, 'NOT FOUND')

    addresses = directory_addresses_from_response(resp)
    if not addresses:
        return [], 'NOT FOUND'
    return addresses, None

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
        _wlog = logging.getLogger('werkzeug')
        _wlog.setLevel(logging.ERROR)

    # Configuration stuff
    if args.port is None:
        port = 9020
    else:
        port = args.port

    if args.open:
        hostname = '0.0.0.0'
        hostaddr = args.hostaddr if args.hostaddr else gethostname()
    else:
        hostaddr = hostname = args.hostaddr if args.hostaddr else socket.gethostname()

    log_prefix = f'ventas-{port}'
    log(f'DS Hostname = {hostaddr}')

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    # Registramos el solver aritmetico en el servicio de directorio
    solveradd = f'http://{hostaddr}:{port}'
    mess = build_directory_register(log_prefix, 'VENTAS', solveradd, sender=log_prefix)

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
