"""
Shared FIPA ACL and RDF helpers for the agents.

The transport is still HTTP GET over /message, but the value of the message
parameter is always a serialized RDF graph. The graph contains a FIPA ACL
envelope and RDF content typed with the system ontology.
"""
from uuid import uuid4
import re

import requests
from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import XSD


ACL = Namespace('http://www.nuin.org/ontology/fipa/acl#')
ECSDI = Namespace('http://www.semanticweb.org/jaume/ontologies/2026/ECSDI#')
DSO = Namespace('http://www.agentes.org/ontology/directory-service#')

RDF_FORMAT = 'xml'
DEFAULT_AGENT = 'agent'
DIRECTORY_AGENT = 'DirectoryService'


def bind_namespaces(graph):
    graph.bind('acl', ACL)
    graph.bind('ecsdi', ECSDI)
    graph.bind('dso', DSO)
    graph.bind('rdf', RDF)
    graph.bind('xsd', XSD)
    return graph


def new_graph():
    return bind_namespaces(Graph())


def serialize_graph(graph):
    return graph.serialize(format=RDF_FORMAT)


def parse_graph(message):
    graph = new_graph()
    graph.parse(data=message, format=RDF_FORMAT)
    return graph


def _uri(namespace, local):
    safe_local = re.sub(r'[^A-Za-z0-9_-]+', '_', str(local)).strip('_') or 'node'
    return namespace[f'{safe_local}_{uuid4().hex}']


def _as_literal(value, datatype=None):
    if datatype is None:
        return Literal(value)
    return Literal(value, datatype=datatype)


def _performative_uri(performative):
    if isinstance(performative, URIRef):
        return performative
    return ACL[str(performative)]


def _content_type_uri(content_type):
    if isinstance(content_type, URIRef):
        return content_type
    return ECSDI[str(content_type)]


def build_acl_message(graph, performative, sender, receiver, content, conversation_id=None):
    message = _uri(ACL, 'message')
    conversation = conversation_id or str(uuid4())

    graph.add((message, RDF.type, ACL.FipaAclMessage))
    graph.add((message, ACL.performative, _performative_uri(performative)))
    graph.add((message, ACL.sender, Literal(sender or DEFAULT_AGENT)))
    graph.add((message, ACL.receiver, Literal(receiver or DEFAULT_AGENT)))
    graph.add((message, ACL.content, content))
    graph.add((message, ACL['conversation-id'], Literal(conversation)))
    return graph


def build_message_with_content(content_type, performative=ACL.request, sender=DEFAULT_AGENT,
                               receiver=DEFAULT_AGENT, conversation_id=None):
    graph = new_graph()
    content = _uri(ECSDI, str(content_type).split('#')[-1])
    graph.add((content, RDF.type, _content_type_uri(content_type)))
    build_acl_message(graph, performative, sender, receiver, content, conversation_id)
    return graph, content


def get_message_properties(graph):
    message = next(graph.subjects(RDF.type, ACL.FipaAclMessage), None)
    if message is None:
        message = next(graph.subjects(ACL.performative, None), None)
    if message is None:
        raise ValueError('RDF graph does not contain a FIPA ACL message')

    return {
        'message': message,
        'performative': graph.value(message, ACL.performative),
        'sender': graph.value(message, ACL.sender),
        'receiver': graph.value(message, ACL.receiver),
        'content': graph.value(message, ACL.content),
        'conversation_id': graph.value(message, ACL['conversation-id']),
    }


def message_performative(graph):
    return get_message_properties(graph)['performative']


def message_sender(props):
    sender = props.get('sender')
    return str(sender) if sender is not None else DEFAULT_AGENT


def message_conversation(props):
    conversation = props.get('conversation_id')
    return str(conversation) if conversation is not None else None


def has_type(graph, node, rdf_type):
    return (node, RDF.type, rdf_type) in graph


def first_literal(graph, subject, predicate, default=None):
    value = graph.value(subject, predicate)
    if value is None:
        return default
    return str(value)


def literal_values(graph, subject, predicate):
    return [str(value) for value in graph.objects(subject, predicate)]


def first_int(graph, subject, predicate, default=None):
    value = graph.value(subject, predicate)
    if value is None:
        return default
    return int(value)


def first_float(graph, subject, predicate, default=None):
    value = graph.value(subject, predicate)
    if value is None:
        return default
    return float(value)


def first_bool(graph, subject, predicate, default=False):
    value = graph.value(subject, predicate)
    if value is None:
        return default
    return bool(value.toPython()) if hasattr(value, 'toPython') else str(value).lower() == 'true'


def send_graph_message(address, graph, timeout=None):
    response = requests.get(
        address + '/message',
        params={'message': serialize_graph(graph)},
        timeout=timeout
    )
    return parse_graph(response.text)


