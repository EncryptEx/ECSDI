# ECSDI – Multi-Agent E-Commerce System

A multi-agent system built with Flask where each agent runs as an independent HTTP service. Agents use **FIPA ACL performatives** with RDF graph content for communication. The central **DirectoryService** handles registration, discovery, and round-robin load balancing through the same RDF/FIPA message format.

## Architecture

| Agent | Default port | Role |
|---|---|---|
| `DirectoryService` | 9000 | Service registry and load balancer |
| `Logger` | 9100 | Centralized log collector |
| `Client` | 9010 | Web UI – search products, place orders |
| `Catalogador` | 9040 | Product catalogue search and filtering |
| `Valorador` | 9050 | Product ratings and reviews |
| `Tesorero` | 9060 | Client billing, refunds and external provider payments |
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
python Tesorero.py    --port 9060 --dir http://192.168.1.10:9000 --hostaddr 192.168.1.XX --open
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

This starts all agents (DirectoryService, Logger, Client, Catalogador, Valorador, Tesorero, Ventas, and 4 CentroLogistico instances) as background processes. Press any key to stop them all cleanly.

The script auto-detects the Python interpreter in `env/`, `.venv/`, or falls back to the system `python`/`python3`.

> `develop.sh` is **not** meant for production – all agents bind to `127.0.0.1` and are only reachable locally.

## Demo timers

Some PDTool perceptions are proactive timers. For the demo, they are exposed as manual endpoints so you can accelerate the process after placing an order:

```bash
cd src/
./demo_tick.sh
```

This triggers all local logistics centres to send pending shipping data to the client, then asks `Valorador` to send feedback requests and recommendations.

Individual timer endpoints:

```bash
curl http://127.0.0.1:9030/tick/envios
curl http://127.0.0.1:9031/tick/envios
curl http://127.0.0.1:9032/tick/envios
curl http://127.0.0.1:9033/tick/envios
curl http://127.0.0.1:9050/tick/feedback
curl http://127.0.0.1:9050/tick/recomendaciones
```

## Protocol test suite

Run the in-process protocol game from the repository root:

```bash
src/env/bin/python src/ProtocolTestSuite.py
```

It covers internal products, external products stored in ECSDI logistics centres, fully external products, provider registration, client charging, provider payment, feedback and purchase-history `query-ref` protocols.

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
├── Tesorero.py           # Financial agent
├── Ventas.py             # Sales/order agent
├── CentroLogistico.py    # Logistics centre agent (multiple instances)
├── FlaskServer.py        # Shared Flask utilities
├── Util.py               # Shared helpers
├── StressTest.py         # Load/stress testing script
├── ProtocolTestSuite.py  # In-process PDTool/FIPA protocol test game
├── develop.sh            # Local all-in-one launcher
├── requirements.txt
└── templates/            # Jinja2 HTML templates
```
