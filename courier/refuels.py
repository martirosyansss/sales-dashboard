"""Read-only refueling journal shared by administrators and the garage role."""
from __future__ import annotations

import logging
import os
import re
from decimal import Decimal
from functools import wraps
from typing import Any

from flask import Blueprint, abort, g, jsonify, render_template, request, send_file

from . import clock
from .facts import office_window, refuel_flags
from .state import state

bp = Blueprint('refuels', __name__)
log = logging.getLogger(__name__)
API = '/api/routes/garage/refuels'
PAGE_SIZE = 50
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _access(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if g.get('user_role') not in ('admin', 'garage'):
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


@bp.after_request
def _private(response):
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@bp.get('/routes/refuels')
@_access
def page():
    today = clock.today()
    return render_template('routes_refuels.html', refuels_since=today.replace(day=1).isoformat(),
                           refuels_until=today.isoformat())


def journal(store, since: str, until: str, car: str, corrected: bool, page_number: int) -> dict[str, Any]:
    # Read the complete chains before filtering: a correction may arrive in another month.
    records = store.refuels()
    cars = sorted({r['car_code'] for r in records})
    selected = [r for r in records if since <= r['eff_date'] <= until
                and (not car or r['car_code'] == car)]
    active = [r for r in selected if not r['superseded']]
    known_amounts = [Decimal(str(r['payload']['amount_amd'])) for r in active
                     if r['payload'].get('amount_amd') is not None]
    totals = {'count': len(active), 'cars': len({r['car_code'] for r in active}),
              'liters': float(sum((Decimal(str(r['payload'].get('liters', 0))) for r in active), Decimal(0))),
              'amount_amd': float(sum(known_amounts)) if known_amounts else (None if active else 0),
              'missing_amount': len(active) - len(known_amounts)}
    shown = [r for r in selected if corrected or not r['superseded']]
    shown.sort(key=lambda r: (r['eff_at_utc'], r['at_utc'], r['id']), reverse=True)
    total = len(shown)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page_number = min(page_number, pages)
    shown = shown[(page_number - 1) * PAGE_SIZE:page_number * PAGE_SIZE]
    first, last = clock.parse_day(since), clock.parse_day(until)
    flags = refuel_flags([r for r in records if not car or r['car_code'] == car],
                         office_window(first)[0], office_window(last)[1]) if shown else {}
    photos = store.photos_for_events([r['id'] for r in shown])
    entries = []
    for r in shown:
        p = r['payload']
        entries.append({'id': r['id'], 'car_code': r['car_code'], 'at': r['eff_at'],
                        'sent_at': r['at'], 'day': r['eff_date'], 'driver_name': r['driver_name'],
                        'liters': p.get('liters'), 'odometer_km': p.get('odometer_km'),
                        'full_tank': p.get('full_tank'), 'amount_amd': p.get('amount_amd'),
                        'place': p.get('place') or '', 'superseded': r['superseded'],
                        'correction': bool(p.get('supersedes')), 'flags': flags.get(r['id'], []),
                        'photos': [{'id': x['id'], 'url': API + '/photos/' + x['id']}
                                   for x in photos.get(r['id'], []) if UUID.fullmatch(x['id'])]})
    return {'success': True, 'since': since, 'until': until, 'car': car, 'corrected': corrected,
            'cars': cars, 'entries': entries, 'total': total, 'totals': totals,
            'page': page_number, 'pages': pages, 'page_size': PAGE_SIZE, 'today': clock.today().isoformat()}


@bp.get(API)
@_access
def api_journal():
    today = clock.today()
    since = request.args.get('since', today.replace(day=1).isoformat())
    until = request.args.get('until', today.isoformat())
    first, last = clock.parse_day(since), clock.parse_day(until)
    if first is None or last is None or first > last or (last - first).days > 365:
        return jsonify(success=False, error='Ընտրեք ճիշտ ժամանակահատված՝ առավելագույնը մեկ տարի։'), 400
    car = request.args.get('car', '').strip()
    if len(car) > 64 or any(ord(c) < 32 for c in car):
        return jsonify(success=False, error='Մեքենայի համարը սխալ է։'), 400
    corrected = request.args.get('corrected', '0')
    number = request.args.get('page', '1')
    if (corrected not in ('0', '1') or len(number) > 6 or not number.isascii()
            or not number.isdigit() or not 1 <= int(number) <= 100000):
        return jsonify(success=False, error='Ցուցակի պարամետրերը սխալ են։'), 400
    try:
        return jsonify(journal(state().store, since, until, car, corrected == '1', int(number)))
    except Exception:
        log.exception('Refueling journal is unavailable')
        return jsonify(success=False, error='Լիցքավորումները չհաջողվեց բեռնել։ Կրկին փորձեք։'), 503


@bp.get(API + '/photos/<photo_id>')
@_access
def photo(photo_id):
    # Garage access is limited to refueling attachments, never other courier photos.
    if not UUID.fullmatch(photo_id):
        abort(404)
    st = state()
    rec = st.store.photo(photo_id)
    if not rec or not any(r['id'] == rec['event_id'] for r in st.store.refuels()):
        abort(404)
    root = os.path.realpath(st.store.photos_dir)
    path = os.path.realpath(os.path.join(root, rec['path']))
    try:
        inside = os.path.commonpath([root, path]) == root and path != root
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(path):
        abort(404)
    response = send_file(path, mimetype='image/png' if path.lower().endswith('.png') else 'image/jpeg', max_age=0)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