def build_status_response(sender, receiver, ok=True, text='OK', conversation_id=None):
    graph, content = build_message_with_content(
        ECSDI.Respuesta,
        performative=ACL.inform if ok else ACL.refuse,
        sender=sender,
        receiver=receiver,
        conversation_id=conversation_id
    )
    graph.add((content, ECSDI.resultado, Literal(text)))
    return graph


def response_ok(graph):
    props = get_message_properties(graph)
    if props['performative'] == ACL.inform:
        return True
    content = props['content']
    return first_bool(graph, content, ECSDI.existe, False)


def response_text(graph, default=''):
    content = get_message_properties(graph)['content']
    return first_literal(graph, content, ECSDI.resultado, default)


def build_directory_register(agent_id, agent_type, address, sender, receiver=DIRECTORY_AGENT):
    graph = new_graph()
    content = _uri(DSO, 'Register')
    graph.add((content, RDF.type, DSO.Register))
    graph.add((content, DSO.AgentID, Literal(agent_id)))
    graph.add((content, DSO.AgentType, Literal(agent_type)))
    graph.add((content, DSO.Address, Literal(address)))
    build_acl_message(graph, ACL.request, sender, receiver, content)
    return graph


def build_directory_unregister(agent_id, sender, receiver=DIRECTORY_AGENT):
    graph = new_graph()
    content = _uri(DSO, 'Unregister')
    graph.add((content, RDF.type, DSO.Unregister))
    graph.add((content, DSO.AgentID, Literal(agent_id)))
    build_acl_message(graph, ACL.request, sender, receiver, content)
    return graph


def build_directory_search(agent_type, sender, receiver=DIRECTORY_AGENT, all_agents=False):
    graph = new_graph()
    content = _uri(DSO, 'SearchAll' if all_agents else 'Search')
    graph.add((content, RDF.type, DSO.SearchAll if all_agents else DSO.Search))
    graph.add((content, DSO.AgentType, Literal(agent_type)))
    build_acl_message(graph, ACL.request, sender, receiver, content)
    return graph


def directory_register_values(graph, content):
    return (
        first_literal(graph, content, DSO.AgentID),
        first_literal(graph, content, DSO.AgentType),
        first_literal(graph, content, DSO.Address),
    )


def directory_search_type(graph, content):
    return first_literal(graph, content, DSO.AgentType)


def directory_unregister_id(graph, content):
    return first_literal(graph, content, DSO.AgentID)


def build_directory_search_response(addresses, sender, receiver, agent_type=None,
                                    conversation_id=None):
    graph = new_graph()
    content = _uri(DSO, 'SearchResult')
    graph.add((content, RDF.type, DSO.SearchResult))
    if agent_type is not None:
        graph.add((content, DSO.AgentType, Literal(agent_type)))
    for address in addresses:
        graph.add((content, DSO.Address, Literal(address)))
    build_acl_message(graph, ACL.inform, sender, receiver, content, conversation_id)
    return graph


def directory_addresses_from_response(graph):
    content = get_message_properties(graph)['content']
    return literal_values(graph, content, DSO.Address)


def add_product(graph, product, subject=None):
    pid = str(product.get('id') or product.get('idProducto') or '').strip()
    name = str(product.get('name') or product.get('nombreProducto') or '').strip()
    subject = subject or _uri(ECSDI, f'Producto_{pid or name or "anon"}')

    graph.add((subject, RDF.type, ECSDI.Producto))
    if pid:
        graph.add((subject, ECSDI.idProducto, Literal(pid)))
    if name:
        graph.add((subject, ECSDI.nombreProducto, Literal(name)))
    if product.get('brand') or product.get('nombreMarca'):
        graph.add((subject, ECSDI.nombreMarca, Literal(product.get('brand') or product.get('nombreMarca'))))
    if product.get('seller') or product.get('vendedorProducto'):
        graph.add((subject, ECSDI.vendedorProducto, Literal(product.get('seller') or product.get('vendedorProducto'))))
    if product.get('price') is not None:
        graph.add((subject, ECSDI.precioProducto, Literal(float(product.get('price')), datatype=XSD.float)))
    if product.get('rating') is not None:
        graph.add((subject, ECSDI.valoracionProducto, Literal(float(product.get('rating')), datatype=XSD.float)))

    tags = product.get('tags') or product.get('caracteristicasProducto') or []
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        if str(tag).strip():
            graph.add((subject, ECSDI.caracteristicasProducto, Literal(str(tag).strip())))
    return subject


