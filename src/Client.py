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
from FlaskServer import shutdown_server
from requests import ConnectionError
from flask import Flask, request, render_template, url_for, redirect, abort
import logging
import socket
from datetime import datetime
from GeoUtils import DEMO_CLIENT_LOCATIONS
from RuntimeInfo import render_runtime_info, rows_from_mapping, rows_from_sequence, table_section

__author__ = 'bejar'

from AgentCommunication import (
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_feedback_response,
    build_purchase_request,
    build_return_request,
    build_search_request,
    build_status_response,
    directory_addresses_from_response,
    feedback_request_from_content,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    purchase_result_from_content,
    purchase_result_total,
    products_from_search_response,
    recommendation_notice_from_content,
    return_resolution_from_content,
    response_ok,
    response_text,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
    shipping_notice_from_content,
)

app = Flask(__name__)

problems = {}
probcounter = 0
clientid = ''
diraddress = ''
log_prefix = 'client'
search_groups = []
assistant_proposal = []
last_restrictions = [
    {
        'name': '',
        'brand': '',
        'seller': '',
        'tags': '',
        'min_price': '',
        'max_price': '',
        'min_rating': ''
    }
]
last_delivery_address = ''
last_delivery_address_choice = ''
last_delivery_deadline = ''
last_client_iban = ''
iface_message = ''
has_searched = False
client_notifications = []
notification_counter = 0
last_invoice = None
client_invoices = []
invoice_counter = 0


def empty_restriction_row():
    return {
        'name': '',
        'brand': '',
        'seller': '',
        'tags': '',
        'min_price': '',
        'max_price': '',
        'min_rating': ''
    }


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def add_notification(kind, title, body, data=None, status='received'):
    global notification_counter
    notification_counter += 1
    notification = {
        'id': f'N{notification_counter:04}',
        'kind': kind,
        'title': title,
        'body': body,
        'data': data or {},
        'status': status
    }
    client_notifications.insert(0, notification)
    log(f'Notification {notification["id"]}: {title}')
    return notification


def mask_iban(iban):
    if not iban:
        return ''
    return ('****' + iban[-4:]) if len(iban) >= 4 else iban


def line_price(item):
    return float(item.get('line_price', item.get('price', 0.0)) or 0.0)


def invoice_line_total(item):
    return line_price(item) * int(item.get('quantity', 1))


def build_invoice_record(invoice_data):
    global invoice_counter
    purchase = invoice_data.get('purchase') or {}
    invoice_counter += 1
    invoice_id = f'F{invoice_counter:04}'
    items = []
    for item in purchase.get('items') or []:
        line = dict(item)
        line['quantity'] = int(line.get('quantity', 1))
        line['unit_price'] = line_price(line)
        line['line_total'] = invoice_line_total(line)
        items.append(line)

    total = float(invoice_data.get('total') or 0.0)
    if total <= 0.0:
        total = sum(item['line_total'] for item in items)

    return {
        'invoice_id': invoice_id,
        'order_id': invoice_data.get('purchase_id') or purchase.get('id') or invoice_id,
        'date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'client_id': invoice_data.get('client_id') or purchase.get('client_id') or clientid or log_prefix,
        'delivery_address': invoice_data.get('delivery_address') or purchase.get('delivery_address', ''),
        'delivery_deadline': invoice_data.get('delivery_deadline') or purchase.get('delivery_deadline', ''),
        'client_iban': mask_iban(purchase.get('client_iban', '')),
        'items': items,
        'total': total,
    }


def normalize_delivery_deadline(value):
    raw = str(value or '').strip()
    if not raw:
        return '', None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return '', 'El plazo maximo de entrega debe ser una fecha y hora validas'
    return parsed.replace(microsecond=0).isoformat(timespec='minutes'), None


def store_invoice_notification(invoice_data):
    global last_invoice
    invoice = build_invoice_record(invoice_data)
    own_client_id = clientid or log_prefix
    if invoice.get('client_id') and own_client_id and str(invoice['client_id']) != str(own_client_id):
        return None

    existing = next((item for item in client_invoices if item['order_id'] == invoice['order_id']), None)
    if existing:
        existing.update(invoice)
        invoice = existing
    else:
        client_invoices.insert(0, invoice)
    last_invoice = invoice

    body = f'Pedido {invoice["order_id"]}: factura emitida por {invoice["total"]:.2f} EUR'
    add_notification('factura', 'Factura de compra recibida', body, invoice)
    return invoice


