"""Гонки публикации, долговечность замка и доказанный отказ до native Store; ERP не подключается."""
from pathlib import Path
import json
import sqlite3
from types import SimpleNamespace

import pytest

from test_route_erp_chain_service import web, day, client, service, plan, preview, DAY  # noqa: F401
from test_route_dispatch_send import _move_one
from route_optimizer import erp_chain_guard as guard, erp_chain_service as ec
from route_optimizer.store import Store


def execute(web, token):
    return web.post('/api/routes/dispatch/erp-chain/execute', json={'id': token, 'csrf': web.chain_csrf})


def lease(web, token):
    record = web.state.erp_chain.get(token, 'qa')
    assert guard.acquire(web.state.store, json.loads(record['plan']), token)


def leased(web):
    return web.state.store._read(lambda db: db.execute('SELECT COUNT(*) FROM erp_chain_plan_lease').fetchone()[0])


def test_live_move_and_publish_are_blocked_while_native_is_running(web):
    web.state.driver_list_loader = web.state.crew_days_loader = None
    token = preview(web).get_json()['result']['id']
    raw, revision = web.state.store.load_dispatch(DAY)
    current = web.get('/api/routes/dispatch?date=' + DAY).get_json()
    move = {**_move_one(current), 'truck': 'CAR2', 'to_trip': None}
    original = web.state.erp_chain.native.call
    observed = []
    def native(request):
        if request['action'] == 'execute':
            for action in (move, {'action': 'send'}, {'action': 'discard'}):
                response = web.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': revision, **action})
                observed.append((response.status_code, response.get_json().get('code')))
            assert web.state.store.load_dispatch(DAY) == (raw, revision)
        return original(request)
    web.state.erp_chain.native.call = native
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    assert observed == [(409, 'plan_locked')] * 3
    assert leased(web) == 0
    assert web.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': revision, **move}).status_code == 200


def test_guard_is_installed_before_live_plan_is_rebuilt(web):
    token = preview(web).get_json()['result']['id']
    record = web.state.erp_chain.get(token, 'qa')
    def current():
        raw, revision = web.state.store.load_dispatch(DAY)
        with pytest.raises(guard.PlanLocked):
            web.state.store.save_dispatch(DAY, raw, 'other', expected_rev=revision)
        return json.loads(record['plan'])
    result = web.state.erp_chain.execute(token, 'qa', current, plan_store=web.state.store)
    assert result['status'] == 'complete' and leased(web) == 0


def test_validation_failure_releases_lease_without_claims_or_native_write(web):
    token = preview(web).get_json()['result']['id']
    record = web.state.erp_chain.get(token, 'qa')
    changed = {**json.loads(record['plan']), 'revision': 999}
    with pytest.raises(ec.ServiceError):
        web.state.erp_chain.execute(token, 'qa', changed, plan_store=web.state.store)
    assert leased(web) == 0
    assert not any(x['action'] == 'execute' for x in web.state.erp_chain.native.calls)


def test_separate_store_and_raw_connection_cannot_bypass_plan_guard(web):
    token = preview(web).get_json()['result']['id']
    lease(web, token)
    another = Store(web.state.store.path)
    raw, rev = another.load_dispatch(DAY)
    with pytest.raises(guard.PlanLocked):
        another.save_dispatch(DAY, raw, 'another', expected_rev=rev)
    with sqlite3.connect(another.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match='erp_chain_plan_locked'):
            db.execute('UPDATE dispatch_plan SET rev=rev+1 WHERE day=?', (DAY,))
    assert another.load_dispatch(DAY)[1] == rev and leased(web) == 1
    assert another.save_dispatch('2026-10-02', {}, 'other') == 1


@pytest.mark.parametrize('change', ['driver', 'courier_elsewhere', 'absence', 'apk', 'truck', 'rewrite'])
def test_crew_and_indirect_mutations_cannot_bypass_guard(web, change):
    token = preview(web).get_json()['result']['id']
    lease(web, token)
    store = web.state.store
    if change == 'truck':  # замок парка действует только на сегодня и позже, а DAY здесь в прошлом
        store._transaction(lambda db: db.execute("UPDATE erp_chain_plan_lease SET day='2999-01-01'"), 'test')
    with pytest.raises(guard.PlanLocked):
        if change == 'driver':
            store.save_truck_driver('CAR1', DAY, 'Կարեն Ավագյան', 'qa')
        elif change == 'courier_elsewhere':
            store.save_truck_driver('CAR2', DAY, 'Արամ Պետրոսյան', 'qa')
        elif change == 'absence':
            store.save_driver_absence('Արամ Պետրոսյան', DAY, None, 'qa')
        elif change == 'apk':
            store.save_apk_crew('CAR1', DAY, 'driver', 'Կարեն Ավագյան', True, 'apk:qa')
        elif change == 'truck':
            store._transaction(lambda db: db.execute("UPDATE trucks SET active=0 WHERE car_code='CAR1'"), 'test')
        else:
            store._transaction(lambda db: store.rewrite_dispatch_after(db, '2026-09-30', lambda d, p: p, 'qa'), 'test')
    assert leased(web) == 1


def test_identical_crew_refresh_is_allowed_but_unrelated_car_can_change(web):
    token = preview(web).get_json()['result']['id']
    lease(web, token)
    store = web.state.store
    from_day, name = store._read(lambda db: db.execute(
        "SELECT from_day,name FROM truck_driver WHERE car_code='CAR1' AND only_day=0 ORDER BY from_day DESC LIMIT 1").fetchone())
    store.save_truck_driver('CAR1', from_day, name, 'qa')
    store.save_truck_driver('UNRELATED', DAY, 'Կարեն Ավագյան', 'qa', only_day=True)
    assert leased(web) == 1


