"""
.. module:: StressTest

StressTest
*************

:Description: StressTest

    Manda peticiones a los solver como si fuera el cliente

    Un cliente que este en marcha recibira las respuestas

:Authors: bejar
    

:Version: 

:Created on: 07/02/2018 13:26 

"""

import argparse
import random, string
import socket
from rdflib import Literal

__author__ = 'bejar'

from AgentCommunication import (
    ECSDI,
    build_directory_search,
    build_message_with_content,
    directory_addresses_from_response,
    response_ok,
    send_graph_message,
)


def build_solve_request(problem_type, clientaddress, probid, payload, sender):
    graph, content = build_message_with_content(ECSDI.PeticionResolverProblema, sender=sender, receiver='SOLVER')
    graph.add((content, ECSDI.tipoProblema, Literal(problem_type)))
    graph.add((content, ECSDI.direccionCliente, Literal(clientaddress)))
    graph.add((content, ECSDI.idProblema, Literal(probid)))
    graph.add((content, ECSDI.contenidoProblema, Literal(payload)))
    return graph

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--n', default=100, type=int, help="Numero de iteraciones del test")
    parser.add_argument('--cport', default=None, help="Puerto del cliente que recibe las respuestas")
    parser.add_argument('--dir', default=None, help="Direccion del servicio de directorio")

    # parsing de los parametros de la linea de comandos
    args = parser.parse_args()

    probcounter = 0
    diraddress = args.dir
    clientaddress = f'http://{socket.gethostname()}:{args.cport}' #args.client
    testid = ''.join(random.choice(string.ascii_lowercase) for i in range(10))

    for i in range(args.n):
        print(f'TEST {i}')
        probcounter += 1

        solver_response = send_graph_message(
            diraddress,
            build_directory_search('SOLVER', sender=testid)
        )

        if response_ok(solver_response):
            addresses = directory_addresses_from_response(solver_response)
            if not addresses:
                continue
            solveradd = addresses[0]
            probid = f'TESTARITH-{testid}-{probcounter:03}'
            mess = build_solve_request('ARITH', clientaddress, probid, f'{i}+{i}', testid)
            resp = send_graph_message(solveradd, mess, timeout=5)
            probid = f'TESTMFREQ-{testid}-{probcounter}'
            payload = ''.join(random.choice(string.ascii_lowercase) for i in range(500))
            mess = build_solve_request('MFREQ', clientaddress, probid, payload, testid)
            resp = send_graph_message(solveradd, mess, timeout=5)