def store_return_resolution_notification(resolution):
    accepted = bool(resolution.get('ok'))
    purchase_id = resolution.get('purchase_id') or 'compra'
    product = resolution.get('product') or 'pedido completo'
    reason = resolution.get('reason') or 'sin motivo'
    title = 'Devolucion aceptada' if accepted else 'Devolucion rechazada'
    body = resolution.get('message') or title
    if accepted:
        body = (
            f'Compra {purchase_id}, producto {product}: reembolso de '
            f'{float(resolution.get("amount") or 0.0):.2f} EUR. '
            f'Reenvia el paquete a {resolution.get("return_address") or "direccion indicada por la tienda"}'
        )
        if resolution.get('transportista'):
            body += f' mediante {resolution["transportista"]}'
        if resolution.get('tracking_id'):
            body += f' (referencia {resolution["tracking_id"]})'
    else:
        body = f'Compra {purchase_id}, producto {product}, motivo {reason}: {body}'
    return add_notification('devolucion', title, body, resolution, status='accepted' if accepted else 'rejected')


def find_agent(agent_type, all_agents=False):
    try:
        response = send_graph_message(
            diraddress,
            build_directory_search(agent_type, sender=clientid or log_prefix, all_agents=all_agents)
        )
    except ConnectionError:
        return [], 'No se puede conectar al servicio de directorio'
    except Exception:
        return [], 'Respuesta invalida del servicio de directorio'

    if not response_ok(response):
        return [], response_text(response, f'No hay agente {agent_type} registrado')

    addresses = directory_addresses_from_response(response)
    if not addresses:
        return [], f'No hay agente {agent_type} registrado'
    return addresses, None


def receive_agent_message(graph):
    try:
        props = get_message_properties(graph)
        sender = message_sender(props)
        conversation_id = message_conversation(props)
        content = props['content']
    except Exception as exc:
        log(f'Invalid incoming agent message: {exc}')
        return build_status_response(clientid or log_prefix, 'unknown', ok=False, text='INVALID RDF/FIPA MESSAGE')

    if has_type(graph, content, ECSDI.EnvioDatosEnvio):
        notice = shipping_notice_from_content(graph, content)
        body = (
            f'Compra {notice["purchase_id"]}: {notice["transportista"]} '
            f'entregara en {notice["delivery_address"]} el {notice["delivery_date"]}'
        )
        if notice.get('tracking_id'):
            body += f' (seguimiento {notice["tracking_id"]})'
        add_notification('envio', 'Datos de envio recibidos', body, notice)
        return build_status_response(clientid or log_prefix, sender, ok=True, text='DATOS ENVIO RECIBIDOS',
                                     conversation_id=conversation_id)

    if has_type(graph, content, ECSDI.ResultadoCompra):
        invoice_data = purchase_result_from_content(graph, content)
        if not invoice_data.get('ok'):
            return build_status_response(clientid or log_prefix, sender, ok=False, text='FACTURA RECHAZADA',
                                         conversation_id=conversation_id)
        invoice = store_invoice_notification(invoice_data)
        text = 'FACTURA RECIBIDA' if invoice else 'FACTURA NO DESTINADA A ESTE CLIENTE'
        return build_status_response(clientid or log_prefix, sender, ok=True, text=text,
                                     conversation_id=conversation_id)

    if has_type(graph, content, ECSDI.ResultadoDevolucion):
        resolution = return_resolution_from_content(graph, content)
        target_client = resolution.get('client_id') or ''
        own_client = clientid or log_prefix
        if target_client and own_client and str(target_client) != str(own_client):
            return build_status_response(clientid or log_prefix, sender, ok=True, text='DEVOLUCION NO DESTINADA',
                                         conversation_id=conversation_id)
        store_return_resolution_notification(resolution)
        return build_status_response(clientid or log_prefix, sender, ok=True, text='RESOLUCION DEVOLUCION RECIBIDA',
                                     conversation_id=conversation_id)

    if has_type(graph, content, ECSDI.PeticionFeedbackCliente):
        feedback = feedback_request_from_content(graph, content)
        target_client = feedback.get('client_id') or ''
        own_client = clientid or log_prefix
        if target_client and own_client and str(target_client) != str(own_client):
            return build_status_response(clientid or log_prefix, sender, ok=True, text='FEEDBACK NO DESTINADO',
                                         conversation_id=conversation_id)
        product = feedback.get('product') or {}
        product_name = product.get('name') or product.get('id') or 'producto'
        add_notification(
            'feedback',
            'Peticion de feedback',
            feedback.get('message') or f'Valora tu compra de {product_name}',
            feedback,
            status='pending'
        )
        return build_status_response(clientid or log_prefix, sender, ok=True, text='PETICION FEEDBACK RECIBIDA',
                                     conversation_id=conversation_id)

    if has_type(graph, content, ECSDI.EnvioSugerenciaProductoACliente):
        recommendation = recommendation_notice_from_content(graph, content)
        target_client = recommendation.get('client_id') or ''
        own_client = clientid or log_prefix
        if target_client and own_client and str(target_client) != str(own_client):
            return build_status_response(clientid or log_prefix, sender, ok=True, text='RECOMENDACIONES NO DESTINADAS',
                                         conversation_id=conversation_id)
        product_names = [
            product.get('name') or product.get('id') or 'producto'
            for product in recommendation.get('products') or []
        ]
        product_names_text = ', '.join(product_names)
        body = recommendation.get('message') or 'Productos recomendados'
        if product_names_text and product_names_text not in body:
            body = f'{body}: {product_names_text}'
        add_notification('recomendacion', 'Recomendaciones recibidas', body, recommendation)
        return build_status_response(clientid or log_prefix, sender, ok=True, text='RECOMENDACIONES RECIBIDAS',
                                     conversation_id=conversation_id)

    return build_status_response(clientid or log_prefix, sender, ok=False, text='MENSAJE NO SOPORTADO',
                                 conversation_id=conversation_id)


