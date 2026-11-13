# -*- coding: utf-8 -*-
"""«Развоз»: утверждение и отправка — два шага (владелец 09.11.2026), итог пересборки.

approve с "send": false закрепляет рейсы и ставит approved, но released и sent не трогает; водителям план уходит только
action send. Без флага — как раньше. Инвариант №80: водители не видят план, который ни разу не утверждали.
note_rebuild: итог пересборки (Draft.last_build) — только сведение, рейсы сборки не меняются.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_publish_split.py -q
"""
import importlib.util
import json
import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import forecast_history as fh  # noqa: E402
from route_optimizer import tg_reports as rp  # noqa: E402
from route_optimizer import views  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from test_route_dispatch_logic_fixes import _ok, _page, _post, _split_store  # noqa: E402
from test_route_dispatch_same_day import DAY, FORD, HOWO, D, _base, _build, _page_setup, client  # noqa: E402,F401
from test_route_dispatch_send import NOW, _edit, _move_one, _terminal  # noqa: E402
from test_route_optimizer import _info  # noqa: E402

TWO = ('CAR1', 'CAR2')


def _raw(draft):
    return json.loads(json.dumps(draft.to_json()))


def _two_trips():
    return dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101]),
                                                           dp.DraftTrip(2, HOWO.car_code, [102, 103])], next_id=3)


# ============================== approve / send: черновик ==============================

def test_default_approve_still_releases_and_sends():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u')
    assert draft.released == {'at': 'a', 'by': 'u'} and draft.sent['at'] == 'a' and 'held' not in draft.approved


def test_approve_without_send_leaves_released_and_sent_untouched():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    assert draft.approved['at'] == 'a' and all(t.pinned for t in draft.trips)
    assert draft.released is None and draft.sent is None and draft.for_drivers() is draft
    assert dp.unsent(draft) is None


def test_approved_without_send_stays_unreleased_after_reading_from_the_base():
    """Главная ловушка: запись «approved без released» читалась как утверждённая до №80 — и попадала бы на терминалы."""
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    back = dp.Draft.from_json(_raw(draft))
    assert back == draft and back.released is None and back.sent is None and back.approved['held'] is True
    assert 'sent' not in back.to_json() and 'released' not in back.to_json()


def test_held_approval_is_stored_under_its_own_key():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    raw = _raw(draft)
    assert 'approved' not in raw and raw['held_approval']['at'] == 'a' and 'held' not in raw['held_approval']
    dp.send(draft, 'b', 'u')
    raw = _raw(draft)
    assert 'held_approval' not in raw and 'held' not in raw['approved']


@pytest.fixture
def base_dispatch(tmp_path):
    """dispatch.py базового релиза 5a05176 — отдельным модулем пакета: так он увидит соседей (fleet, replacement…)."""
    if shutil.which('git') is None:
        pytest.skip('нет git на этой машине: базовый dispatch.py из 5a05176 недоступен')
    got = subprocess.run(['git', 'show', '5a05176:route_optimizer/dispatch.py'], cwd=ROOT, capture_output=True)
    if got.returncode != 0:
        pytest.skip('нет истории git: базовый dispatch.py из 5a05176 недоступен')
    path = tmp_path / 'dispatch_5a05176.py'
    path.write_bytes(got.stdout)
    name = 'route_optimizer.dispatch_5a05176'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # dataclass ищет модуль по имени
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[name]


def test_previous_release_sees_held_draft_as_unapproved(base_dispatch):
    """Откат: прежний Draft.from_json не должен выпустить утверждённый без отправки план (миграция «до №80»)."""
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    raw = _raw(draft)
    old = base_dispatch.Draft.from_json(raw)
    drivers = old.for_drivers()
    assert old.approved is None and old.released is None and old.sent is None
    assert drivers.released is None and drivers.sent is None
    # контроль: сама миграция в этом модуле есть — обычный approved без released она выпускает
    control = base_dispatch.Draft.from_json({**raw, 'approved': {'at': 'a', 'by': 'u', 'pinned': []}})
    assert control.released == {'at': 'a', 'by': 'u'} and control.sent is not None


