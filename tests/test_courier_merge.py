# -*- coding: utf-8 -*-
"""Правило объединения и разделения точек «Առաքիչ» (контракт §5 п. 12, v1.2): courier/merge.py.

Контрольные примеры — tests/fixtures/courier_merge_vectors.json (байт-в-байт копия
docs/plans/courier-merge-vectors.json; приложение проходит те же). Чистая функция: без баз и сети.

Запуск из корня проекта:  python -m pytest tests/test_courier_merge.py -q
"""
import itertools
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import merge as mg  # noqa: E402

VECTORS = json.loads((ROOT / 'tests' / 'fixtures' / 'courier_merge_vectors.json').read_text(encoding='utf-8'))
CASES = VECTORS['cases']
BY_NAME = {c['name']: c for c in CASES}
FIELDS = ('removed', 'status', 'due', 'paid', 'flags', 'instruction')


def at(hhmm: str, zone: str = '+04:00') -> str:
    return f'2026-10-02T{hhmm}:00.000{zone}'


def stop(sid, source, lines, current=True, replaces=(), collect='cash'):
    s = {'stop_id': sid, 'source': source, 'collect': collect, 'current': current,
         'lines': [{'line_id': lid, 'product_id': pid, 'qty': q, 'price': pr} for lid, pid, q, pr in lines]}
    if source == 'invoice':
        s['replaces'] = list(replaces)
    return s


def delivery(eid, sid, when, lines, supersedes=None):
    payload = {'lines': [{'line_id': lid, 'qty': q} for lid, q in lines]}
    if supersedes is not None:
        payload['supersedes'] = supersedes
    return {'id': eid, 'type': 'delivery', 'stop_id': sid, 'at': when, 'payload': payload}


def payment(eid, sid, when, amount, kind='invoice', cancel_of=None):
    payload = {'amount': amount, 'kind': kind}
    if cancel_of is not None:
        payload['cancel_of'] = cancel_of
    return {'id': eid, 'type': 'payment', 'stop_id': sid, 'at': when, 'payload': payload}


# --- контрольные примеры ---

def test_vectors_fixture():
    assert VECTORS['version'] == '1.2.2'
    assert len(CASES) == 26
    assert len(BY_NAME) == 26


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_vector(case):
    got = mg.merge(case['stops'], case['events']).json()
    expected = case['expected']
    assert sorted(got) == sorted(expected)   # ровно показываемые точки (поглощённые не показываются)
    for sid, exp in expected.items():
        assert set(got[sid]) == set(FIELDS), sid
        for f in FIELDS:
            assert got[sid][f] == exp[f], f'{sid}.{f}'
    assert got == expected
    for v in got.values():   # форма JSON: деньги — float, без Decimal
        assert isinstance(v['due'], float) and isinstance(v['paid'], float)
        assert v['instruction']['amount'] is None or isinstance(v['instruction']['amount'], float)
    json.dumps(got)


def test_vectors_attribution_and_absorbed():
    """Сверх формы примеров: чьё заявление определило статус (для «кто доставил» в офисе) и кто кого поглотил."""
    r = mg.merge(BY_NAME['05_merge_all_orders_delivered']['stops'], BY_NAME['05_merge_all_orders_delivered']['events'])
    assert r.absorbed_by == {'O:1': 'S:A', 'O:2': 'S:A'}
    assert r.stops['S:A'].statements == ('d1', 'd2') and r.stops['S:A'].statement == 'd2'   # сумма заказов
    c = BY_NAME['09_order_delivery_after_invoice_delivery_conflict']
    assert mg.merge(c['stops'], c['events']).stops['S:A'].statement == 'dS'   # конфликт → заявление накладной
    c = BY_NAME['11_split_order_delivered_before_split']
    r = mg.merge(c['stops'], c['events'])
    assert r.absorbed_by == {'O:2': 'S:10'}
    assert r.stops['S:10'].statement == 'd2' and r.stops['S:20'].statement is None   # covered — своего нет
    c = BY_NAME['14_invoice_recreated_after_delivery']
    r = mg.merge(c['stops'], c['events'])
    assert r.absorbed_by == {'O:1': 'S:2', 'S:1': 'S:2'} and r.stops['S:2'].statement == 'dS1'
    c = BY_NAME['18_vanished_stop_without_successor_removed']
    r = mg.merge(c['stops'], c['events'])
    assert r.absorbed_by == {} and r.stops['S:9'].statement == 'e1'


# --- свойства ---

