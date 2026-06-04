#!/usr/bin/env bash

# this script spins up:
# - a directory service,
# - 1 catalogador agent,
# - 1 valorador agent,
# - 1 tesorero agent,
# - 4 logistics centers,
# - 1 venta agent, and
# - 1 cliente agent,
# - 1 external banking entity,
# - 3 external transport companies, and
# - 4 external seller companies.

set -u

DIR_HOST="127.0.0.1"
DIR_PORT="9000"
DIR_URL="http://${DIR_HOST}:${DIR_PORT}"
HOSTADDR="127.0.0.1"

# Prefer local env python, then active env python, then system python3
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/external_agents_config.json"
if [[ -x "${SCRIPT_DIR}/env/bin/python" ]]; then
    PYTHON="${SCRIPT_DIR}/env/bin/python"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    PYTHON="${SCRIPT_DIR}/.venv/bin/python"
else
    PYTHON="$(command -v python || command -v python3)"
fi

if [[ -z "${PYTHON:-}" ]]; then
    echo "Python interpreter not found."
    exit 1
fi

echo "Using Python: ${PYTHON}"

PIDS=()
NAMES=()

start_agent() {
    local name="$1"
    shift

    echo "Starting ${name}..."
    "$@" &
    local pid=$!

    PIDS+=("$pid")
    NAMES+=("$name")

    echo "  -> ${name} PID: ${pid}"
}

shutdown_all() {
    echo
    echo "Shutting down agents..."

    for i in "${!PIDS[@]}"; do
        local pid="${PIDS[$i]}"
        local name="${NAMES[$i]}"
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping ${name} (PID ${pid})"
            kill "$pid" 2>/dev/null || true
        fi
    done

    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    echo "All agents stopped."
    exit 0
}

trap shutdown_all EXIT INT TERM

start_agent "DirectoryService"  "$PYTHON" DirectoryService.py  --port 9000  --open --hostaddr "$HOSTADDR"
start_agent "Logger"             "$PYTHON" Logger.py             --port 9100  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "Client"             "$PYTHON" Client.py             --port 9010  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "Catalogador"        "$PYTHON" Catalogador.py        --port 9040  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "Valorador"          "$PYTHON" Valorador.py          --port 9050  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "EntidadBancaria"    "$PYTHON" EntidadBancaria.py    --port 9080  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE"
start_agent "EmpresaVendedora HomePlus"   "$PYTHON" EmpresaVendedora.py --port 9090 --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile homeplus
start_agent "EmpresaVendedora BagStore"   "$PYTHON" EmpresaVendedora.py --port 9091 --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile bagstore
start_agent "EmpresaVendedora BookPlanet" "$PYTHON" EmpresaVendedora.py --port 9092 --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile bookplanet
start_agent "EmpresaVendedora TechHub"    "$PYTHON" EmpresaVendedora.py --port 9093 --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile techhub
start_agent "Transportista RapidShip"  "$PYTHON" Transportista.py  --port 9071  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile rapidship
start_agent "Transportista CheapMove"  "$PYTHON" Transportista.py  --port 9072  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile cheapmove
start_agent "Transportista PremiumLog" "$PYTHON" Transportista.py  --port 9073  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR" --config "$CONFIG_FILE" --profile premiumlog
start_agent "Tesorero"           "$PYTHON" Tesorero.py           --port 9060  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "Ventas"             "$PYTHON" Ventas.py             --port 9020  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "CentroLogistico0"   "$PYTHON" CentroLogistico.py   --port 9030  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "CentroLogistico1"   "$PYTHON" CentroLogistico.py   --port 9031  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "CentroLogistico2"   "$PYTHON" CentroLogistico.py   --port 9032  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"
start_agent "CentroLogistico3"   "$PYTHON" CentroLogistico.py   --port 9033  --dir "$DIR_URL" --open --hostaddr "$HOSTADDR"

echo
echo "Agents are running."
read -r -n 1 -s -p "Press any key to stop all agents..."
echo
