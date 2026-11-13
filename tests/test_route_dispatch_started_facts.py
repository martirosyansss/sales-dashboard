# -*- coding: utf-8 -*-
"""«Развоз»: передача рейса другой машине после отправки плана. Запрет «рейс уже начат» смотрит на факт работы водителя,
а не только на часы плана: плановое начало загрузки прошло, а терминал машины молчит — рейс ещё можно отдать.
Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_started_facts.py -q
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_dispatch_same_day import DAY, FORD, HOWO, _base, _build, _page_setup, client  # noqa: E402,F401

LATE = 24 * 60.0    # все рейсы по часам плана уже начаты
BLOCKED = 'Երթն արդեն սկսվել է'


def _sent():
    ctx, base, *_ = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101, 102, 103])], next_id=2)
    dp.approve(draft, 'a', 'u')
    return ctx, base, draft


def _swap(ctx, base, draft, **kw):
    edit = {'action': 'swap_trucks', 'truck': FORD.car_code, 'other_truck': HOWO.car_code}
    return dp.apply_edit(ctx, base, draft, edit, set(), now_min=LATE, **kw)


def _pin(ctx, base, draft, **kw):
    return dp.apply_edit(ctx, base, draft, {'action': 'pin', 'trip': 1, 'truck': HOWO.car_code}, set(), now_min=LATE, **kw)


@pytest.mark.parametrize('edit', [_swap, _pin])
def test_trip_passes_to_other_truck_while_driver_has_not_started(edit):
    ctx, base, draft = _sent()
    assert 1 in dp.started_trips(ctx, base, draft, LATE)   # по часам плана рейс начат
    assert next(t for t in edit(ctx, base, draft, driver_active=set()).trips if t.id == 1).truck == HOWO.car_code


@pytest.mark.parametrize('edit', [_swap, _pin])
def test_driver_activity_keeps_the_block(edit):
    ctx, base, draft = _sent()
    with pytest.raises(dp.DispatchError, match=BLOCKED):
        edit(ctx, base, draft, driver_active={FORD.car_code})


@pytest.mark.parametrize('edit', [_swap, _pin])
def test_unknown_facts_fall_back_to_plan_clock(edit):
    ctx, base, draft = _sent()
    with pytest.raises(dp.DispatchError, match=BLOCKED):
        edit(ctx, base, draft)


def test_activity_of_another_truck_does_not_block():
    ctx, base, draft = _sent()
    assert next(t for t in _swap(ctx, base, draft, driver_active={'OTHER'}).trips if t.id == 1).truck == HOWO.car_code


def test_loaded_trip_stays_blocked_without_driver_facts():
    ctx, base, draft = _sent()
    draft.trips[0].loaded = {'at': 'a', 'by': 'u'}
    with pytest.raises(dp.DispatchError, match=BLOCKED):
        _swap(ctx, base, draft, driver_active=set())


def test_loaded_mark_removed_after_send_does_not_block():
    """Отметку «Բեռնված է» снимают только в черновике: след в отправленном снимке запрета не держит."""
    ctx, base, *_ = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101, 102, 103])], next_id=2)
    draft.trips[0].loaded = {'at': 'a', 'by': 'u'}
    dp.approve(draft, 'a', 'u')
    draft.trips[0].loaded = None
    assert next(t for t in _swap(ctx, base, draft, driver_active=set()).trips if t.id == 1).truck == HOWO.car_code


def test_truck_of_sent_plan_counts_when_draft_already_moved_the_trip():
    """Черновик уже переложил рейс на другую машину, а водитель работает по отправленному плану."""
    ctx, base, draft = _sent()
    draft.trips[0].truck = HOWO.car_code
    with pytest.raises(dp.DispatchError, match=BLOCKED):
        _swap(ctx, base, draft, driver_active={FORD.car_code})


class _Facts:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or {}, error

    def fleet(self, day):
        if self.error is not None:
            raise self.error
        return self.rows


def _active(facts):
    return views._driver_active(SimpleNamespace(live_facts=facts), date(2026, 10, 1))


def test_driver_active_needs_terminal_events_or_track():
    rows = {'LOGIN': {'stops': [], 'track': [], 'contacts': [], 'last_contact': 'x'},
            'OFFICE_PLAN': {'stops': [{'stop_id': 'O:1', 'status': 'pending'}], 'track': [], 'contacts': []},
            'EVENTS': {'stops': [], 'track': [], 'contacts': ['t']},
            'GPS': {'stops': [], 'track': [(1, 40.0, 44.0)], 'contacts': []}}
    assert _active(_Facts(rows)) == {'EVENTS', 'GPS'}


@pytest.mark.parametrize('facts', [None, _Facts(), _Facts(error=OSError('x')), _Facts(error=RuntimeError('x')),
                                   _Facts(error=KeyError('x'))])
def test_driver_active_is_unknown_without_facts(facts):
    assert _active(facts) is None


def test_driver_active_prefers_fresh_read():
    facts = _Facts({'STALE': {'contacts': [1]}})
    facts.fresh_fleet = lambda day: {'FRESH': {'contacts': [1]}}
    assert _active(facts) == {'FRESH'}


def _setup_sent(client, monkeypatch, rows):
    _page_setup(client, monkeypatch)
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve', 'confirm_crew': True})
    assert r.status_code == 200, r.get_json()
    client.application.extensions['route_optimizer'].live_facts = _Facts(rows)
    return r.get_json()


def _post_swap(client, d):
    return client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'swap_trucks',
                                                          'truck': 'CAR2', 'other_truck': 'CAR1', 'confirm_reserve': True})


def _trip_trucks(d):
    return {tr['id']: t['car_code'] for t in d['plan']['trucks'] for tr in t['trips']}


def test_api_transfers_trip_of_silent_terminal(client, monkeypatch):
    office_plan = {'stops': [{'stop_id': 'O:1', 'status': 'pending'}], 'track': [], 'contacts': []}
    d = _setup_sent(client, monkeypatch, {'CAR1': office_plan, 'CAR2': office_plan, 'OTHER': {'track': [], 'contacts': ['t']}})
    before = _trip_trucks(d)
    monkeypatch.setattr(dp, '_central', lambda *_: False)   # допуск в центр здесь ни при чём: у машин теста он разный
    r = _post_swap(client, d)
    assert r.status_code == 200, r.get_json()
    assert _trip_trucks(r.get_json()) == {tid: {'CAR1': 'CAR2', 'CAR2': 'CAR1'}[car] for tid, car in before.items()}


def test_api_keeps_block_for_working_terminal(client, monkeypatch):
    working = {'stops': [{'stop_id': 'O:1'}], 'track': [], 'contacts': ['t']}
    r = _post_swap(client, _setup_sent(client, monkeypatch, {'CAR1': working, 'CAR2': working}))
    assert r.status_code == 400 and BLOCKED in r.get_json()['error']