@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_order_of_input_does_not_matter(case):
    base = mg.merge(case['stops'], case['events'])
    for seed in range(8):
        rnd = random.Random(seed)
        stops, events = list(case['stops']), list(case['events'])
        rnd.shuffle(stops)
        rnd.shuffle(events)
        assert mg.merge(stops, events) == base, seed


def test_superseded_chain():
    """e1 ← e2 ← e3: действует e3, хотя его at самый ранний (часы терминала шли назад)."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 10, 100)])]
    events = [delivery('e1', 'S:A', at('12:00'), [('a1', 10)]),
              delivery('e2', 'S:A', at('11:00'), [('a1', 6)], supersedes='e1'),
              delivery('e3', 'S:A', at('10:00'), [('a1', 3)], supersedes='e2'),
              payment('p1', 'S:A', at('10:01'), 300)]
    for perm in itertools.permutations(events):
        r = mg.merge(stops, list(perm))
        v = r.stops['S:A']
        assert v.json() == {'removed': False, 'status': 'partial', 'due': 300, 'paid': 300, 'flags': [],
                            'instruction': {'kind': 'paid', 'amount': None}}
        assert v.statement == 'e3'


def test_supersedes_only_within_same_stop():
    """supersedes на доставку другой точки не вытесняет её (п. 14: «той же точки»)."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 10, 100)]), stop('S:B', 'invoice', [('b1', 100, 10, 100)])]
    events = [delivery('eA', 'S:A', at('10:00'), [('a1', 10)]),
              delivery('eB', 'S:B', at('10:05'), [('b1', 0)], supersedes='eA')]
    r = mg.merge(stops, events)
    assert (r.stops['S:A'].status, r.stops['S:B'].status) == ('full', 'refused')


def test_payment_cancel_of_a_cancel_is_ignored():
    """Отмена отмены не восстанавливает платёж: p1 отменён c1, c2 ссылается на c1 — ничего не меняет."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 10, 100)])]
    events = [payment('p1', 'S:A', at('09:00'), 1000),
              payment('c1', 'S:A', at('09:05'), 1000, cancel_of='p1'),
              payment('c2', 'S:A', at('09:10'), 1000, cancel_of='c1'),
              payment('p2', 'S:A', at('09:20'), 400),
              payment('p3', 'S:A', at('09:30'), 999, kind='debt')]
    v = mg.merge(stops, events).stops['S:A']
    assert v.json() == {'removed': False, 'status': 'pending', 'due': 1000, 'paid': 400, 'flags': [],
                        'instruction': {'kind': 'take', 'amount': 600}}


def test_unknown_stop_in_replaces_ignored():
    """O:404 в replaces неизвестен: ссылка игнорируется (не ребро, не поглощается, без ошибки)."""
    stops = [stop('O:1', 'order', [('o1', 100, 10, 100)], current=False),
             stop('S:A', 'invoice', [('s1', 100, 10, 100)], replaces=['O:1', 'O:404'])]
    events = [delivery('d1', 'O:1', at('09:00'), [('o1', 10)]), payment('p1', 'O:1', at('09:01'), 1000)]
    r = mg.merge(stops, events)
    assert r.absorbed_by == {'O:1': 'S:A'}
    assert r.json() == {'S:A': {'removed': False, 'status': 'full', 'due': 1000, 'paid': 1000, 'flags': [],
                                'instruction': {'kind': 'paid', 'amount': None}}}
    assert mg.merge(stops, []).json()['S:A']['status'] == 'pending'


def test_empty_input():
    r = mg.merge([], [])
    assert r.stops == {} and r.absorbed_by == {} and r.json() == {}


def test_any_event_but_gps_makes_stop_active_money_only_from_delivery_and_payment():
    """Любое событие, кроме arrived/geo_suggest, делает точку «со своими событиями» (показ убранной точки, рёбра
    replaces); деньги и статус — только из delivery и payment; at и payload прочих типов не разбираются."""
    stops = [stop('S:9', 'invoice', [('z1', 100, 10, 100)], current=False),
             stop('S:8', 'invoice', [('y1', 100, 10, 100)], current=False),
             stop('S:A', 'invoice', [('a1', 100, 10, 100)])]
    events = [{'id': 'r1', 'type': 'return', 'stop_id': 'S:9', 'at': at('09:00'), 'payload': {}},
              {'id': 'g1', 'type': 'arrived', 'stop_id': 'S:8', 'at': at('09:00'), 'payload': {}},
              {'id': 'g2', 'type': 'geo_suggest', 'stop_id': 'S:8', 'at': at('09:01'), 'payload': {}},
              {'id': 's1', 'type': 'scan', 'stop_id': 'S:A', 'at': 'garbage', 'payload': None},
              {'id': 'x1', 'type': 'day_closed', 'stop_id': None, 'at': at('18:00'), 'payload': {'summary': {}}}]
    r = mg.merge(stops, events)
    assert r.json() == {'S:9': {'removed': True, 'status': 'pending', 'due': 1000, 'paid': 0, 'flags': [],
                                'instruction': {'kind': 'none', 'amount': None}},
                        'S:A': mg.merge(stops, []).json()['S:A']}   # S:8 только с GPS — не показывается
    # старая накладная S:1 только с тарой всё равно связывает свои заказы с новой S:2; arrived — нет
    stops = [stop('O:1', 'order', [('o1', 100, 10, 100)], current=False),
             stop('O:3', 'order', [('o3', 300, 4, 50)], current=False),
             stop('S:1', 'invoice', [('s1', 100, 10, 100), ('s3', 300, 4, 50)], current=False,
                  replaces=['O:1', 'O:3']),
             stop('S:2', 'invoice', [('v1', 100, 10, 100), ('v3', 300, 4, 50)], replaces=['O:1'])]
    events = [{'id': 't1', 'type': 'tare', 'stop_id': 'S:1', 'at': at('08:00'), 'payload': {'items': []}},
              delivery('d3', 'O:3', at('09:30'), [('o3', 4)])]
    r = mg.merge(stops, events)
    assert r.absorbed_by == {'O:1': 'S:2', 'O:3': 'S:2', 'S:1': 'S:2'}
    assert r.json() == {'S:2': {'removed': False, 'status': 'in_progress', 'due': 1200, 'paid': 0, 'flags': [],
                                'instruction': {'kind': 'take', 'amount': 1200}}}
    gps = [{'id': 'g1', 'type': 'arrived', 'stop_id': 'S:1', 'at': at('08:00'), 'payload': {}}, events[1]]
    for unlinked in (events[1:], gps):   # без события S:1 или только с GPS — S:1 не связывает O:3
        assert sorted(mg.merge(stops, unlinked).stops) == ['O:3', 'S:2']


def test_same_moment_ties_broken_by_id_across_zones():
    """Один момент в разных зонах — равенство at, решает id (строковое сравнение)."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 10, 100)])]
    events = [delivery('b', 'S:A', '2026-10-02T06:00:00Z', [('a1', 4)]),
              delivery('a', 'S:A', at('10:00'), [('a1', 10)])]
    for perm in (events, events[::-1]):
        v = mg.merge(stops, perm).stops['S:A']
        assert (v.status, v.due, v.statement) == ('partial', Decimal(400), 'b')