@pytest.mark.parametrize('outcome', ['unknown', 'partial', 'crash'])
def test_uncertain_or_partial_operation_keeps_durable_guard_on_restart(web, outcome):
    token = preview(web).get_json()['result']['id']
    native = web.state.erp_chain.native
    if outcome == 'crash':
        native.failure = SystemExit('crash')
        with pytest.raises(SystemExit):
            execute(web, token)
    elif outcome == 'unknown':
        native.failure = ec.ServiceError('unknown', 503)
        assert execute(web, token).status_code == 503
    else:
        original = native.call
        def partial(request):
            result = original(request)
            if request['action'] == 'execute':
                result['status'] = 'partial'
            return result
        native.call = partial
        assert execute(web, token).get_json()['result']['status'] == 'partial'
    restarted = ec.ChainService(web.state.erp_chain.journal, native, quarantine=web.state.erp_chain.quarantine)
    state = restarted.status(token, 'qa', plan_store=Store(web.state.store.path))
    assert state['status'] in {'unknown', 'partial'} and leased(web) == 1
    raw, rev = web.state.store.load_dispatch(DAY)
    with pytest.raises(guard.PlanLocked):
        Store(web.state.store.path).save_dispatch(DAY, raw, 'qa', expected_rev=rev)
    assert sum(x['action'] == 'execute' for x in native.calls) == 1


def test_completed_native_readback_recovers_after_web_journal_write_failure(web):
    token = preview(web).get_json()['result']['id']
    journal = web.state.erp_chain.journal
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TRIGGER fail_complete BEFORE UPDATE OF status ON previews WHEN NEW.status='complete' BEGIN SELECT RAISE(ABORT,'disk fault'); END")
    assert execute(web, token).status_code == 500
    assert leased(web) == 1
    with sqlite3.connect(journal) as db:
        db.execute('DROP TRIGGER fail_complete')
    web.state.erp_chain = ec.ChainService(journal, web.state.erp_chain.native, quarantine=web.state.erp_chain.quarantine)
    result = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert result['status'] == 'complete' and leased(web) == 0
    assert sum(x['action'] == 'execute' for x in web.state.erp_chain.native.calls) == 1


def test_structured_native_busy_is_proven_no_write(monkeypatch):
    monkeypatch.setattr(ec.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0,
        stdout=json.dumps({'success': False, 'code': 'busy', 'no_write_dispatched': True}), stderr=''))
    with pytest.raises(ec.ServiceError) as caught:
        ec.LocalNative().call({'action': 'execute', 'id': 'unused'})
    assert caught.value.code == 'busy' and caught.value.no_write


def test_unstructured_native_exit_keeps_unknown_safeguards(monkeypatch):
    monkeypatch.setattr(ec.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='IOException'))
    with pytest.raises(ec.ServiceError) as caught:
        ec.LocalNative().call({'action': 'execute', 'id': 'unused'})
    assert caught.value.code == 'unknown' and not caught.value.no_write


def test_busy_releases_claims_and_plan_but_old_token_is_never_relaunched(web):
    token = preview(web).get_json()['result']['id']
    native = web.state.erp_chain.native
    native.failure = ec.ServiceError('busy', no_write=True)
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['no_write_dispatched']
    assert leased(web) == 0
    result = execute(web, token).get_json()['result']
    assert result['status'] == 'refused' and result['no_write_dispatched']
    assert sum(x['action'] == 'execute' for x in native.calls) == 1
    native.failure = None
    new_token = preview(web).get_json()['result']['id']
    assert execute(web, new_token).get_json()['result']['status'] == 'complete'


def test_proven_busy_recovery_survives_crash_before_plan_release(web, monkeypatch):
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain.native.failure = ec.ServiceError('busy', no_write=True)
    release = guard.release
    monkeypatch.setattr(guard, 'release', lambda *a: (_ for _ in ()).throw(SystemExit('crash')))
    with pytest.raises(SystemExit):
        execute(web, token)
    assert leased(web) == 1
    monkeypatch.setattr(guard, 'release', release)
    result = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert result['no_write_dispatched'] and leased(web) == 0


def _chain_objects(store):
    return store._read(lambda db: db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'erp_chain%'").fetchone()[0])


def test_guard_objects_appear_only_with_the_first_lease(web):
    store = web.state.store
    before = store.load_dispatch(DAY)
    assert _chain_objects(store) == 0 and guard.held_operations(store) == []
    token = preview(web).get_json()['result']['id']
    assert web.get('/api/routes/dispatch/erp-chain/status/' + token).status_code == 200
    assert _chain_objects(store) == 0
    upgraded = Store(store.path)
    assert upgraded.load_dispatch(DAY) == before and _chain_objects(upgraded) == 0
    lease(web, token)
    assert _chain_objects(store) > 0 and guard.held_operations(store) == [token]
    with pytest.raises(guard.PlanLocked):
        upgraded.save_dispatch(DAY, before[0], 'qa', expected_rev=before[1])


def test_release_before_any_lease_is_a_no_op(web):
    store = web.state.store
    guard.release(store, '00000000-0000-0000-0000-000000000001')
    assert _chain_objects(store) == 0