def submit_feedback_to_valorador(notification_id, product_id, rating, comment):
    addresses, error = find_agent('VALORADOR')
    if error:
        return error

    graph = build_feedback_response(
        product_id,
        rating,
        client_id=clientid or log_prefix,
        comment=comment,
        sender=clientid or log_prefix,
        receiver='VALORADOR'
    )
    try:
        response = send_graph_message(addresses[0], graph)
    except ConnectionError:
        return 'No se puede conectar con VALORADOR'
    except Exception:
        return 'Respuesta invalida de VALORADOR'

    if not response_ok(response):
        return response_text(response, 'VALORADOR rechazo el feedback')

    for notification in client_notifications:
        if notification['id'] == notification_id:
            notification['status'] = 'sent'
            notification['body'] += f' - enviado feedback {rating:.1f}/5'
            break
    return None


def submit_return_request(purchase_id, product, reason, comment):
    if not purchase_id:
        return 'No se puede solicitar devolucion sin pedido'
    if reason not in {'defectuoso', 'equivocado', 'expectativas'}:
        return 'Motivo de devolucion invalido'

    addresses, error = find_agent('VENTAS')
    if error:
        return error

    graph = build_return_request(
        purchase_id,
        product=product,
        reason=reason,
        comment=comment,
        sender=clientid or log_prefix,
        receiver='VENTAS',
        client_id=clientid or log_prefix
    )
    try:
        response = send_graph_message(addresses[0], graph)
    except ConnectionError:
        return 'No se puede conectar con VENTAS'
    except Exception:
        return 'Respuesta invalida de VENTAS'

    content = get_message_properties(response)['content']
    if has_type(response, content, ECSDI.ResultadoDevolucion):
        resolution = return_resolution_from_content(response, content)
        store_return_resolution_notification(resolution)
        return None if resolution.get('ok') else resolution.get('message') or 'Devolucion rechazada'

    if not response_ok(response):
        return response_text(response, 'VENTAS rechazo la devolucion')
    add_notification('devolucion', 'Solicitud de devolucion enviada',
                     f'Compra {purchase_id}: solicitud registrada', {'purchase_id': purchase_id})
    return None