def test_no_refund_before_stop_is_finished():
    """pending/in_progress с переплатой — «paid», не «refund»; после завершения точки — refund."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 10, 100)])]
    events = [payment('p1', 'S:A', at('09:00'), 1500)]
    assert mg.merge(stops, events).json()['S:A']['instruction'] == {'kind': 'paid', 'amount': None}
    events.append(delivery('d1', 'S:A', at('10:00'), [('a1', 10)]))
    assert mg.merge(stops, events).json()['S:A']['instruction'] == {'kind': 'refund', 'amount': 500}


def test_money_exact_and_diff_rounded_half_up():
    """float → Decimal без двоичного шума; due и paid не округляются; diff = due − paid — до 0,01, половина — вверх
    (ROUND_HALF_UP: от нуля)."""
    stops = [stop('S:A', 'invoice', [('a1', 100, 3, 0.1)]),
             stop('S:B', 'invoice', [('b1', 100, 1, 1.005)]),
             stop('S:C', 'invoice', [('c1', 100, 1, 0.125)])]
    events = [delivery('dA', 'S:A', at('10:00'), [('a1', 3)]), payment('pA', 'S:A', at('10:01'), 0.3),
              delivery('dB', 'S:B', at('10:02'), [('b1', 1)]), payment('pB', 'S:B', at('10:03'), 1),
              delivery('dC', 'S:C', at('10:04'), [('c1', 1)]), payment('pC', 'S:C', at('10:05'), 0.13)]
    r = mg.merge(stops, events)
    a, b, c = (r.stops[sid] for sid in ('S:A', 'S:B', 'S:C'))
    assert a.due == Decimal('0.3') and r.json()['S:A']['due'] == 0.3 and a.instruction.kind == 'paid'
    assert b.due == Decimal('1.005') and r.json()['S:B']['due'] == 1.005   # due не округляется
    assert b.instruction == mg.Instruction('take', Decimal('0.01'))   # 0,005 → 0,01 (Decimal(1.005) дал бы 0,00)
    assert c.instruction == mg.Instruction('refund', Decimal('0.01'))   # −0,005 → −0,01
    assert r.json()['S:C']['instruction'] == {'kind': 'refund', 'amount': 0.01}


def test_absorbed_order_outside_owner_replaces_counts_in_sum():
    """«Сумма заказов» берёт все заявления заказов компоненты, даже вне replaces владельца (O:3 связан со старой
    S:1); остаток — по строкам владельца: товары 100 и 300 покрыты — остатка нет."""
    stops = [stop('O:1', 'order', [('o1', 100, 10, 100)], current=False),
             stop('O:3', 'order', [('o3', 300, 4, 50)], current=False),
             stop('S:1', 'invoice', [('s1', 100, 10, 100), ('s3', 300, 4, 50)], current=False,
                  replaces=['O:1', 'O:3']),
             stop('S:2', 'invoice', [('v1', 100, 10, 100)], replaces=['O:1'])]
    events = [delivery('d1', 'O:1', at('09:00'), [('o1', 10)]),
              delivery('d3', 'O:3', at('09:30'), [('o3', 4)]),
              payment('pS1', 'S:1', at('09:31'), 1200)]
    r = mg.merge(stops, events)
    assert r.absorbed_by == {'O:1': 'S:2', 'O:3': 'S:2', 'S:1': 'S:2'}
    assert r.json() == {'S:2': {'removed': False, 'status': 'full', 'due': 1200, 'paid': 1200, 'flags': [],
                                'instruction': {'kind': 'paid', 'amount': None}}}
    assert r.stops['S:2'].statements == ('d1', 'd3')


def test_remainder_ignores_owner_lines_with_zero_qty():
    """Остаток — только строки владельца с кол-вом > 0: строка 0 шт. непокрытого товара не делает in_progress."""
    stops = [stop('O:1', 'order', [('o1', 100, 10, 100)], current=False),
             stop('S:A', 'invoice', [('s1', 100, 10, 100), ('s9', 900, 0, 70)], replaces=['O:1'])]
    r = mg.merge(stops, [delivery('d1', 'O:1', at('09:00'), [('o1', 10)])])
    assert (r.stops['S:A'].status, r.stops['S:A'].due) == ('full', Decimal(1000))


def test_owner_is_invoice_before_smaller_order_id():
    """Владелец — среди текущих сначала накладная, хотя 'O:…' < 'S:…' по строке."""
    stops = [stop('O:1', 'order', [('o1', 100, 10, 100)]),
             stop('O:2', 'order', [('o2', 200, 10, 200)], current=False),
             stop('S:A', 'invoice', [('s1', 100, 10, 100), ('s2', 200, 10, 200)], replaces=['O:1', 'O:2'])]
    r = mg.merge(stops, [delivery('d2', 'O:2', at('09:00'), [('o2', 10)])])
    assert r.absorbed_by == {'O:2': 'S:A'}
    assert sorted(r.stops) == ['O:1', 'S:A']
    assert all(v.flags == ('split_order',) for v in r.stops.values())


@pytest.mark.parametrize('stops, events', [
    ([stop('S:A', 'invoice', []), stop('S:A', 'invoice', [])], []),                                  # повтор точки
    ([stop('S:A', 'invoice', [])], [payment('p', 'S:A', at('09:00'), 1), payment('p', 'S:A', at('09:01'), 1)]),
    ([stop('S:A', 'invoice', [])], [payment('p', 'S:A', '2026-10-02T09:00:00', 1)]),                 # без зоны
    ([stop('S:A', 'invoice', [])], [payment('p', 'S:A', at('09:00'), '1000')]),                      # не число
    ([stop('S:A', 'invoice', [('a1', 1, float('nan'), 1)])], []),
    ([dict(stop('S:A', 'invoice', []), source='bill')], []),
    ([stop('S:A', 'invoice', [])], [{'type': 'tare', 'stop_id': 'S:A', 'at': at('09:00'), 'payload': {}}]),  # без id
    ([{k: v for k, v in stop('S:A', 'invoice', []).items() if k != 'current'}], []),
    ([stop('S:A', 'invoice', [('a1', 1, 1, 1), ('a1', 1, 1, 1)])], []),                              # повтор строки
])
def test_malformed_input_raises(stops, events):
    with pytest.raises(mg.MergeInputError):
        mg.merge(stops, events)