def product_from_graph(graph, subject):
    product = {}
    pid = first_literal(graph, subject, ECSDI.idProducto)
    name = first_literal(graph, subject, ECSDI.nombreProducto)
    brand = first_literal(graph, subject, ECSDI.nombreMarca)
    seller = first_literal(graph, subject, ECSDI.vendedorProducto)
    price = first_float(graph, subject, ECSDI.precioProducto)
    rating = first_float(graph, subject, ECSDI.valoracionProducto)
    tags = literal_values(graph, subject, ECSDI.caracteristicasProducto)

    if pid is not None:
        product['id'] = pid
    if name is not None:
        product['name'] = name
    if brand is not None:
        product['brand'] = brand
    if seller is not None:
        product['seller'] = seller
    if price is not None:
        product['price'] = price
    if tags:
        product['tags'] = tags
    if rating is not None:
        product['rating'] = rating
    return product


def build_search_request(filters, sender, receiver):
    graph, content = build_message_with_content(ECSDI.PeticionCerca, sender=sender, receiver=receiver)

    if filters.get('name'):
        graph.add((content, ECSDI.nombreProducto, Literal(filters['name'])))
    if filters.get('brand'):
        graph.add((content, ECSDI.nombreMarca, Literal(filters['brand'])))
    if filters.get('seller'):
        graph.add((content, ECSDI.vendedorProducto, Literal(filters['seller'])))
    for tag in filters.get('tags') or []:
        graph.add((content, ECSDI.caracteristicasProducto, Literal(tag)))
    if filters.get('min_price') is not None:
        graph.add((content, ECSDI.precioProductoMinimo, Literal(float(filters['min_price']), datatype=XSD.float)))
    if filters.get('max_price') is not None:
        graph.add((content, ECSDI.precioProductoMaximo, Literal(float(filters['max_price']), datatype=XSD.float)))
    if filters.get('min_rating') is not None:
        graph.add((content, ECSDI.valoracionProducto, Literal(float(filters['min_rating']), datatype=XSD.float)))
    return graph


def filters_from_search_request(graph, content):
    return {
        'name': first_literal(graph, content, ECSDI.nombreProducto, ''),
        'brand': first_literal(graph, content, ECSDI.nombreMarca, ''),
        'seller': first_literal(graph, content, ECSDI.vendedorProducto, ''),
        'tags': literal_values(graph, content, ECSDI.caracteristicasProducto),
        'min_price': first_float(graph, content, ECSDI.precioProductoMinimo),
        'max_price': first_float(graph, content, ECSDI.precioProductoMaximo),
        'min_rating': first_float(graph, content, ECSDI.valoracionProducto),
    }


def build_search_response(products, sender, receiver, conversation_id=None):
    graph, content = build_message_with_content(
        ECSDI.ResultadoCerca,
        performative=ACL.inform,
        sender=sender,
        receiver=receiver,
        conversation_id=conversation_id
    )
    for product in products:
        product_node = add_product(graph, product)
        graph.add((content, ECSDI.contiene_productos, product_node))
    return graph


def products_from_search_response(graph):
    content = get_message_properties(graph)['content']
    return [product_from_graph(graph, product) for product in graph.objects(content, ECSDI.contiene_productos)]


def build_ratings_request(product_ids, sender, receiver):
    graph, content = build_message_with_content(ECSDI.PeticionValoracionesProducto, sender=sender, receiver=receiver)
    for product_id in product_ids:
        product_node = add_product(graph, {'id': product_id})
        graph.add((content, ECSDI.contiene_productos, product_node))
    return graph


def product_ids_from_ratings_request(graph, content):
    product_ids = []
    for product in graph.objects(content, ECSDI.contiene_productos):
        pid = first_literal(graph, product, ECSDI.idProducto)
        if pid:
            product_ids.append(pid)
    return product_ids


def build_ratings_response(ratings, sender, receiver, conversation_id=None):
    graph, content = build_message_with_content(
        ECSDI.ResultadoValoracionesProducto,
        performative=ACL.inform,
        sender=sender,
        receiver=receiver,
        conversation_id=conversation_id
    )
    for product_id, rating in ratings.items():
        rating_node = _uri(ECSDI, f'ValoracionProducto_{product_id}')
        graph.add((rating_node, RDF.type, ECSDI.ValoracionProducto))
        graph.add((rating_node, ECSDI.idProducto, Literal(product_id)))
        graph.add((rating_node, ECSDI.valoracionProducto, Literal(float(rating), datatype=XSD.float)))
        graph.add((content, ECSDI.ContieneValoracionesProductos, rating_node))
    return graph


def ratings_from_response(graph):
    content = get_message_properties(graph)['content']
    ratings = {}
    for rating_node in graph.objects(content, ECSDI.ContieneValoracionesProductos):
        product_id = first_literal(graph, rating_node, ECSDI.idProducto)
        rating = first_float(graph, rating_node, ECSDI.valoracionProducto)
        if product_id is not None and rating is not None:
            ratings[product_id] = rating
    return ratings