def parse_restrictions(form):
    names = form.getlist('name')
    brands = form.getlist('brand')
    sellers = form.getlist('seller')
    tags_list = form.getlist('tags')
    min_prices = form.getlist('min_price')
    max_prices = form.getlist('max_price')
    min_ratings = form.getlist('min_rating')

    max_rows = max(
        len(names),
        len(brands),
        len(sellers),
        len(tags_list),
        len(min_prices),
        len(max_prices),
        len(min_ratings),
        1
    )

    rows = []
    entries = []

    for i in range(max_rows):
        row = {
            'name': names[i].strip() if i < len(names) else '',
            'brand': brands[i].strip() if i < len(brands) else '',
            'seller': sellers[i].strip() if i < len(sellers) else '',
            'tags': tags_list[i].strip() if i < len(tags_list) else '',
            'min_price': min_prices[i].strip() if i < len(min_prices) else '',
            'max_price': max_prices[i].strip() if i < len(max_prices) else '',
            'min_rating': min_ratings[i].strip() if i < len(min_ratings) else ''
        }
        rows.append(row)

        if not any(row.values()):
            continue

        try:
            min_price = float(row['min_price']) if row['min_price'] else None
            max_price = float(row['max_price']) if row['max_price'] else None
        except ValueError:
            return [], rows, f'Fila {i + 1}: los precios minimo y maximo deben ser numericos'

        try:
            min_rating = float(row['min_rating']) if row['min_rating'] else None
        except ValueError:
            return [], rows, f'Fila {i + 1}: la puntuacion minima debe ser numerica'

        if min_price is not None and max_price is not None and min_price > max_price:
            return [], rows, f'Fila {i + 1}: el precio minimo no puede ser mayor que el maximo'

        if min_rating is not None and (min_rating < 0.0 or min_rating > 5.0):
            return [], rows, f'Fila {i + 1}: la puntuacion minima debe estar entre 0 y 5'

        tags = [t.strip() for t in row['tags'].split(',') if t.strip()]

        entries.append({
            'row_index': i + 1,
            'raw': row,
            'filters': {
                'name': row['name'],
                'brand': row['brand'],
                'seller': row['seller'],
                'tags': tags,
                'min_price': min_price,
                'max_price': max_price,
                'min_rating': min_rating
            }
        })

    return entries, rows, None


def choose_product(results):
    if not results:
        return None

    def ranking_key(product):
        try:
            rating = float(product.get('rating', 0.0))
        except (TypeError, ValueError):
            rating = 0.0
        try:
            price = float(product.get('price', 1e12))
        except (TypeError, ValueError):
            price = 1e12
        return (-rating, price, product.get('name', ''))

    return sorted(results, key=ranking_key)[0]


