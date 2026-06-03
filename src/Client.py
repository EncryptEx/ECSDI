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
from flask import Flask, request, render_template, url_for, redirect
import logging
import socket

__author__ = 'bejar'

from AgentCommunication import (
    ECSDI,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_feedback_response,
    build_purchase_request,
    build_search_request,
    build_status_response,
    directory_addresses_from_response,
    feedback_request_from_content,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    purchase_result_total,
    products_from_search_response,
    recommendation_notice_from_content,
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
last_client_iban = ''
iface_message = ''
has_searched = False
client_notifications = []
notification_counter = 0
last_invoice = None


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

    if has_type(graph, content, ECSDI.PeticionFeedbackCliente):
        feedback = feedback_request_from_content(graph, content)
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
        product_names = [
            product.get('name') or product.get('id') or 'producto'
            for product in recommendation.get('products') or []
        ]
        body = recommendation.get('message') or 'Productos recomendados: ' + ', '.join(product_names)
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
            delivery_address = request.form.get('delivery_address', '').strip()
            client_iban = request.form.get('client_iban', '').strip()
            last_delivery_address = delivery_address
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

            products = {}
            for item in assistant_proposal:
                product_name = item['product']['name']
                products[product_name] = products.get(product_name, 0) + 1

            order_id, status, invoice_total = send_message(products, delivery_address, client_iban)
            if status == 'SENT':
                iface_message = f'Pedido {order_id} enviat correctament. Factura: {invoice_total:.2f} EUR'
                from datetime import datetime
                last_invoice = {
                    'order_id': order_id,
                    'date': datetime.now().strftime('%d/%m/%Y %H:%M'),
                    'client_id': clientid or log_prefix,
                    'delivery_address': delivery_address,
                    'client_iban': ('****' + client_iban[-4:]) if len(client_iban) >= 4 else client_iban,
                    'items': [
                        {
                            'name': item['product'].get('name', ''),
                            'brand': item['product'].get('brand', ''),
                            'seller': item['product'].get('seller', ''),
                            'price': float(item['product'].get('price', 0.0) or 0.0),
                        }
                        for item in assistant_proposal
                    ],
                    'total': invoice_total,
                }
            else:
                iface_message = f'Pedido {order_id} no enviat ({status})'

            # Limpiamos la interfaz despues de solicitar el envio.
            search_groups = []
            assistant_proposal = []
            last_restrictions = [empty_restriction_row()]
            last_delivery_address = ''
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

        return redirect(url_for('.iface'))

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
    proposal_total = sum(item['product']['price'] for item in assistant_proposal)

    return render_template(
        'iface.html',
        restriction_rows=last_restrictions,
        search_groups=search_groups,
        proposal=assistant_proposal,
        proposal_total=proposal_total,
        delivery_address=last_delivery_address,
        client_iban=last_client_iban,
        client_id=clientid or log_prefix,
        notifications=client_notifications,
        iface_message=iface_message,
        has_searched=has_searched,
        last_invoice=last_invoice,
    )


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


def send_message(products, delivery_address, client_iban):
    """
    Envia una solicitud de compra al agente de ventas

    mensaje:

    PeticionCompra enviada como grafo RDF dentro de un sobre ACL FIPA.

    :param products: diccionario de productos a comprar
    :param delivery_address: direccion de entrega del cliente
    :param client_iban: datos bancarios del cliente para el cobro
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
            client_iban=client_iban
        )
        log(f'Sending RDF/FIPA PeticionCompra to VENTAS')
        try:
            resp = send_graph_message(ventasaddr, mess)
            if response_ok(resp):
                invoice_total = purchase_result_total(resp, 0.0)
                problems[probid][1] = f'SENT - factura {invoice_total:.2f} EUR'
                log(f'{probid} sent successfully; invoice={invoice_total:.2f} EUR')
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