def _add_line(graph, compra_node, product_name, quantity):
    line = _uri(ECSDI, 'LineaComanda')
    product = add_product(graph, {'name': product_name})
    graph.add((line, RDF.type, ECSDI.LineaComanda))
    graph.add((line, ECSDI.cantidadProducto, Literal(int(quantity), datatype=XSD.int)))
    graph.add((line, ECSDI.contiene_productos, product))
    graph.add((compra_node, ECSDI.contiene_lineas, line))
    return line


def build_purchase_request(products, delivery_address, sender, receiver):
    graph, content = build_message_with_content(ECSDI.PeticionCompra, sender=sender, receiver=receiver)
    compra = _uri(ECSDI, 'Compra')
    graph.add((compra, RDF.type, ECSDI.Compra))
    graph.add((content, ECSDI.contiene_compra, compra))
    if delivery_address:
        graph.add((compra, ECSDI.direccion, Literal(delivery_address)))
    for product_name, quantity in products.items():
        _add_line(graph, compra, product_name, quantity)
    return graph


def build_line_request(content_type, products, sender, receiver):
    graph, content = build_message_with_content(content_type, sender=sender, receiver=receiver)
    compra = _uri(ECSDI, 'Compra')
    graph.add((compra, RDF.type, ECSDI.Compra))
    graph.add((content, ECSDI.contiene_compra, compra))
    for product_name, quantity in products.items():
        _add_line(graph, compra, product_name, quantity)
    return graph


def products_from_line_request(graph, content):
    compra = graph.value(content, ECSDI.contiene_compra)
    if compra is None:
        return {}
    products = {}
    for line in graph.objects(compra, ECSDI.contiene_lineas):
        quantity = first_int(graph, line, ECSDI.cantidadProducto, 0)
        product_node = graph.value(line, ECSDI.contiene_productos)
        if product_node is None:
            continue
        name = first_literal(graph, product_node, ECSDI.nombreProducto)
        pid = first_literal(graph, product_node, ECSDI.idProducto)
        key = name or pid
        if key:
            products[key] = quantity
    return products


def delivery_address_from_purchase(graph, content):
    compra = graph.value(content, ECSDI.contiene_compra)
    if compra is None:
        return ''
    return first_literal(graph, compra, ECSDI.direccion, '')


def build_existence_response(availability, quantities, sender, receiver, conversation_id=None):
    graph, content = build_message_with_content(
        ECSDI.ResultadoExistenciaLineasComanda,
        performative=ACL.inform,
        sender=sender,
        receiver=receiver,
        conversation_id=conversation_id
    )
    for product_name, exists in availability.items():
        existencia = _uri(ECSDI, 'ExistenciaLineaComanda')
        line = _uri(ECSDI, 'LineaComanda')
        product = add_product(graph, {'name': product_name})

        graph.add((existencia, RDF.type, ECSDI.ExistenciaLineaComanda))
        graph.add((existencia, ECSDI.existe, Literal(bool(exists), datatype=XSD.boolean)))
        graph.add((existencia, ECSDI.contiene_linea, line))
        graph.add((line, RDF.type, ECSDI.LineaComanda))
        graph.add((line, ECSDI.cantidadProducto, Literal(int(quantities.get(product_name, 0)), datatype=XSD.int)))
        graph.add((line, ECSDI.contiene_productos, product))
        graph.add((content, ECSDI.contiene_existencia_linea_comandas, existencia))
    return graph


def availability_from_response(graph):
    content = get_message_properties(graph)['content']
    availability = {}
    for existencia in graph.objects(content, ECSDI.contiene_existencia_linea_comandas):
        exists = first_bool(graph, existencia, ECSDI.existe, False)
        line = graph.value(existencia, ECSDI.contiene_linea)
        if line is None:
            continue
        product_node = graph.value(line, ECSDI.contiene_productos)
        if product_node is None:
            continue
        name = first_literal(graph, product_node, ECSDI.nombreProducto)
        pid = first_literal(graph, product_node, ECSDI.idProducto)
        key = name or pid
        if key:
            availability[key] = exists
    return availability


def build_purchase_result(ok, sender, receiver, conversation_id=None, total=0.0):
    graph, content = build_message_with_content(
        ECSDI.ResultadoCompra,
        performative=ACL.inform if ok else ACL.refuse,
        sender=sender,
        receiver=receiver,
        conversation_id=conversation_id
    )
    factura = _uri(ECSDI, 'FacturaCompra')
    graph.add((factura, RDF.type, ECSDI.FacturaCompra))
    graph.add((factura, ECSDI.precioTotalFactura, Literal(float(total), datatype=XSD.float)))
    graph.add((content, ECSDI.contiene_factura, factura))
    graph.add((content, ECSDI.existe, Literal(bool(ok), datatype=XSD.boolean)))
    return graph