@app.route("/message", methods=['GET', 'POST'])
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    global iface_message
    global search_groups
    global assistant_proposal
    global last_restrictions
    global last_delivery_address
    global last_delivery_address_choice
    global last_delivery_deadline
    global last_client_iban
    global has_searched
    global last_invoice

    if request.method == 'GET' and 'message' in request.args:
        try:
            graph = parse_graph(request.args['message'])
            response = receive_agent_message(graph)
        except Exception as exc:
            log(f'Incoming RDF message failed: {exc}')
            response = build_status_response(clientid or log_prefix, 'unknown', ok=False, text='INVALID MESSAGE')
        return serialize_graph(response)

    if request.method == 'POST':
        action = request.form.get('action', '').strip()

        if action == 'search':
            entries, rows, parse_error = parse_restrictions(request.form)
            last_restrictions = rows if rows else [empty_restriction_row()]

            if parse_error:
                iface_message = parse_error
                return redirect(url_for('.iface'))

            if not entries:
                iface_message = 'Debes introducir al menos una fila de restricciones'
                has_searched = False
                search_groups = []
                assistant_proposal = []
                return redirect(url_for('.iface'))

            groups = []
            proposal = []
            error_count = 0

            for entry in entries:
                results, error = search_products(entry['filters'])
                selected = None
                if error is None:
                    selected = choose_product(results)
                else:
                    error_count += 1

                group = {
                    'row_index': entry['row_index'],
                    'filters': entry['raw'],
                    'results': results,
                    'error': error,
                    'selected': selected
                }
                groups.append(group)

                if selected is not None:
                    proposal.append({
                        'row_index': entry['row_index'],
                        'filters': entry['raw'],
                        'product': selected
                    })

            search_groups = groups
            assistant_proposal = proposal
            has_searched = True

            if error_count == len(entries):
                iface_message = 'No se pudo buscar ninguna fila de restricciones'
            elif error_count > 0:
                iface_message = (
                    f'Se han procesado {len(entries)} filas: '
                    f'{len(assistant_proposal)} propuestas y {error_count} errores de busqueda'
                )
            else:
                iface_message = (
                    f'Se han procesado {len(entries)} filas y '
                    f'el asistente ha propuesto {len(assistant_proposal)} productos'
                )

        elif action == 'confirm_proposal':
            delivery_address_choice = request.form.get('delivery_address_choice', '').strip()
            delivery_address = request.form.get('delivery_address', '').strip()
            selected_location = next(
                (location for location in DEMO_CLIENT_LOCATIONS if location['id'] == delivery_address_choice),
                None
            )
            if selected_location is not None:
                delivery_address = selected_location['address']
            client_iban = request.form.get('client_iban', '').strip()
            delivery_deadline, deadline_error = normalize_delivery_deadline(request.form.get('delivery_deadline', ''))
            last_delivery_address = delivery_address
            last_delivery_address_choice = delivery_address_choice
            last_delivery_deadline = delivery_deadline
            last_client_iban = client_iban

            if not assistant_proposal:
                iface_message = 'No hay propuesta del asistente para confirmar'
                return redirect(url_for('.iface'))

            if not delivery_address:
                iface_message = 'La direccion de entrega es obligatoria'
                return redirect(url_for('.iface'))

            if not client_iban:
                iface_message = 'El IBAN del cliente es obligatorio para procesar el cobro'
                return redirect(url_for('.iface'))

            if deadline_error:
                iface_message = deadline_error
                return redirect(url_for('.iface'))

            products = {}
            for item in assistant_proposal:
                product_name = item['product']['name']
                products[product_name] = products.get(product_name, 0) + 1

            order_id, status, invoice_total = send_message(products, delivery_address, client_iban, delivery_deadline)
            if status == 'SENT':
                iface_message = f'Pedido {order_id} enviat correctament. Ventas enviara la factura como notificacion.'
            else:
                iface_message = f'Pedido {order_id} no enviat ({status})'

            # Limpiamos la interfaz despues de solicitar el envio.
            search_groups = []
            assistant_proposal = []
            last_restrictions = [empty_restriction_row()]
            last_delivery_address = ''
            last_delivery_address_choice = ''
            last_delivery_deadline = ''
            last_client_iban = ''
            has_searched = False

        elif action == 'submit_feedback':
            notification_id = request.form.get('notification_id', '').strip()
            product_id = request.form.get('product_id', '').strip()
            comment = request.form.get('comment', '').strip()
            try:
                rating = float(request.form.get('rating', '').strip())
            except ValueError:
                iface_message = 'La valoracion debe ser numerica'
                return redirect(url_for('.iface'))

            if rating < 0.0 or rating > 5.0:
                iface_message = 'La valoracion debe estar entre 0 y 5'
                return redirect(url_for('.iface'))

            if not product_id:
                iface_message = 'No se puede enviar feedback sin producto'
                return redirect(url_for('.iface'))

            error = submit_feedback_to_valorador(notification_id, product_id, rating, comment)
            if error:
                iface_message = f'Feedback no enviado: {error}'
            else:
                iface_message = 'Feedback enviado correctamente'

        elif action == 'request_return':
            purchase_id = request.form.get('purchase_id', '').strip()
            product = request.form.get('return_product', '').strip()
            reason = request.form.get('return_reason', '').strip()
            comment = request.form.get('return_comment', '').strip()
            error = submit_return_request(purchase_id, product, reason, comment)
            if error:
                iface_message = f'Devolucion no aceptada/enviada: {error}'
            else:
                iface_message = 'Solicitud de devolucion procesada'

        return redirect(url_for('.iface'))

    return 'OK'