def test_forecast_history_counts_held_approval_as_approved():
    conn = sqlite3.connect(':memory:')
    fh.append(conn, DAY, 1, {'prediction': {'created_at': 'x', 'trucks': {}}, 'trips': [], 'held_approval': {'at': 'a', 'by': 'u', 'pinned': []}})
    (row,) = fh.read(conn, DAY)
    assert row['approved'] is True and row['published'] is False


def test_send_after_approve_releases_and_snapshots():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    dp.send(draft, 'b', 'u2')
    assert draft.released == {'at': 'b', 'by': 'u2'} and draft.sent['at'] == 'b' and draft.sent['by'] == 'u2'
    assert draft.sent['plan']['released'] == {'at': 'b', 'by': 'u2'} and 'held' not in draft.sent['plan']['approved']
    assert 'held' not in draft.approved
    back = dp.Draft.from_json(_raw(draft))
    assert back == draft and back.for_drivers().released == {'at': 'b', 'by': 'u2'}


def test_send_with_neither_approved_nor_released_raises():
    *_, draft = _base((FORD, HOWO))
    with pytest.raises(dp.DispatchError):
        dp.send(draft, 'a', 'u')
    assert draft.released is None and draft.sent is None


def test_send_of_released_plan_without_approval_still_allowed():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u')
    dp.unapprove(draft)
    dp.send(draft, 'b', 'u')
    assert draft.approved is None and draft.released == {'at': 'a', 'by': 'u'} and draft.sent['at'] == 'b'


def test_old_plans_read_as_before():
    *_, draft = _base((FORD, HOWO))
    raw = _raw(draft)
    before_80 = dp.Draft.from_json({**raw, 'approved': {'at': 'a', 'by': 'u', 'pinned': []}})   # утверждён до №80
    assert before_80.released == {'at': 'a', 'by': 'u'} and before_80.sent is not None
    before_81 = dp.Draft.from_json({**raw, 'released': {'at': 'a', 'by': 'u'}})                 # выпущен до №81
    assert before_81.sent is not None and before_81.sent['at'] == 'a' and before_81.for_drivers().trips == before_81.trips


def test_reissue_keeps_old_snapshot_until_send():
    ctx, base, orders, _, _ = _base((FORD, HOWO))
    draft = _two_trips()
    dp.approve(draft, 'a', 'u')
    sent, was = draft.sent, {t.truck: list(t.stops) for t in draft.for_drivers().trips}
    dp.unapprove(draft)
    trip, other = draft.trips
    draft = dp.apply_edit(ctx, base, draft, {'action': 'move', 'customer_id': trip.stops[0], 'from_trip': trip.id,
                                             'to_trip': other.id, 'truck': other.truck}, {o.isn for o in orders})
    dp.approve(draft, 'c', 'u', send=False)
    assert draft.sent is sent or draft.sent == sent
    assert draft.released == {'at': 'a', 'by': 'u'} and 'held' not in draft.approved
    assert {t.truck: list(t.stops) for t in draft.for_drivers().trips} == was
    assert dp.unsent(draft) is not None
    dp.send(draft, 'd', 'u')
    assert dp.unsent(draft) is None and draft.sent['at'] == 'd'


def test_mark_loaded_refused_until_sent():
    *_, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u', send=False)
    with pytest.raises(dp.DispatchError) as e:
        dp.mark_loaded(draft, draft.trips[0].id, '2026-10-01T09:00:00', 'u')
    assert str(e.value) == 'Նախ ուղարկեք պլանը վարորդներին'
    dp.send(draft, 'b', 'u')
    dp.mark_loaded(draft, draft.trips[0].id, '2026-10-01T09:00:00', 'u')
    assert draft.trips[0].loaded is not None


# ============================== approve / send: страница, терминалы, склад ==============================

