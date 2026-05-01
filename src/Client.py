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
search_results = []
last_filters = {
    'name': '',
    'brand': '',
    'seller': '',
    'tags': '',
    'min_price': '',
    'max_price': ''
}
cart = {}
iface_message = ''
has_searched = False


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


@app.route("/message", methods=['GET', 'POST'])
def message():
    """
    Entrypoint para todas las comunicaciones

    :return:
    """
    global iface_message
    global search_results
    global cart
    global last_filters
    global has_searched

    if request.method == 'POST':
        action = request.form.get('action', '').strip()

        if action == 'search':
            name = request.form.get('name', '').strip()
            brand = request.form.get('brand', '').strip()
            seller = request.form.get('seller', '').strip()
            tags_raw = request.form.get('tags', '').strip()
            min_price = request.form.get('min_price', '').strip()
            max_price = request.form.get('max_price', '').strip()

            tags = [t.strip() for t in tags_raw.split(',') if t.strip()]

            try:
                min_price_value = float(min_price) if min_price else None
                max_price_value = float(max_price) if max_price else None
            except ValueError:
                iface_message = 'Los precios minimo y maximo deben ser numericos'
                return redirect(url_for('.iface'))

            filters = {
                'name': name,
                'brand': brand,
                'seller': seller,
                'tags': tags,
                'min_price': min_price_value,
                'max_price': max_price_value
            }

            last_filters = {
                'name': name,
                'brand': brand,
                'seller': seller,
                'tags': tags_raw,
                'min_price': min_price,
                'max_price': max_price
            }

            results, error = search_products(filters)
            has_searched = True
            if error:
                search_results = []
                iface_message = f'Error de busqueda: {error}'
            else:
                search_results = results
                iface_message = f'Se han encontrado {len(search_results)} productos'

        elif action == 'add_to_cart':
            product_name = request.form.get('product_name', '').strip()
            brand = request.form.get('brand', '').strip()
            seller = request.form.get('seller', '').strip()
            tags = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]

            try:
                price = float(request.form.get('price', '0'))
                quantity = int(request.form.get('quantity', '1'))
            except ValueError:
                iface_message = 'Cantidad o precio invalidos'
                return redirect(url_for('.iface'))

            if quantity <= 0:
                iface_message = 'La cantidad debe ser mayor que cero'
                return redirect(url_for('.iface'))

            if product_name in cart:
                cart[product_name]['quantity'] += quantity
            else:
                cart[product_name] = {
                    'name': product_name,
                    'brand': brand,
                    'seller': seller,
                    'price': price,
                    'tags': tags,
                    'quantity': quantity
                }

            iface_message = f'Anadido al carrito: {product_name} x{quantity}'

        elif action == 'update_cart_item':
            product_name = request.form.get('product_name', '').strip()
            try:
                quantity = int(request.form.get('quantity', '1'))
            except ValueError:
                iface_message = 'Cantidad invalida'
                return redirect(url_for('.iface'))

            if product_name in cart:
                if quantity <= 0:
                    del cart[product_name]
                    iface_message = f'Producto eliminado del carrito: {product_name}'
                else:
                    cart[product_name]['quantity'] = quantity
                    iface_message = f'Cantidad actualizada: {product_name} x{quantity}'

        elif action == 'remove_cart_item':
            product_name = request.form.get('product_name', '').strip()
            if product_name in cart:
                del cart[product_name]
                iface_message = f'Producto eliminado del carrito: {product_name}'

        elif action == 'checkout':
            delivery_address = request.form.get('delivery_address', '').strip()
            if not cart:
                iface_message = 'No se puede tramitar el pedido con el carrito vacio'
                return redirect(url_for('.iface'))

            if not delivery_address:
                iface_message = 'La direccion de entrega es obligatoria'
                return redirect(url_for('.iface'))

            products = {name: data['quantity'] for name, data in cart.items()}
            order_id, status = send_message(products, delivery_address)
            if status == 'SENT':
                cart = {}
                iface_message = f'Pedido {order_id} enviado correctamente'
            else:
                iface_message = f'Pedido {order_id} no enviado ({status})'

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
    cart_total = sum(item['price'] * item['quantity'] for item in cart.values())
    return render_template(
        'iface.html',
        search_results=search_results,
        cart=cart,
        cart_total=cart_total,
        filters=last_filters,
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
        search_resp = requests.get(diraddress + '/message', params={'message': 'SEARCH|CERCADOR'}).text
    except ConnectionError:
        return [], 'No se puede conectar al servicio de directorio'

    if 'OK' not in search_resp:
        return [], 'No hay agente CERCADOR registrado'

    cercador_addr = search_resp[4:]
    mess = f'BUSCAR_PRODUCTOS|{json.dumps(filters)}'

    try:
        resp = requests.get(cercador_addr + '/message', params={'message': mess}).text
    except ConnectionError:
        return [], 'No se puede conectar con CERCADOR'

    if 'OK: ' in resp:
        try:
            return json.loads(resp[4:]), None
        except json.JSONDecodeError:
            return [], 'Respuesta invalida de CERCADOR'
    return [], resp


def send_message(products, delivery_address):
    """
    Envia una solicitud de compra al agente de ventas

    mensaje:

    PRODUCTOS_A_COMPRAR|{"products": {"product": quantity, ...}, "delivery_address": "..."}

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
        ventasaddr = requests.get(diraddress + '/message', params={'message': 'SEARCH|VENTAS'}).text
    except ConnectionError:
        problems[probid] = [products, 'FAILED DS CONNECTION']
        log(f'{probid} connection error to Directory Service')
        return probid, 'FAILED DS CONNECTION'

    # Agente de ventas encontrado
    if 'OK' in ventasaddr:
        ventasaddr = ventasaddr[4:]
        log(f'Found VENTAS at {ventasaddr}')

        problems[probid] = [products, 'PENDING']
        payload = {
            'products': products,
            'delivery_address': delivery_address
        }
        mess = f'PRODUCTOS_A_COMPRAR|{json.dumps(payload)}'
        log(f'Sending to VENTAS: {mess}')
        try:
            resp = requests.get(ventasaddr + '/message', params={'message': mess}).text
            if 'ERROR' not in resp:
                problems[probid][1] = 'SENT'
                log(f'{probid} sent successfully')
                return probid, 'SENT'
            else:
                problems[probid][1] = 'FAILED VENTAS'
                log(f'{probid} VENTAS returned error: {resp}')
                return probid, 'FAILED VENTAS'
        except ConnectionError:
            problems[probid][1] = 'FAILED CONNECTION'
            log(f'{probid} connection error to VENTAS at {ventasaddr}')
            return probid, 'FAILED CONNECTION'
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