@app.route('/info')
def info():
    stats = [
        {'label': 'Cliente', 'value': clientid or log_prefix},
        {'label': 'Pedidos enviados', 'value': len(problems)},
        {'label': 'Notificaciones', 'value': len(client_notifications)},
        {'label': 'Facturas', 'value': len(client_invoices)},
    ]
    proposal_rows = []
    for item in assistant_proposal:
        product = item.get('product') or {}
        proposal_rows.append({
            'row_index': item.get('row_index', ''),
            'name': product.get('name', ''),
            'brand': product.get('brand', ''),
            'seller': product.get('seller', ''),
            'price': product.get('price', ''),
            'rating': product.get('rating', ''),
        })
    sections = [
        table_section('Pedidos enviados desde la interfaz', rows_from_mapping(problems, id_key='order_id'), empty='No hay pedidos enviados'),
        table_section('Notificaciones recibidas', rows_from_sequence(client_notifications), empty='No hay notificaciones'),
        table_section('Facturas guardadas', rows_from_sequence(client_invoices), empty='No hay facturas'),
        table_section('Propuesta actual del asistente', proposal_rows, empty='No hay propuesta activa'),
    ]
    return render_runtime_info('Cliente', clientid or log_prefix, stats=stats, sections=sections)


@app.route('/iface')
def iface():
    """
    Interfaz con el cliente a traves de una pagina de web
    """
    proposal_total = sum(item['product']['price'] for item in assistant_proposal)

    return render_template(
        'iface.html',
        restriction_rows=last_restrictions,
        search_groups=search_groups,
        proposal=assistant_proposal,
        proposal_total=proposal_total,
        delivery_address=last_delivery_address,
        delivery_address_choice=last_delivery_address_choice,
        delivery_deadline=last_delivery_deadline,
        delivery_locations=DEMO_CLIENT_LOCATIONS,
        client_iban=last_client_iban,
        client_id=clientid or log_prefix,
        notifications=client_notifications,
        iface_message=iface_message,
        has_searched=has_searched,
        last_invoice=last_invoice,
        invoice_history=client_invoices,
    )


@app.route('/invoice/<invoice_id>')
def invoice_print(invoice_id):
    invoice = next((item for item in client_invoices if item['invoice_id'] == invoice_id), None)
    if invoice is None:
        abort(404)
    return render_template('invoice_print.html', invoice=invoice)


@app.route("/stop")
def stop():
    """
    Entrada que para el agente
    """
    log('Stopping server')
    shutdown_server()
    return "Parando Servidor"


def search_products(filters):
    global diraddress

    try:
        search_resp = send_graph_message(
            diraddress,
            build_directory_search('CATALOGADOR', sender=clientid or log_prefix)
        )
    except ConnectionError:
        return [], 'No se puede conectar al servicio de directorio'
    except Exception:
        return [], 'Respuesta invalida del servicio de directorio'

    if not response_ok(search_resp):
        return [], 'No hay agente CATALOGADOR registrado'

    addresses = directory_addresses_from_response(search_resp)
    if not addresses:
        return [], 'No hay agente CATALOGADOR registrado'

    catalogador_addr = addresses[0]
    mess = build_search_request(filters, sender=clientid or log_prefix, receiver='CATALOGADOR')

    try:
        resp = send_graph_message(catalogador_addr, mess)
    except ConnectionError:
        return [], 'No se puede conectar con CATALOGADOR'
    except Exception:
        return [], 'Respuesta invalida de CATALOGADOR'

    if response_ok(resp):
        return products_from_search_response(resp), None
    return [], response_text(resp, 'Respuesta de error de CATALOGADOR')