def test_api_approve_without_send_reaches_no_terminal_and_send_delivers(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    d = _edit(client, d, action='approve', send=False)
    assert d['approved'] and 'sent' not in d and 'released' not in d and 'unsent' not in d
    assert rl.routes_view(state, D).released is False and not any(_terminal(state).values())
    s = client.get('/api/routes/dispatch?date=' + DAY).get_json()    # и после чтения из базы
    assert s['approved'] and 'sent' not in s and 'released' not in s
    d = _edit(client, d, action='send')
    assert d['approved'] and d['sent'] and d['released'] is True and d['unsent'] is None
    assert rl.routes_view(state, D).released is True and any(_terminal(state).values())


def test_api_default_approve_unchanged(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    d = _edit(client, d, action='approve')
    assert d['sent'] and d['released'] is True and any(_terminal(state).values())


def test_api_send_before_any_approval_refused(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'send'})
    assert r.status_code == 400 and 'հաստատեք' in r.get_json()['errors']['_']


def test_api_crew_and_cargo_checks_wait_for_send(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    monkeypatch.setattr(views, '_crew_problems', lambda *a, **k: [{'truck': 'CAR1', 'kind': 'none'}])
    monkeypatch.setattr(views.dp, 'loaded_changed', lambda *a, **k: True)
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True    # без флага — как раньше
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve', 'confirm_loaded': True})
    assert r.status_code == 400 and r.get_json()['crew_confirm'] is True
    d = _edit(client, d, action='approve', send=False)                        # без отправки — ни то ни другое
    assert d['approved'] and 'sent' not in d
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'send'})
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True    # при отправке — снова
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'send', 'confirm_loaded': True})
    assert r.status_code == 400 and r.get_json()['crew_confirm'] is True


def test_api_multi_truck_block_kept_without_send(client, monkeypatch):
    state = _page(client, monkeypatch)
    d = _split_store(client, state, 101)
    r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve', 'send': False})
    assert r.status_code == 400 and [x['customer_id'] for x in r.get_json()['split_block']] == [101]
    assert 'approved' not in _ok(client.get('/api/routes/dispatch?date=2026-10-01'))


def test_api_warehouse_waits_for_send(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    t = d['plan']['trucks'][0]
    car, trip = t['car_code'], t['trips'][0]['id']
    d = _edit(client, d, action='approve', send=False)
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert w['approved'] is False and w['unsent'] is True and w['planned'] is True and w['trucks'] == []
    q = f'date={DAY}&truck={car}&rev={d["rev"]}'
    assert client.get(f'/api/routes/warehouse/goods?{q}&trip={trip}').status_code == 409
    assert client.get(f'/api/routes/warehouse/waybill?{q}').status_code == 409
    r = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': d['rev'], 'trip': trip, 'loaded': True})
    assert r.status_code == 409 and r.get_json()['error'] == dp.NOT_SENT_LOAD
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'loaded', 'trip': trip})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == dp.NOT_SENT_LOAD
    d = _edit(client, d, action='send')
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert w['approved'] is True and 'unsent' not in w and w['trucks']
    r = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': d['rev'], 'trip': trip, 'loaded': True})
    assert r.status_code == 200, r.get_json()


