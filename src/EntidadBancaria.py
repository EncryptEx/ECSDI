"""
External banking entity agent.

It represents the external actor that receives payment/refund requests from
Tesorero and returns the banking confirmation perception.
"""
import argparse
import json
import logging
import random
import socket

from FlaskServer import shutdown_server
from Util import gethostname
from flask import Flask, request
from requests import ConnectionError

from AgentCommunication import (
    ACL,
    ECSDI,
    build_bank_transfer_response,
    build_directory_register,
    build_directory_search,
    build_directory_unregister,
    build_status_response,
    directory_addresses_from_response,
    get_message_properties,
    has_type,
    message_conversation,
    message_sender,
    parse_graph,
    response_ok,
    send_graph_message,
    serialize_graph,
    set_tracer_url,
    transfer_from_request,
)


app = Flask(__name__)

log_prefix = 'banco'
diraddress = ''
BANK_NAME = 'Entidad Bancaria Demo'
FAILURE_RATE = 0.0
TRANSFERENCIAS = []


def log(msg):
    print(f'[{log_prefix}] {msg}', flush=True)


def load_bank_config(path):
    if not path:
        return {}
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)
    return data.get('entidad_bancaria', {})


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

    if props['performative'] != ACL.request or not has_type(graph, content, ECSDI.PeticionBancariaDeTransferencia):
        response = build_status_response(
            log_prefix,
            sender,
            ok=False,
            text='INVALID BANK REQUEST',
            conversation_id=conversation_id
        )
        return serialize_graph(response)

    transfer = transfer_from_request(graph, content)
    ok = random.random() >= FAILURE_RATE
    transfer_record = dict(transfer)
    transfer_record['status'] = 'confirmed' if ok else 'rejected'
    TRANSFERENCIAS.append(transfer_record)
    text = 'TRANSFERENCIA CONFIRMADA' if ok else 'TRANSFERENCIA RECHAZADA'
    log(f'{text}: tipo={transfer["kind"]} importe={transfer["amount"]:.2f}')
    response = build_bank_transfer_response(
        ok,
        transfer,
        sender=log_prefix,
        receiver=sender,
        conversation_id=conversation_id,
        text=text
    )
    return serialize_graph(response)


@app.route('/stop')
def stop():
    log('Stopping server')
    shutdown_server()
    return 'Parando Servidor'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true', default=False)
    parser.add_argument('--verbose', action='store_true', default=False)
    parser.add_argument('--port', type=int, default=9080)
    parser.add_argument('--dir', default=None)
    parser.add_argument('--hostaddr', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--name', default=None)
    parser.add_argument('--failure-rate', type=float, default=None)
    args = parser.parse_args()

    if not args.verbose:
        logging.getLogger('werkzeug').setLevel(logging.ERROR)

    cfg = load_bank_config(args.config)
    BANK_NAME = args.name or cfg.get('name', BANK_NAME)
    FAILURE_RATE = float(args.failure_rate if args.failure_rate is not None else cfg.get('failure_rate', FAILURE_RATE))

    hostname = '0.0.0.0' if args.open else socket.gethostname()
    hostaddr = args.hostaddr if args.hostaddr else (gethostname() if args.open else hostname)
    log_prefix = f'banco-{args.port}'

    if args.dir is None:
        raise NameError('A Directory Service addess is needed')
    diraddress = args.dir

    agentadd = f'http://{hostaddr}:{args.port}'
    agentid = f'{BANK_NAME.replace(" ", "_")}-{args.port}'
    mess = build_directory_register(agentid, 'ENTIDAD_BANCARIA', agentadd, sender=agentid)

    done = False
    while not done:
        try:
            resp = send_graph_message(diraddress, mess)
            done = True
        except ConnectionError:
            pass

    if response_ok(resp):
        log(f'{agentid} successfully registered')
        try:
            logger_resp = send_graph_message(diraddress, build_directory_search('LOGGER', sender=agentid))
            if response_ok(logger_resp):
                addresses = directory_addresses_from_response(logger_resp)
                if addresses:
                    set_tracer_url(addresses[0])
        except Exception:
            pass
        app.run(host=hostname, port=args.port, debug=False, use_reloader=False)

        log(f'{agentid} unregistering')
        send_graph_message(diraddress, build_directory_unregister(agentid, sender=agentid))
    else:
        log('Unable to register')
