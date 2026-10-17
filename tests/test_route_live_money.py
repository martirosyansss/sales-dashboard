# -*- coding: utf-8 -*-
"""Подсказка магазина на карте «Մեքենաները առցանց» (пользователь 13.10): сколько товара отдано по весу, сколько денег
взято и сколько надо было взять, проблемы точки (courier.live.stop_money → live.car_view → /api/routes/live/truck).
Деньги API отдаёт только администратору (как «Կանխիկ» в «Վարորդներ»), «Гаражу» — money: null."""
from datetime import timedelta

import pytest

from test_route_live import (A, API_NOW, B, C, DAY, LAN, LIVE_NOW, SID1, SID2, SID3, _day_stop, _ev,  # noqa: F401
                             _session_as, _who, app_v2, client, courier_app, live_app)


def _cash(stop, collect='cash', amount=None):
    lines = stop['lines']
    return {**stop, 'collect': collect,
            'amount_due': amount if amount is not None else sum(ln['qty'] * ln['price'] for ln in lines)}


def test_stop_money_short_noncash_and_no_payment(courier_app):
    from courier import events as ev
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    ds = DAY.isoformat()
    # SID1 — наличные 1 500 ֏ (15 шт × 100), отдано 10 + 2,5 — стоимость 1 250, взято 1 000: «Պակաս է վերցրել 250»
    # SID2 — безнал, а водитель взял 300 ֏; SID3 (другая машина) — наличные, отдано всё, денег нет
    store.save_day(ds, 'CAR1', [_cash(_day_stop(SID1, 1, A, [('l1', 10, 12.0), ('l2', 5, 6.0)], 1)),
                                _cash(_day_stop(SID2, 2, B, [('l3', 4, 20.0)], 2), collect='none')],
                   'v1', LIVE_NOW.isoformat())
    store.save_day(ds, 'CAR2', [_cash(_day_stop(SID3, 3, C, [('l6', 2, 10.0)], 1))], 'v1', LIVE_NOW.isoformat())
    t = lambda m: LIVE_NOW - timedelta(minutes=m)   # noqa: E731
    r = ev.ingest(store, _who(store, 'CAR1', 'Արամ', '1111'), [
        _ev('delivery', SID1, {'lines': [{'line_id': 'l1', 'qty': 10}, {'line_id': 'l2', 'qty': 2.5}]}, t(30)),
        _ev('payment', SID1, {'amount': 1000, 'kind': 'invoice'}, t(29)),
        _ev('payment', SID1, {'amount': 200, 'kind': 'debt'}, t(29)),
        _ev('delivery', SID2, {'lines': [{'line_id': 'l3', 'qty': 4}]}, t(20)),
        _ev('payment', SID2, {'amount': 300, 'kind': 'invoice'}, t(19))]).json()
    assert len(r['accepted']) == 5, r
    r = ev.ingest(store, _who(store, 'CAR2', 'Սուրեն', '2222'),
                  [_ev('delivery', SID3, {'lines': [{'line_id': 'l6', 'qty': 2}]}, t(10))]).json()
    assert len(r['accepted']) == 1, r
    with courier_app.app_context():
        fleet = LiveSource(store).fleet(ds)
    s1, s2 = fleet['CAR1']['stops']
    assert s1['money'] == {'collect': 'cash', 'amount_due': 1500.0, 'due': 1250.0, 'expected': 1250.0, 'paid': 1000.0,
                           'debt': 200.0, 'problems': [{'code': 'short', 'amd': 250.0}]}
    assert s2['money']['expected'] is None and s2['money']['paid'] == 300.0
    assert s2['money']['problems'] == [{'code': 'noncash_paid', 'amd': 300.0}]
    s3 = fleet['CAR2']['stops'][0]
    assert s3['money']['expected'] == 200.0 and s3['money']['paid'] == 0.0
    assert s3['money']['problems'] == [{'code': 'no_payment', 'amd': 200.0}]


