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
    build_directory_search,
    build_purchase_request,
    build_search_request,
    directory_addresses_from_response,
    products_from_search_response,
    response_ok,
    response_text,
    send_graph_message,
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
iface_message = ''
has_searched = False


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
    global has_searched

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
            last_delivery_address = delivery_address

            if not assistant_proposal:
                iface_message = 'No hay propuesta del asistente para confirmar'
                return redirect(url_for('.iface'))

            if not delivery_address:
                iface_message = 'La direccion de entrega es obligatoria'
                return redirect(url_for('.iface'))

            products = {}
            for item in assistant_proposal:
                product_name = item['product']['name']
                products[product_name] = products.get(product_name, 0) + 1

            order_id, status = send_message(products, delivery_address)
            if status == 'SENT':
                iface_message = f'Pedido {order_id} enviado correctamente'
            else:
                iface_message = f'Pedido {order_id} no enviado ({status})'

            # Limpiamos la interfaz despues de solicitar el envio.
            search_groups = []
            assistant_proposal = []
            last_restrictions = [empty_restriction_row()]
            last_delivery_address = ''
            has_searched = False

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
        iface_message=iface_message,
        has_searched=has_searched
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


def send_message(products, delivery_address):
    """
    Envia una solicitud de compra al agente de ventas

    mensaje:

    PeticionCompra enviada como grafo RDF dentro de un sobre ACL FIPA.

    :param products: diccionario de productos a comprar
    :param delivery_address: direccion de entrega del cliente
    :return: order id y estado
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
        return probid, 'FAILED DS CONNECTION'
    except Exception:
        problems[probid] = [products, 'FAILED DS RESPONSE']
        log(f'{probid} invalid response from Directory Service')
        return probid, 'FAILED DS RESPONSE'

    # Agente de ventas encontrado
    if response_ok(ventas_response):
        addresses = directory_addresses_from_response(ventas_response)
        if not addresses:
            problems[probid] = [products, 'FAILED DS']
            log(f'{probid} VENTAS not found in directory service')
            return probid, 'FAILED DS'

        ventasaddr = addresses[0]
        log(f'Found VENTAS at {ventasaddr}')

        problems[probid] = [products, 'PENDING']
        mess = build_purchase_request(products, delivery_address, sender=clientid or log_prefix, receiver='VENTAS')
        log(f'Sending RDF/FIPA PeticionCompra to VENTAS')
        try:
            resp = send_graph_message(ventasaddr, mess)
            if response_ok(resp):
                problems[probid][1] = 'SENT'
                log(f'{probid} sent successfully')
                return probid, 'SENT'
            else:
                problems[probid][1] = 'FAILED VENTAS'
                log(f'{probid} VENTAS returned error: {response_text(resp, "ERROR")}')
                return probid, 'FAILED VENTAS'
        except ConnectionError:
            problems[probid][1] = 'FAILED CONNECTION'
            log(f'{probid} connection error to VENTAS at {ventasaddr}')
            return probid, 'FAILED CONNECTION'
        except Exception:
            problems[probid][1] = 'FAILED VENTAS RESPONSE'
            log(f'{probid} invalid response from VENTAS')
            return probid, 'FAILED VENTAS RESPONSE'
    # Agente de ventas no encontrado
    else:
        problems[probid] = [products, 'FAILED DS']
        log(f'{probid} VENTAS not found in directory service')
        return probid, 'FAILED DS'

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
