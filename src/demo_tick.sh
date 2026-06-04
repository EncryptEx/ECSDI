#!/usr/bin/env bash

set -u

HOST="${1:-127.0.0.1}"
WAIT_FOR_FEEDBACK_SECONDS="${WAIT_FOR_FEEDBACK_SECONDS:-30}"

echo "Accelerating ECSDI demo timers on ${HOST}"

if [[ "${REGISTER_EXTERNAL_PRODUCTS:-0}" == "1" ]]; then
    for port in 9090 9091 9092 9093; do
        echo
        echo "EmpresaVendedora ${port}: Recepcion nuevo producto"
        curl -s "http://${HOST}:${port}/tick/nuevo-producto"
        echo
    done
fi

for port in 9030 9031 9032 9033; do
    echo
    echo "CentroLogistico ${port}: Enviar lotes timer up"
    curl -s "http://${HOST}:${port}/tick/envios"
    echo
done

if [[ "${WAIT_FOR_FEEDBACK_SECONDS}" -gt 0 ]]; then
    echo
    echo "Waiting ${WAIT_FOR_FEEDBACK_SECONDS}s so delivered purchases become feedback candidates"
    sleep "${WAIT_FOR_FEEDBACK_SECONDS}"
fi

echo
echo "Valorador 9050: Feedback timer up"
curl -s "http://${HOST}:9050/tick/feedback"
echo

echo
echo "Valorador 9050: Recomendacion timer up"
curl -s "http://${HOST}:9050/tick/recomendaciones"
echo