def test_api_telegram_and_first_sent_day_wait_for_send(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    d = _edit(client, d, action='approve', send=False)
    assert state.store.first_sent_day() is None
    assert rp.day_plan(state.store.load_dispatch(DAY)[0], D, {}, {}, {}, {}) is None
    _edit(client, d, action='send')
    assert state.store.first_sent_day() == DAY
    assert rp.day_plan(state.store.load_dispatch(DAY)[0], D, {}, {}, {}, {}) is not None


def test_api_never_sent_plan_can_be_reset_after_unapprove(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    d = _edit(client, d, action='approve', send=False)
    d = _edit(client, d, action='unapprove')
    r = client.post('/api/routes/dispatch/reset', json={'date': DAY, 'rev': d['rev']})
    assert r.status_code == 200, r.get_json()


def test_api_reissue_keeps_drivers_on_old_snapshot(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    d = _edit(client, d, action='approve')

    def drivers():
        return [(t.truck, t.stops) for t in dp.Draft.from_json(state.store.load_dispatch(DAY)[0]).for_drivers().trips]
    seen, at = drivers(), d['sent']['at']
    d = _edit(client, d, action='unapprove')
    d = _edit(client, d, **_move_one(d))
    d = _edit(client, d, action='approve', send=False)
    assert d['approved'] and d['sent']['at'] == at and d['unsent'] is not None
    assert drivers() == seen
    d = _edit(client, d, action='send')
    assert d['unsent'] is None and drivers() != seen


# ============================== итог пересборки (last_build) ==============================

def _setup_rebuild():
    ctx, base, _, _, old = _base((FORD, HOWO))
    new = dp.build(ctx, base, old, [FORD.car_code, HOWO.car_code], 'later')
    return ctx, base, old, new


def _fake_km(monkeypatch, new):
    def view(ctx, stops, draft, info, explain=True, crew=None, published=False):
        return {'summary': {'km': 90.0 if [t.stops for t in draft.trips] == [t.stops for t in new.trips] else 100.0}}
    monkeypatch.setattr(dp, 'plan_view', view)


def test_first_build_untouched():
    ctx, base, _, new = _setup_rebuild()
    before = new.to_json()
    dp.note_rebuild(ctx, base, dp.Draft(), new, _info, 'at')
    assert new.last_build is None and 'last_build' not in new.to_json() and new.to_json() == before


def test_rebuild_report_never_changes_the_new_plan():
    """Прежний план не оставляется никогда: правила сборки (отдельный рейс, load_cap, одна машина на магазин) — за build."""
    ctx, base, old, new = _setup_rebuild()
    trips = [(t.id, t.truck, list(t.stops)) for t in new.trips]
    snapshot = _raw(old)
    dp.note_rebuild(ctx, base, old, new, _info, 'at')
    assert [(t.id, t.truck, t.stops) for t in new.trips] == trips and _raw(old) == snapshot
    assert set(new.last_build) == {'at', 'moved', 'km_before', 'km_after'} and new.last_build['at'] == 'at'


def test_last_build_reports_moved_stores_and_km(monkeypatch):
    ctx, base, _, new = _setup_rebuild()
    old = _two_trips()
    new.trips = [dp.DraftTrip(5, HOWO.car_code, [101, 102]), dp.DraftTrip(6, FORD.car_code, [103])]
    _fake_km(monkeypatch, new)
    dp.note_rebuild(ctx, base, old, new, _info, 'at')
    assert new.last_build == {'at': 'at', 'km_before': 100.0, 'km_after': 90.0,
                              'moved': [{'customer_id': 101, 'from': FORD.car_code, 'to': HOWO.car_code},
                                        {'customer_id': 103, 'from': HOWO.car_code, 'to': FORD.car_code}]}


def test_last_build_round_trip_absence_and_garbage():
    *_, draft = _base((FORD, HOWO))
    assert 'last_build' not in draft.to_json() and dp.Draft.from_json(_raw(draft)).last_build is None
    row = {'at': 'a', 'moved': [{'customer_id': 7, 'from': 'A', 'to': 'B'}], 'km_before': 10.5, 'km_after': 9.0}
    draft.last_build = row
    assert dp.Draft.from_json(_raw(draft)).last_build == row
    assert 'last_build' not in draft.sent_plan()                # водителям не передаётся
    for bad in ('x', {'at': 1}, {'at': 'a', 'moved': 'x', 'km_before': 1, 'km_after': 1}, {'at': 'a', 'moved': [], 'km_before': 'x', 'km_after': 1}):
        assert dp.Draft.from_json({**_raw(draft), 'last_build': bad}).last_build is None
    broken_row = {**row, 'moved': [*row['moved'], {'customer_id': 'x', 'from': 'A', 'to': 'B'}, 'y']}
    assert dp.Draft.from_json({**_raw(draft), 'last_build': broken_row}).last_build['moved'] == row['moved']


def test_api_first_build_has_no_report_rebuild_has(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)
    assert 'last_build' not in d
    d = _build(client, TWO)
    assert d['last_build']['moved'] == [] and d['last_build']['km_before'] == d['last_build']['km_after']


def test_api_compare_does_not_report(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, TWO)

    def boom(*a, **k):
        raise AssertionError('compare must not call note_rebuild')
    monkeypatch.setattr(views.dp, 'note_rebuild', boom)
    r = client.post('/api/routes/dispatch/compare', json={'date': DAY, 'rev': d['rev'], 'trucks': list(TWO)})
    assert r.status_code == 200, r.get_json()