def test_stop_money_rules():
    from types import SimpleNamespace as NS
    from decimal import Decimal as D
    from courier.live import stop_money

    def v(status, due, paid, statement='e1', flags=()):
        return NS(status=status, due=D(due), paid=D(paid), statement=statement, flags=tuple(flags))
    assert stop_money(None, 'cash', 100, []) is None
    # всё взято (расхождение меньше 1 ֏ — округление): проблем нет
    assert stop_money(v('full', '1000', '999.5'), 'cash', 1000, [])['problems'] == []
    # ещё не доставлен: стоимость и «надо взять» неизвестны, проблем нет
    m = stop_money(v('pending', '1000', '0', statement=None), 'cash', 1000, [])
    assert (m['due'], m['expected'], m['problems']) == (None, None, [])
    # отдан по заказу (covered): брать нечего; взяли — «Ավել է վերցրել»
    m = stop_money(v('covered', '0', '50'), 'cash', 1000, [])
    assert m['expected'] == 0.0 and m['problems'] == [{'code': 'over', 'amd': 50.0}]
    # ՀԴՄ без чека и расходящиеся заявления — в проблемах; «ask» — не безнал
    pays = [{'type': 'payment', 'payload': {'amount': 500, 'kind': 'invoice'}, 'flags': ['no_ecr_receipt']}]
    m = stop_money(v('full', '500', '500', flags=['merge_conflict']), 'cash_ecr', 500, pays)
    assert [p['code'] for p in m['problems']] == ['no_receipt', 'conflict']
    assert stop_money(v('full', '500', '500'), 'ask', 500, [])['problems'] == []


def test_live_truck_money_only_for_admin(client, live_app):
    fleet = live_app.app.extensions['route_optimizer'].live_facts.data['2026-10-03']['CAR1']
    money = {'collect': 'cash', 'amount_due': 1500.0, 'due': 1250.0, 'expected': 1250.0, 'paid': 1000.0, 'debt': 0.0,
             'problems': [{'code': 'short', 'amd': 250.0}]}
    fleet['stops'][0] = {**fleet['stops'][0], 'status': 'partial', 'share': 0.5, 'money': money}
    _session_as(client, 'boss', base=LAN)
    stops = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']['stops']
    assert stops[0]['money'] == money and stops[0]['delivered_kg'] == 250.0   # 500 кг × 0,5
    assert stops[1]['money'] is None and stops[1]['delivered_kg'] is None
    _session_as(client, 'garage1', base=LAN)
    stops = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']['stops']
    assert stops[0]['money'] is None and stops[0]['delivered_kg'] == 250.0     # вес «Гаражу» виден, деньги — нет


def test_live_page_cash_missed_point_red_and_blinks():
    """Пользователь 17.10: деньги надо было взять, а не взяты (ничего — no_payment, меньше — short) — точка на карте красная
    и мигает (кольцо 1 Гц, «меньше движения» — ровное); взял больше / безнал — не мигает. ?v= подняты, знак — в легенде."""
    from test_route_live import ROOT
    js = (ROOT / 'static' / 'js' / 'routes_live.js').read_text(encoding='utf-8')
    css = (ROOT / 'static' / 'css' / 'routes_live.css').read_text(encoding='utf-8')
    html = (ROOT / 'templates' / 'routes_live.html').read_text(encoding='utf-8')
    assert "const CASH_SHORT_MIN = 10;" in js   # недостача меньше 10 ֏ (сдача монетами) — не мигает
    assert "p.code === 'no_payment' || (p.code === 'short' && num(p.amd) >= CASH_SHORT_MIN)" in js
    assert "const cash = cashMissed(s);" in js and "const color = cash ? '--rt-danger' : storeOf(s)[1];" in js
    assert "pinIcon(s.plan_no, color, !!lt, cash)" in js and "(cash ? ' is-cash' : '')" in js
    assert "className: 'lv-cash-dot'" in js   # точка вне плана (кружок SVG) — мигает заливкой
    assert '.lv-npin.is-cash::before {\n' in css   # ровное кольцо — всегда
    motion = css.index('@media (prefers-reduced-motion: no-preference) {\n    .lv-npin.is-cash::before')
    assert css.index('.lv-npin.is-cash::before { inset: -2px; animation: lv-halo 1s ease-out infinite; }') > motion   # мигание
    assert '<i class="lv-npin-sq is-cash">' in html
    assert "routes_live.js') }}?v=33" in html and "routes_live.css') }}?v=24" in html
