"""
Helpers for rendering small runtime dashboards for agents.
"""

import json

from flask import render_template


def compact(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def summarize_items(items):
    parts = []
    for item in items or []:
        name = item.get('name') or item.get('id') or 'producto'
        quantity = item.get('quantity', 1)
        parts.append(f'{name} x{quantity}')
    return ', '.join(parts)


def purchase_row(purchase):
    return {
        'id': purchase.get('id', ''),
        'client_id': purchase.get('client_id', ''),
        'delivery_address': purchase.get('delivery_address', ''),
        'delivery_deadline': purchase.get('delivery_deadline', ''),
        'delivery_date': purchase.get('delivery_date', ''),
        'transportista': purchase.get('transportista', ''),
        'tracking_id': purchase.get('tracking_id', ''),
        'items': summarize_items(purchase.get('items') or []),
    }


def rows_from_mapping(mapping, id_key='id'):
    rows = []
    for key, value in (mapping or {}).items():
        if isinstance(value, dict):
            row = {id_key: key}
            row.update({k: compact(v) for k, v in value.items()})
        else:
            row = {id_key: key, 'value': compact(value)}
        rows.append(row)
    return rows


def rows_from_sequence(sequence):
    rows = []
    for index, value in enumerate(sequence or [], 1):
        if isinstance(value, dict):
            row = {'#': index}
            row.update({k: compact(v) for k, v in value.items()})
        else:
            row = {'#': index, 'value': compact(value)}
        rows.append(row)
    return rows


def table_section(title, rows, empty='Sin datos', columns=None):
    clean_rows = [{k: compact(v) for k, v in row.items()} for row in (rows or [])]
    if columns is None:
        columns = []
        seen = set()
        for row in clean_rows:
            for key in row.keys():
                if key not in seen:
                    columns.append(key)
                    seen.add(key)
    return {
        'title': title,
        'kind': 'table',
        'rows': clean_rows,
        'columns': columns,
        'empty': empty,
    }


def json_section(title, data, empty='Sin datos'):
    return {
        'title': title,
        'kind': 'json',
        'data': json.dumps(data or {}, ensure_ascii=False, indent=2, default=str),
        'empty': empty,
    }


def render_runtime_info(agent_name, agent_id, stats=None, sections=None, subtitle='Datos runtime del agente'):
    return render_template(
        'runtime_info.html',
        agent_name=agent_name,
        agent_id=agent_id,
        subtitle=subtitle,
        stats=stats or [],
        sections=sections or [],
    )
