"""
Small local geocoding helpers for the ECSDI demo.

No external APIs are used. The demo works with known addresses and optional
"lat,lon" coordinates typed by the user.
"""

import math
import re


DEMO_CLIENT_LOCATIONS = [
    {
        'id': 'upc_nord',
        'label': 'UPC Campus Nord',
        'address': 'UPC Campus Nord, Barcelona',
        'lat': 41.3894,
        'lon': 2.1132,
    },
    {
        'id': 'sants',
        'label': 'Barcelona Sants',
        'address': 'Barcelona Sants, Barcelona',
        'lat': 41.3791,
        'lon': 2.1400,
    },
    {
        'id': 'gracia',
        'label': 'Gracia',
        'address': 'Travessera de Gracia 120, Barcelona',
        'lat': 41.4008,
        'lon': 2.1577,
    },
    {
        'id': 'sabadell',
        'label': 'Sabadell Centre',
        'address': 'Sabadell Centre',
        'lat': 41.5486,
        'lon': 2.1074,
    },
]


LOGISTIC_CENTER_LOCATIONS = [
    {
        'id': 'zona_franca',
        'label': 'Centro Logistico Zona Franca',
        'address': 'Carrer A, Zona Franca, Barcelona',
        'lat': 41.3355,
        'lon': 2.1356,
    },
    {
        'id': 'sant_andreu',
        'label': 'Centro Logistico Sant Andreu',
        'address': 'Passeig de Santa Coloma 60, Barcelona',
        'lat': 41.4354,
        'lon': 2.1932,
    },
    {
        'id': 'sant_cugat',
        'label': 'Centro Logistico Sant Cugat',
        'address': 'Avinguda Cerdanyola 75, Sant Cugat',
        'lat': 41.4720,
        'lon': 2.0847,
    },
    {
        'id': 'girona',
        'label': 'Centro Logistico Girona',
        'address': 'Carrer Barcelona 120, Girona',
        'lat': 41.9794,
        'lon': 2.8214,
    },
]


COORDINATE_PATTERN = re.compile(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)')


def normalize_text(value):
    return str(value or '').strip().lower()


def all_known_locations():
    return DEMO_CLIENT_LOCATIONS + LOGISTIC_CENTER_LOCATIONS


def geocode_address(address):
    text = normalize_text(address)
    if not text:
        return None

    for location in all_known_locations():
        if text in {
            normalize_text(location.get('id')),
            normalize_text(location.get('label')),
            normalize_text(location.get('address')),
        }:
            return dict(location)

    match = COORDINATE_PATTERN.search(str(address))
    if match:
        return {
            'id': 'custom',
            'label': 'Custom coordinates',
            'address': str(address).strip(),
            'lat': float(match.group(1)),
            'lon': float(match.group(2)),
        }

    return None


def location_for_logistics_port(port):
    try:
        index = max(0, int(port) - 9030)
    except (TypeError, ValueError):
        index = 0
    return dict(LOGISTIC_CENTER_LOCATIONS[index % len(LOGISTIC_CENTER_LOCATIONS)])


def haversine_km(first, second):
    if not first or not second:
        return None
    if first.get('lat') is None or first.get('lon') is None:
        return None
    if second.get('lat') is None or second.get('lon') is None:
        return None

    radius_km = 6371.0
    lat1 = math.radians(float(first['lat']))
    lon1 = math.radians(float(first['lon']))
    lat2 = math.radians(float(second['lat']))
    lon2 = math.radians(float(second['lon']))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def distance_from_address_km(address, location):
    return haversine_km(geocode_address(address), location)
