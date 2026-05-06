# ECSDI – Multi-Agent E-Commerce System

A multi-agent system built with Flask where each agent runs as an independent HTTP service. Agents communicate through a central **DirectoryService** that handles registration, discovery, and round-robin load balancing.

## Architecture

| Agent | Default port | Role |
|---|---|---|
| `DirectoryService` | 9000 | Service registry and load balancer |
| `Logger` | 9100 | Centralized log collector |
| `Client` | 9010 | Web UI – search products, place orders |
| `Catalogador` | 9040 | Product catalogue search and filtering |
| `Valorador` | 9050 | Product ratings and reviews |
| `Ventas` | 9020 | Order/sales management |
| `CentroLogistico` (×4) | 9030–9033 | Logistics centres – stock and delivery |

The intended deployment runs each agent on a **separate machine**, pointing `--dir` at the shared DirectoryService URL and `--hostaddr` at the machine's own IP so agents advertise the correct address.

## Running on separate machines (production-like)

Each agent accepts at minimum `--port`, `--dir`, and `--hostaddr`:

```bash
# On the directory host (e.g. 192.168.1.10)
python DirectoryService.py --port 9000 --hostaddr 192.168.1.10 --open

# On each agent host – replace DIR_URL and HOSTADDR accordingly
python Catalogador.py --port 9040 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
python Valorador.py   --port 9050 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
python Ventas.py      --port 9020 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
python CentroLogistico.py --port 9030 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
python Client.py      --port 9010 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
```

Open `http://<client-host>:9010` in your browser.

## Local development with `develop.sh`

To iterate quickly without a multi-machine setup, `develop.sh` spins up the full stack on `localhost` in a single terminal session:

```bash
cd src/
bash develop.sh
```

This starts all agents (DirectoryService, Logger, Client, Catalogador, Valorador, Ventas, and 4 CentroLogistico instances) as background processes. Press any key to stop them all cleanly.

The script auto-detects the Python interpreter in `env/`, `.venv/`, or falls back to the system `python`/`python3`.

> `develop.sh` is **not** meant for production – all agents bind to `127.0.0.1` and are only reachable locally.

## Setup

```bash
cd src/
python -m venv env
source env/bin/activate        # Windows: env\Scripts\activate
pip install -r requirements.txt
```

## Project structure

```
src/
├── DirectoryService.py   # Service registry
├── Logger.py             # Log aggregator
├── Client.py             # End-user web interface
├── Catalogador.py        # Product search agent
├── Valorador.py          # Ratings agent
├── Ventas.py             # Sales/order agent
├── CentroLogistico.py    # Logistics centre agent (multiple instances)
├── FlaskServer.py        # Shared Flask utilities
├── Util.py               # Shared helpers
├── StressTest.py         # Load/stress testing script
├── develop.sh            # Local all-in-one launcher
├── requirements.txt
└── templates/            # Jinja2 HTML templates
```