def send_message(products, delivery_address, client_iban, delivery_deadline=''):
    """
    Envia una solicitud de compra al agente de ventas

    mensaje:

    PeticionCompra enviada como grafo RDF dentro de un sobre ACL FIPA.

    :param products: diccionario de productos a comprar
    :param delivery_address: direccion de entrega del cliente
    :param client_iban: datos bancarios del cliente para el cobro
    :param delivery_deadline: fecha limite solicitada por el cliente
    :return: order id, estado y total de factura
    """
    global probcounter
    global clientid
    global diraddress
    global port
    global problems

    probid = f'{clientid}-{probcounter:03}'
    probcounter += 1

    log(f'New order {probid}: {products}')
    log(f'Delivery address for {probid}: {delivery_address}')
    if delivery_deadline:
        log(f'Max delivery deadline for {probid}: {delivery_deadline}')
    log(f'Billing data for {probid}: client_id={clientid or log_prefix}, iban={client_iban}')

    # Busca el agente de ventas en el servicio de directorio
    log('Searching for VENTAS in directory service')
    try:
        ventas_response = send_graph_message(
            diraddress,
            build_directory_search('VENTAS', sender=clientid or log_prefix)
        )
    except ConnectionError:
        problems[probid] = [products, 'FAILED DS CONNECTION']
        log(f'{probid} connection error to Directory Service')
        return probid, 'FAILED DS CONNECTION', 0.0
    except Exception:
        problems[probid] = [products, 'FAILED DS RESPONSE']
        log(f'{probid} invalid response from Directory Service')
        return probid, 'FAILED DS RESPONSE', 0.0

    # Agente de ventas encontrado
    if response_ok(ventas_response):
        addresses = directory_addresses_from_response(ventas_response)
        if not addresses:
            problems[probid] = [products, 'FAILED DS']
            log(f'{probid} VENTAS not found in directory service')
            return probid, 'FAILED DS', 0.0

        ventasaddr = addresses[0]
        log(f'Found VENTAS at {ventasaddr}')

        problems[probid] = [products, 'PENDING']
        mess = build_purchase_request(
            products,
            delivery_address,
            sender=clientid or log_prefix,
            receiver='VENTAS',
            client_id=clientid or log_prefix,
            client_iban=client_iban,
            delivery_deadline=delivery_deadline
        )
        log(f'Sending RDF/FIPA PeticionCompra to VENTAS')
        try:
            resp = send_graph_message(ventasaddr, mess)
            if response_ok(resp):
                invoice_total = purchase_result_total(resp, 0.0)
                if invoice_total > 0:
                    problems[probid][1] = f'SENT - factura {invoice_total:.2f} EUR'
                    log(f'{probid} sent successfully; invoice={invoice_total:.2f} EUR')
                else:
                    problems[probid][1] = 'SENT - factura pendiente/notificada'
                    log(f'{probid} sent successfully; invoice will arrive as notification')
                return probid, 'SENT', invoice_total
            else:
                problems[probid][1] = 'FAILED VENTAS'
                log(f'{probid} VENTAS returned error: {response_text(resp, "ERROR")}')
                return probid, 'FAILED VENTAS', 0.0
        except ConnectionError:
            problems[probid][1] = 'FAILED CONNECTION'
            log(f'{probid} connection error to VENTAS at {ventasaddr}')
            return probid, 'FAILED CONNECTION', 0.0
        except Exception:
            problems[probid][1] = 'FAILED VENTAS RESPONSE'
            log(f'{probid} invalid response from VENTAS')
            return probid, 'FAILED VENTAS RESPONSE', 0.0
    # Agente de ventas no encontrado
    else:
        problems[probid] = [products, 'FAILED DS']
        log(f'{probid} VENTAS not found in directory service')
        return probid, 'FAILED DS', 0.0

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
    clientid = log_prefix  # use descriptive ID so the tracer shows 'CLIENT-9001' not '127-9001'

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    else:
        diraddress = args.dir

    log(f'Starting at {clientadd}, directory={diraddress}')

    register_message = build_directory_register(clientid, 'CLIENTE', clientadd, sender=clientid)
    done = False
    while not done:
        try:
            register_response = send_graph_message(diraddress, register_message)
            done = True
        except ConnectionError:
            pass

    if response_ok(register_response):
        log(f'{clientid} successfully registered')
        # Try to connect to Logger for packet tracing
        try:
            _lr = send_graph_message(diraddress, build_directory_search('LOGGER', sender=clientid))
            if response_ok(_lr):
                _la = directory_addresses_from_response(_lr)
                if _la:
                    set_tracer_url(_la[0])
                    log(f'Packet tracing enabled → {_la[0]}')
        except Exception:
            pass
        app.run(host=hostname, port=port, debug=False, use_reloader=False)

        log(f'{clientid} unregistering')
        unregister_message = build_directory_unregister(clientid, sender=clientid)
        send_graph_message(diraddress, unregister_message)
    else:
        log('Unable to register client')
