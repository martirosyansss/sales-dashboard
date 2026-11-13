"""Отказы, захват заказов, повтор HTTP и права новой цепочки; ERP здесь не подключается."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import erp_chain_guard as guard, erp_chain_service as ec
from route_optimizer.erp import Agent, Car, ErpError
from test_route_dispatch_erp_write import day, client, _sent, _isn, DAY  # noqa: F401


class Native:
    def __init__(self):
        self.calls, self.operations = [], {}
        self.failure = None

    def call(self, request):
        self.calls.append(copy.deepcopy(request))
        action = request['action']
        if action == 'options':
            return {'database': ec.DATABASE, 'warehouses': [{'code': c, 'name': 'Warehouse'} for c in ('000', '007', '015', '016')]}
        if action == 'preview':
            plan = request['plan']
            return {'id': str(uuid4()), 'database': ec.DATABASE, 'day': plan['day'], 'status': 'preview',
                    'order_count': sum(len(x['orders']) for x in plan['loads']),
                    'loads': [{**load, 'orders': [{'isn': i} for i in load['orders']]} for load in plan['loads']]}
        if action == 'execute':
            if self.failure:
                raise self.failure
            self.operations[request['id']] = {'id': request['id'], 'database': ec.DATABASE, 'status': 'complete', 'steps': []}
        if request['id'] not in self.operations:
            raise ec.ServiceError('not_found')
        return self.operations[request['id']]


def plan(revision=1):
    return {'day': DAY, 'revision': revision,
            'loads': [{'trip': '1', 'warehouse': '000', 'courier': 'B001', 'car': 'CAR1',
                       'orders': ['00000000-0000-0000-0000-000000000001']}]}


@pytest.fixture
def service(tmp_path):
    return ec.ChainService(tmp_path / 'journal.sqlite3', Native(), quarantine=tmp_path / 'QUARANTINED.json')


def test_production_connection_cannot_enable_native_service(monkeypatch):
    monkeypatch.setenv('ROUTES_ERP_CHAIN_LOCAL_ROOT', str(ec.ROOT))
    with pytest.raises(RuntimeError):
        ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;')


def test_preview_does_not_dispatch_native_write(service):
    p = service.preview('qa', plan())
    assert p['order_count'] == 1
    assert [r['action'] for r in service.native.calls] == ['preview']


def test_changed_server_plan_refuses_before_native_write(service):
    p = service.preview('qa', plan())
    with pytest.raises(ec.ServiceError, match='փոխվել'):
        service.execute(p['id'], 'qa', plan(2))
    assert not any(r['action'] == 'execute' for r in service.native.calls)


def test_repeated_http_request_only_reads_existing_result(service):
    p = service.preview('qa', plan())
    assert service.execute(p['id'], 'qa', plan())['status'] == 'complete'
    restarted = ec.ChainService(service.journal, service.native, quarantine=service.quarantine)
    assert restarted.execute(p['id'], 'qa', plan(99))['status'] == 'complete'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_order_claim_prevents_new_token_revision_and_actor_bypass(service):
    p = service.preview('qa', plan())
    service.execute(p['id'], 'qa', plan())
    other = service.preview('another-admin', plan(2))
    with pytest.raises(ec.ServiceError) as error:
        service.execute(other['id'], 'another-admin', plan(2))
    assert error.value.code == 'claimed'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_concurrent_web_previews_cannot_claim_same_order_twice(service):
    a, b = [service.preview('qa', plan()) for _ in range(2)]
    def run(p):
        try:
            return service.execute(p['id'], 'qa', plan())['status']
        except ec.ServiceError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run, [a, b])) == ['claimed', 'complete']
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_unknown_native_outcome_never_releases_claim_or_replays(service):
    p = service.preview('qa', plan())
    service.native.failure = ec.ServiceError('unknown', 503)
    with pytest.raises(ec.ServiceError):
        service.execute(p['id'], 'qa', plan())
    assert service.execute(p['id'], 'qa', plan())['status'] == 'unknown'
    next_preview = service.preview('qa', plan(3))
    with pytest.raises(ec.ServiceError) as error:
        service.execute(next_preview['id'], 'qa', plan(3))
    assert error.value.code == 'claimed'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_proven_preflight_refusal_allows_new_review_but_never_replays_old_token(service):
    p = service.preview('qa', plan())
    service.native.failure = ec.ServiceError('changed', no_write=True)
    with pytest.raises(ec.ServiceError):
        service.execute(p['id'], 'qa', plan())
    assert service.execute(p['id'], 'qa', plan())['status'] == 'refused'
    service.native.failure = None
    newer = service.preview('qa', plan(2))
    assert service.execute(newer['id'], 'qa', plan(2))['status'] == 'complete'


def test_crash_after_web_claim_requires_readback_instead_of_relaunch(service):
    p = service.preview('qa', plan())
    service.native.failure = SystemExit('simulated process stop before response')
    with pytest.raises(SystemExit):
        service.execute(p['id'], 'qa', plan())
    restarted = ec.ChainService(service.journal, service.native, quarantine=service.quarantine)
    assert restarted.execute(p['id'], 'qa', plan())['status'] == 'unknown'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_web_journal_failure_after_store_never_replays_native(service):
    p = service.preview('qa', plan())
    with sqlite3.connect(service.journal) as db:
        db.execute("CREATE TRIGGER fail_complete BEFORE UPDATE OF status ON previews WHEN NEW.status='complete' BEGIN SELECT RAISE(ABORT,'disk fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.execute(p['id'], 'qa', plan())
    with sqlite3.connect(service.journal) as db:
        db.execute('DROP TRIGGER fail_complete')
    assert service.execute(p['id'], 'qa', plan())['status'] == 'complete'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1


def test_other_actor_cannot_inspect_operation(service):
    p = service.preview('qa', plan())
    with pytest.raises(ec.ServiceError) as error:
        service.status(p['id'], 'different')
    assert error.value.http_status == 404
    service.execute(p['id'], 'qa', plan())
    assert service.recent('different', DAY) == []
    assert service.recent('qa', DAY)[0]['label'].startswith('CAR1')


@pytest.fixture
def web(day, tmp_path):
    with day.session_transaction() as session:
        session['username'] = 'qa'
    day.state.erp_import_loader = lambda isns: (*day.state.erp_states_loader(isns), {})
    day.state.erp_warehouse_loader = lambda isns: {i.upper(): {'division': '000000'} for i in isns}
    day.people = {'CAR1': (9, 'Արամ Պետրոսյան'), 'CAR2': (10, 'Գագիկ Սարգսյան')}
    day.state.crew_facts = SimpleNamespace(terminal_people=lambda wanted: dict(day.people))
    day.state.erp_chain = ec.ChainService(tmp_path / 'web.sqlite3', Native(), quarantine=tmp_path / 'QUARANTINED.json')
    _sent(day)
    options = day.get('/api/routes/dispatch/erp-chain/options?date=' + DAY).get_json()
    day.chain_csrf = options['csrf']
    day.chain_trips = [r['id'] for r in options['trips']]
    return day


def preview(web, **changes):
    return web.post('/api/routes/dispatch/erp-chain/preview', json={
        'date': DAY, 'trips': web.chain_trips, 'csrf': web.chain_csrf, **changes})


def test_api_is_disabled_by_default(day):
    assert day.get('/api/routes/dispatch/erp-chain/options?date=' + DAY).status_code == 404


def test_non_admin_cannot_preview_or_get_status(web):
    web.role['value'] = 'garage'
    assert preview(web).status_code == 403
    assert web.get('/api/routes/dispatch/erp-chain/status/' + str(uuid4())).status_code == 403


def test_csrf_is_required_before_native_preview(web):
    native = web.state.erp_chain.native
    before = len(native.calls)
    assert preview(web, csrf='wrong').status_code == 403
    assert len(native.calls) == before


@pytest.mark.parametrize('trips', [[{}], ['unknown'], []])
def test_api_rejects_invalid_or_unpublished_trip(web, trips):
    assert preview(web, trips=trips).status_code in (400, 409)
    assert not any(r['action'] == 'preview' for r in web.state.erp_chain.native.calls)


def test_browser_cannot_supply_its_own_order_or_courier(web):
    response = preview(web, orders=[str(uuid4())], courier='B-ATTACK')
    assert response.status_code == 200, response.get_json()
    request = web.state.erp_chain.native.calls[-1]['plan']
    assert all(load['courier'] != 'B-ATTACK' for load in request['loads'])
    assert sum(len(load['orders']) for load in request['loads']) >= 1


def test_execution_requires_existing_superadmin_approval(web):
    p = preview(web).get_json()['result']
    response = web.post('/api/routes/dispatch/erp-chain/execute',
                        json={'id': p['id'], 'csrf': web.chain_csrf}, headers={'X-Superadmin-Password': ''})
    assert response.status_code == 403
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


def test_successful_api_replay_reads_without_second_write(web):
    p = preview(web).get_json()['result']
    body = {'id': p['id'], 'csrf': web.chain_csrf}
    assert web.post('/api/routes/dispatch/erp-chain/execute', json=body).get_json()['result']['status'] == 'complete'
    assert web.post('/api/routes/dispatch/erp-chain/execute', json=body).get_json()['result']['status'] == 'complete'
    assert sum(r['action'] == 'execute' for r in web.state.erp_chain.native.calls) == 1


def test_changed_plan_revision_between_preview_and_confirm_never_launches_native(web):
    p = preview(web).get_json()['result']
    raw, revision = web.state.store.load_dispatch(DAY)
    assert web.state.store.save_dispatch(DAY, raw, 'qa', expected_rev=revision) == revision + 1
    response = web.post('/api/routes/dispatch/erp-chain/execute', json={'id': p['id'], 'csrf': web.chain_csrf})
    assert response.status_code == 409 and response.get_json()['code'] == 'changed'
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


def test_pin_login_after_send_does_not_refuse(web):
    web.state.store.save_truck_driver('CAR1', DAY, 'Կարեն Ավագյան', 'qa')
    response = preview(web)
    assert response.status_code == 200, response.get_json()


def test_quarantine_blocks_before_claim_or_native_execution(service):
    p = service.preview('qa', plan())
    service.quarantine.write_text('{"reason":"integrity check failed"}')
    with pytest.raises(ec.ServiceError) as error:
        service.execute(p['id'], 'qa', plan())
    assert error.value.code == 'quarantined'
    assert not any(r['action'] == 'execute' for r in service.native.calls)
    with sqlite3.connect(service.journal) as db:
        assert db.execute('SELECT COUNT(*) FROM order_claims').fetchone()[0] == 0


DRIVER1, DRIVER2 = 'Արամ Պետրոսյան', 'Գագիկ Սարգսյան'


def cards(web, *rows):
    """Карточки ERP для окна цепочки: (код, имя) или (код, имя, закрыта); обе машины открыты."""
    agents = {i: Agent(i, r[0], r[1], len(r) > 2 and r[2]) for i, r in enumerate(rows, 5000)}
    cars = {'CAR1': Car('CAR1', 'HOWO', False), 'CAR2': Car('CAR2', 'FORD', False)}
    loader = web.state.erp_states_loader
    web.state.erp_import_loader = lambda isns: (loader(isns)[0], agents, cars, {})


def split_trip(web, truck):
    """Отправленный рейс делится на два: последний магазин — вторым рейсом машины truck."""
    raw, rev = web.state.store.load_dispatch(DAY)
    sent = raw['sent']
    first = sent['plan']['trips'][0]
    second = {**copy.deepcopy(first), 'id': 2, 'truck': truck, 'stops': first['stops'][-1:]}
    first['stops'] = first['stops'][:-1]
    sent['plan']['trips'].append(second)
    for row in sent['orders'].values():
        if row[3] == second['stops'][0]:
            row[0] = truck
    assert web.state.store.save_dispatch(DAY, raw, 'qa', expected_rev=rev) == rev + 1


def pick(code, person=9):
    return {'code': code, 'person': person}


def refused(web, code, **changes):
    response = preview(web, **changes)
    assert response.status_code in (400, 409), response.get_json()
    assert response.get_json()['code'] == code
    assert not any(r['action'] == 'preview' for r in web.state.erp_chain.native.calls)
    return response


def previewed_loads(web, **changes):
    response = preview(web, **changes)
    assert response.status_code == 200, response.get_json()
    return web.state.erp_chain.native.calls[-1]['plan']['loads']


def previewed_couriers(web, **changes):
    return [load['courier'] for load in previewed_loads(web, **changes)]


def options(web, day_text=DAY):
    return web.get('/api/routes/dispatch/erp-chain/options?date=' + day_text)


def execute(web, token):
    return web.post('/api/routes/dispatch/erp-chain/execute', json={'id': token, 'csrf': web.chain_csrf})


def run_ok(web, **changes):
    token = preview(web, **changes).get_json()['result']['id']
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    return token


def homes(web, by_order):
    """Склад заказа номер n (1, 2, 3) — через тестовое направление с этим складом."""
    from route_optimizer.erp_chain_routes import DIVISION_WAREHOUSE
    DIVISION_WAREHOUSE.update({f'T{w}': w for w in by_order.values()})
    web.state.erp_warehouse_loader = lambda isns: {i.upper(): {'division': f'T{by_order[int(i[-1])]}'} for i in isns}


def facts_for_all(web, division):
    web.state.erp_warehouse_loader = lambda isns: {i.upper(): {'division': division} for i in isns}


def journal_rows(web, sql, *args):
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        return db.execute(sql, args).fetchall()


def bind(web, person, code, name=DRIVER1):
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        db.execute('INSERT INTO person_cards(person_id,name,code,updated_by) VALUES(?,?,?,?)', (person, name, code, 'test'))


def test_word_order_whitespace_and_case_resolve_the_card(web):
    cards(web, ('B003/27', ' ՊԵՏՐՈՍՅԱՆ   արամ '))
    assert previewed_couriers(web) == ['B003/27']


def test_no_login_gives_no_default_and_needs_a_code(web):
    web.people.clear()
    cards(web, ('B003/27', DRIVER1))
    row = options(web).get_json()['trucks'][0]
    assert (row['person'], row['person_name'], row['code'], row['name']) == (None, None, None, None)
    refused(web, 'courier_choice')


def test_no_login_accepts_a_code_chosen_by_hand(web):
    web.people.clear()
    cards(web, ('B003/27', DRIVER1))
    assert previewed_couriers(web, couriers={'CAR1': pick('B003/27', None)}) == ['B003/27']


def test_bound_code_beats_a_name_match(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', 'Ուրիշ Մարդ'))
    bind(web, 9, 'B010/5')
    assert options(web).get_json()['trucks'][0]['code'] == 'B010/5'
    assert previewed_couriers(web) == ['B010/5']


def test_closed_binding_falls_back_to_the_name_match(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', 'Ուրիշ Մարդ', True))
    bind(web, 9, 'B010/5')
    assert previewed_couriers(web) == ['B003/27']


def test_two_name_matches_without_a_binding_need_a_choice(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', 'Պետրոսյան Արամ'))
    assert options(web).get_json()['trucks'][0]['code'] is None
    refused(web, 'courier_choice')


def test_two_cards_accept_one_of_them(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', 'Պետրոսյան Արամ'))
    assert previewed_couriers(web, couriers={'CAR1': pick('B010/5')}) == ['B010/5']


def test_operator_can_override_a_default_with_any_open_card(web):
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    assert previewed_couriers(web, couriers={'CAR1': pick('B777/1')}) == ['B777/1']


def test_no_match_accepts_any_open_courier_card(web):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'))
    assert previewed_couriers(web, couriers={'CAR1': pick('B777/1')}) == ['B777/1']


def test_no_match_without_a_choice_is_asked_for_it(web):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'))
    refused(web, 'courier_choice')


@pytest.mark.parametrize('card', [('B888/1', 'Փակված Քարտ', True), ('A008/6', 'Մենեջեր Քարտ'), ('B404/1', 'Քարտ', True)])
def test_closed_and_manager_cards_are_refused(web, card):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'), card)
    refused(web, 'directory', couriers={'CAR1': pick(card[0])})


def test_unknown_code_is_refused(web):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'))
    refused(web, 'directory', couriers={'CAR1': pick('B-ATTACK')})


def test_submitting_the_default_is_accepted(web):
    cards(web, ('B003/27', DRIVER1))
    assert previewed_couriers(web, couriers={'CAR1': pick('B003/27')}) == ['B003/27']


@pytest.mark.parametrize('bad', ['B003/27', ['B003/27'], {'CAR1': 7}, {'CAR1': None}, {'CAR1': 'B003/27'},
                                 {'CAR1': {'code': 'B003/27'}}, {'CAR1': {'person': 9}}, {'CAR1': {'code': 7, 'person': 9}},
                                 {'CAR1': {'code': 'B003/27', 'person': 'x'}}, {'CAR1': {'code': 'B003/27', 'person': True}}])
def test_malformed_couriers_are_bad_request(web, bad):
    cards(web, ('B003/27', DRIVER1))
    refused(web, 'bad_request', couriers=bad)


def test_code_for_an_unselected_truck_is_ignored(web):
    cards(web, ('B003/27', DRIVER1))
    assert previewed_couriers(web, couriers={'CAR9': pick('B-OTHER', None)}) == ['B003/27']


def test_person_changed_while_the_dialog_was_open_refuses(web):
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    web.people['CAR1'] = (11, 'Ուրիշ Մարդ')
    response = refused(web, 'person_changed', couriers={'CAR1': pick('B003/27', 9)})
    assert response.get_json()['error'] == 'Վարորդը փոխվել է․ նորից բացեք պատուհանը։'


def test_person_changed_with_an_untouched_empty_select_refuses(web):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'))
    web.people['CAR1'] = (11, 'Ուրիշ Մարդ')
    refused(web, 'person_changed', couriers={'CAR1': {'code': '', 'person': 9}})


def test_login_appearing_while_the_dialog_was_open_refuses(web):
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    refused(web, 'person_changed', couriers={'CAR1': pick('B777/1', None)})


def test_empty_code_with_the_same_person_uses_the_default(web):
    cards(web, ('B003/27', DRIVER1))
    assert previewed_couriers(web, couriers={'CAR1': {'code': '', 'person': 9}}) == ['B003/27']


def test_courier_read_failure_means_no_person(web):
    def broken(day_text):
        raise RuntimeError('courier.db is locked')
    web.state.crew_facts = SimpleNamespace(terminal_people=broken)
    cards(web, ('B003/27', DRIVER1))
    response = options(web)
    assert response.status_code == 200 and response.get_json()['trucks'][0]['person'] is None
    refused(web, 'courier_choice')


def test_without_courier_facts_there_is_no_person(web):
    web.state.crew_facts = None
    cards(web, ('B003/27', DRIVER1))
    assert options(web).get_json()['trucks'][0]['code'] is None


def test_same_code_on_two_trucks_is_refused(web):
    split_trip(web, 'CAR2')
    web.people.pop('CAR2')
    cards(web, ('B003/27', DRIVER1))
    refused(web, 'courier_twice', trips=['1', '2'], couriers={'CAR2': pick('B003/27', None)})


def test_different_codes_on_two_trucks_are_allowed(web):
    split_trip(web, 'CAR2')
    cards(web, ('B003/27', DRIVER1), ('B002/1', 'Սարգսյան Գագիկ'))
    assert previewed_couriers(web, trips=['1', '2']) == ['B003/27', 'B002/1']


def test_two_trips_of_one_truck_share_the_code(web):
    split_trip(web, 'CAR1')
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    assert previewed_couriers(web, trips=['1', '2'], couriers={'CAR1': pick('B010/5')}) == ['B010/5', 'B010/5']


def test_pin_derived_card_of_another_truck_is_refused_even_if_not_selected(web):
    split_trip(web, 'CAR2')
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    refused(web, 'courier_twice', trips=['2'], couriers={'CAR2': pick('B003/27', 10)})


def test_name_of_a_truck_without_a_pin_refuses_nothing(web):
    split_trip(web, 'CAR2')
    web.people.pop('CAR1')
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    assert previewed_couriers(web, trips=['2'], couriers={'CAR2': pick('B003/27', 10)}) == ['B003/27']


def test_earlier_run_blocks_the_same_code_on_another_car(web):
    split_trip(web, 'CAR2')
    cards(web, ('B777/1', 'Ուրիշ Մարդ'))
    run_ok(web, trips=['1'], couriers={'CAR1': pick('B777/1')})
    web.state.erp_chain.native.calls.clear()
    refused(web, 'courier_twice', trips=['2'], couriers={'CAR2': pick('B777/1', 10)})


def test_two_cards_on_one_truck_across_runs_are_allowed(web):
    split_trip(web, 'CAR1')
    cards(web, ('B777/1', 'Ուրիշ Մարդ'), ('B778/1', 'Երրորդ Մարդ'))
    run_ok(web, trips=['1'], couriers={'CAR1': pick('B777/1')})
    web.people['CAR1'] = (11, 'Երրորդ Մարդ')
    assert previewed_couriers(web, trips=['2'], couriers={'CAR1': pick('B778/1', 11)}) == ['B778/1']


def test_refused_earlier_run_does_not_block_another_code(web):
    cards(web, ('B777/1', 'Ուրիշ Մարդ'), ('B778/1', 'Երրորդ Մարդ'))
    token = preview(web, couriers={'CAR1': pick('B777/1')}).get_json()['result']['id']
    web.state.erp_chain.native.failure = ec.ServiceError('busy', no_write=True)
    assert execute(web, token).status_code == 409
    web.state.erp_chain.native.failure = None
    assert previewed_couriers(web, couriers={'CAR1': pick('B778/1')}) == ['B778/1']


def test_execute_rechecks_cards_inside_the_claim_transaction(service):
    first = service.preview('qa', plan())
    assert service.execute(first['id'], 'qa', plan())['status'] == 'complete'
    other = plan()
    other['loads'][0].update(car='CAR2', trip='2', orders=['00000000-0000-0000-0000-000000000002'])
    second = service.preview('qa', other)
    with pytest.raises(ec.ServiceError) as error:
        service.execute(second['id'], 'qa', other)
    assert error.value.code == 'courier_twice'
    assert sum(r['action'] == 'execute' for r in service.native.calls) == 1
    with sqlite3.connect(service.journal) as db:
        assert db.execute('SELECT COUNT(*) FROM order_claims WHERE operation_id=?', (second['id'],)).fetchone()[0] == 0


def test_selected_truck_without_orders_needs_no_card_and_is_not_counted(web):
    split_trip(web, 'CAR2')
    raw, rev = web.state.store.load_dispatch(DAY)
    moved = raw['sent']['plan']['trips'][1]['stops'][0]
    raw['sent']['orders'] = {i: row for i, row in raw['sent']['orders'].items() if row[3] != moved}
    web.state.store.save_dispatch(DAY, raw, 'qa', expected_rev=rev)
    web.people.pop('CAR2')
    cards(web, ('B003/27', DRIVER1))
    loads = previewed_loads(web, trips=['1', '2'])
    assert [load['courier'] for load in loads] == ['B003/27'] and loads[0]['trip'] == '1/000'


def test_binding_is_written_when_the_run_completes_and_not_at_preview(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    assert journal_rows(web, 'SELECT * FROM person_cards') == []
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    assert journal_rows(web, 'SELECT person_id,name,code,updated_by FROM person_cards') == [(9, DRIVER1, 'B010/5', 'qa')]


def test_binding_is_not_rewritten_when_the_code_is_the_same(web):
    cards(web, ('B003/27', DRIVER1))
    bind(web, 9, 'B003/27')
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        db.execute("UPDATE person_cards SET updated_at='2000-01-01 00:00:00'")
    run_ok(web)
    assert journal_rows(web, 'SELECT updated_at,updated_by FROM person_cards') == [('2000-01-01 00:00:00', 'test')]


def test_operator_override_does_not_change_an_existing_binding(web):
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    bind(web, 9, 'B003/27')
    run_ok(web, couriers={'CAR1': pick('B777/1')})
    assert journal_rows(web, 'SELECT person_id,code,updated_by FROM person_cards') == [(9, 'B003/27', 'test')]


def test_no_binding_without_a_known_person(web):
    web.people.clear()
    cards(web, ('B003/27', DRIVER1))
    run_ok(web, couriers={'CAR1': pick('B003/27', None)})
    assert journal_rows(web, 'SELECT * FROM person_cards') == []


def test_execute_refused_before_native_writes_no_binding(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    raw, rev = web.state.store.load_dispatch(DAY)
    web.state.store.save_dispatch(DAY, raw, 'qa', expected_rev=rev)
    assert execute(web, token).status_code == 409
    assert journal_rows(web, 'SELECT * FROM person_cards') == []


def fake_status(web, token, status):
    """Native отвечает этим статусом на execute и status."""
    native = web.state.erp_chain.native
    original = native.call
    def call(request):
        if request['action'] == 'execute' and request.get('id') == token:
            native.calls.append(copy.deepcopy(request))
            native.operations[token] = {'id': token, 'database': ec.DATABASE, 'status': status, 'steps': []}
            return native.operations[token]
        return original(request)
    native.call = call


@pytest.mark.parametrize('status', ['refused', 'partial', 'unknown', 'running'])
def test_outcomes_short_of_complete_do_not_bind(web, status):
    cards(web, ('B003/27', DRIVER1))
    token = preview(web).get_json()['result']['id']
    fake_status(web, token, status)
    execute(web, token)
    assert journal_rows(web, 'SELECT * FROM person_cards') == []


@pytest.mark.parametrize('failure', [ec.ServiceError('writes_disabled', no_write=True), ec.ServiceError('unknown', 503)])
def test_native_refusal_does_not_bind(web, failure):
    cards(web, ('B003/27', DRIVER1))
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain.native.failure = failure
    assert execute(web, token).status_code in (409, 503)
    assert journal_rows(web, 'SELECT * FROM person_cards') == []


def test_binding_follows_a_status_read_that_finds_the_run_complete_once(web):
    cards(web, ('B003/27', DRIVER1))
    token = preview(web).get_json()['result']['id']
    native = web.state.erp_chain.native
    native.failure = ec.ServiceError('unknown', 503)
    assert execute(web, token).status_code == 503
    native.operations[token] = {'id': token, 'database': ec.DATABASE, 'status': 'complete', 'steps': []}
    assert web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']['status'] == 'complete'
    assert journal_rows(web, 'SELECT person_id,code FROM person_cards') == [(9, 'B003/27')]
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        db.execute("UPDATE person_cards SET code='B999/9'")
    web.get('/api/routes/dispatch/erp-chain/status/' + token)
    assert journal_rows(web, 'SELECT code FROM person_cards') == [('B999/9',)]


def test_code_bound_to_another_person_is_not_rebound(web):
    cards(web, ('B003/27', DRIVER1), ('B777/1', 'Ուրիշ Մարդ'))
    bind(web, 10, 'B777/1', DRIVER2)
    run_ok(web, couriers={'CAR1': pick('B777/1')})
    assert journal_rows(web, 'SELECT person_id,code FROM person_cards') == [(10, 'B777/1')]


def test_two_load_partial_reads_honestly_and_the_plan_can_be_released(web):
    homes(web, {1: '000', 2: '015', 3: '000'})
    cards(web, ('B003/27', DRIVER1))
    token = preview(web).get_json()['result']['id']
    plan_loads = json.loads(web.state.erp_chain.get(token, 'qa')['plan'])['loads']
    first, second = plan_loads
    native = web.state.erp_chain.native
    outcome = {'id': token, 'database': ec.DATABASE, 'status': 'partial',
               'loads': [{'trip': x['trip'], 'warehouse': x['warehouse'], 'orders': [{'isn': i} for i in x['orders']]} for x in plan_loads],
               'steps': [{'kind': 'providing', 'isn': str(uuid4()), 'number': 'P-1', 'status': 'verified', 'observed_registered': True,
                          'order_isns': first['orders']}]}
    original = native.call
    def call(request):
        action = request['action']
        if action == 'execute':
            native.calls.append(copy.deepcopy(request))
            native.operations[token] = outcome
            return outcome
        if action == 'reconcile-preview':
            raise ec.ServiceError('providing_missing')
        return original(request)
    native.call = call
    result = execute(web, token).get_json()['result']
    assert result['status'] == 'partial' and result.get('no_write_dispatched') is not True
    rows = [(s['warehouse'], s['status'], s.get('number')) for s in result['steps'] if s['kind'] == 'providing']
    assert rows == [('000', 'verified', 'P-1'), ('015', 'missing', None)]
    assert guard.held_operations(web.state.store) == [token] and journal_rows(web, 'SELECT * FROM person_cards') == []
    status = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert [(s['warehouse'], s['status']) for s in status['steps'] if s['kind'] == 'providing'] == [('000', 'verified'), ('015', 'missing')]
    assert 'plan_released' not in status
    response = web.post('/api/routes/dispatch/erp-chain/reconcile-preview', json={'id': token, 'csrf': web.chain_csrf})
    body = response.get_json()
    assert response.status_code == 409 and body['code'] == 'providing_missing' and 'ՀԾ-ում' in body['error'] and 'Ազատել պլանը' in body['error']
    released = web.post('/api/routes/dispatch/erp-chain/release-plan', json={
        'id': token, 'reason': 'ՀԾ-ում փաստաթղթերը ձեռքով ստուգված են', 'csrf': web.chain_csrf})
    assert released.status_code == 200 and released.get_json()['result']['plan_released'] is True
    assert guard.held_operations(web.state.store) == []
    assert len(journal_rows(web, 'SELECT * FROM order_claims')) == 3 and journal_rows(web, 'SELECT * FROM person_cards') == []
    after = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert after['status'] == 'partial' and after['plan_released'] is True


def test_complete_run_adds_no_missing_rows(web):
    homes(web, {1: '000', 2: '015', 3: '000'})
    token = preview(web).get_json()['result']['id']
    fake_status(web, token, 'complete')
    assert execute(web, token).get_json()['result']['steps'] == []


def test_renamed_card_changes_the_digest_between_preview_and_execute(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    cards(web, ('B003/27', DRIVER1), ('B010/5', 'Արամ Պետրոսյան Նոր'))
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == 'changed'
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


def test_execute_rebuild_keeps_the_chosen_code_and_signature(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    record = web.state.erp_chain.get(token, 'qa')
    load = json.loads(record['plan'])['loads'][0]
    assert (load['courier'], load['courier_name'], load['person'], load['person_name']) == ('B010/5', DRIVER1, 9, DRIVER1)
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    assert web.state.erp_chain.get(token, 'qa')['signature'] == ec.digest(json.loads(record['plan']))
    assert sum(r['action'] == 'execute' for r in web.state.erp_chain.native.calls) == 1


def test_execute_refuses_when_another_person_logged_in_after_preview(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    web.people['CAR1'] = (11, 'Ուրիշ Մարդ')
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == 'person_changed'
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


def test_execute_refuses_when_the_chosen_card_was_closed_after_preview(web):
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1))
    token = preview(web, couriers={'CAR1': pick('B010/5')}).get_json()['result']['id']
    cards(web, ('B003/27', DRIVER1), ('B010/5', DRIVER1, True))
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == 'directory'
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


def test_two_warehouses_on_one_trip_give_two_loads(web):
    homes(web, {1: '000', 2: '015', 3: '000'})
    loads = previewed_loads(web)
    assert [(x['trip'], x['warehouse'], x['car']) for x in loads] == [('1/000', '000', 'CAR1'), ('1/015', '015', 'CAR1')]
    assert [x['orders'] for x in loads] == [[_isn(1), _isn(3)], [_isn(2)]]
    assert {x['courier'] for x in loads} == {'B001/1'}


def test_browser_warehouse_is_ignored(web):
    homes(web, {1: '015', 2: '015', 3: '015'})
    assert [x['warehouse'] for x in previewed_loads(web, warehouse='000')] == ['015']


@pytest.mark.parametrize('division,warehouse', [('000000', '000'), ('000003', '016'), ('000004', '007'), ('000006', '007'),
                                                ('000007', '000'), ('000012', '000'), ('000014', '000'), ('000017', '015')])
def test_order_warehouse_comes_from_its_division(web, division, warehouse):
    facts_for_all(web, division)
    assert [x['warehouse'] for x in previewed_loads(web)] == [warehouse]


def test_trip_mixing_two_divisions_gives_two_loads(web):
    divisions = {1: '000000', 2: '000017', 3: '000000'}
    web.state.erp_warehouse_loader = lambda isns: {i.upper(): {'division': divisions[int(i[-1])]} for i in isns}
    loads = previewed_loads(web)
    assert [(x['trip'], x['warehouse']) for x in loads] == [('1/000', '000'), ('1/015', '015')]
    assert [x['orders'] for x in loads] == [[_isn(1), _isn(3)], [_isn(2)]]


@pytest.mark.parametrize('division', ['000099', ''])
def test_order_of_unknown_division_is_refused_with_its_number(web, division):
    facts_for_all(web, division)
    response = refused(web, 'order_warehouse')
    assert response.get_json()['error'].startswith('Պատվերի պահեստը հնարավոր չէ որոշել։') and 'N1' in response.get_json()['error']


def test_order_facts_come_from_the_document_division_only(monkeypatch):
    from route_optimizer import erp
    first, second = '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'
    queries = []
    def select(conn, sql, params=()):
        queries.append((sql, list(params)))
        if 'DOCUMENTSLOG' in sql:
            return [(first, '000000053287 ', 15, datetime(2026, 11, 11, 9, 47, 31))]
        if 'DOCPARENTS' in sql:
            assert 'fDOCTYPE <> 29' in sql
            return [(first,)]
        if 'TREES' in sql:
            return [('000000', 'Ջուր')]
        if 'HIRESERVATION' in sql:
            return [(second, '000 '), (second, '015')]
        return [(first, '000000'), (second, '000017')]
    monkeypatch.setattr(erp, 'connect', lambda cs: object())
    monkeypatch.setattr(erp, 'close_quietly', lambda conn: None)
    monkeypatch.setattr(erp, '_select', select)
    facts = erp.load_order_warehouses('x', [first, second])
    assert facts == {first.upper(): {'division': '000000', 'division_name': 'Ջուր', 'documents': True, 'reserved': [],
                                     'providings': [{'number': '000000053287', 'user': 15, 'made': '2026-11-11 09:47'}]},
                     second.upper(): {'division': '000017', 'division_name': '', 'documents': False, 'reserved': ['000', '015'],
                                      'providings': []}}
    assert sum('HIRESERVATION' in sql for sql, _ in queries) == 1
    assert [params for sql, params in queries if 'DOCUMENTSLOG' in sql] == [[first.upper()]]  # только оформленные заказы


def test_preview_reports_the_current_writes_state_in_both_directions(web):
    native = web.state.erp_chain.native
    original = native.call
    gate = {'writes_enabled': False, 'max_orders': 0}
    native.call = lambda request: {**original(request), **gate} if request['action'] == 'options' else original(request)
    assert preview(web).get_json()['target'] == {'database': ec.DATABASE, 'environment': None, 'writes_enabled': False, 'max_orders': 0}
    gate.update(writes_enabled=True, max_orders=3)
    assert preview(web).get_json()['target']['writes_enabled'] is True
    gate.update(writes_enabled=False, max_orders=0)
    target = preview(web).get_json()['target']
    assert target['writes_enabled'] is False and target['max_orders'] == 0


@pytest.mark.parametrize('storage', ['006', '011', '999'])
def test_warehouse_native_would_refuse_names_the_order(web, storage):
    homes(web, {1: storage, 2: '000', 3: '000'})
    response = refused(web, 'order_warehouse')
    assert 'N1' in response.get_json()['error'] and 'N2' not in response.get_json()['error']


def test_composite_id_survives_execute_rebuild_and_status(web):
    homes(web, {1: '000', 2: '015', 3: '000'})
    token = preview(web).get_json()['result']['id']
    assert [x['trip'] for x in json.loads(web.state.erp_chain.get(token, 'qa')['plan'])['loads']] == ['1/000', '1/015']
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    assert web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']['status'] == 'complete'
    assert options(web).get_json()['recent'][0]['id'] == token


def test_options_row_lists_every_open_card_with_the_default(web):
    split_trip(web, 'CAR2')
    web.people.pop('CAR2')
    cards(web, ('B003/27', 'Պետրոսյան Արամ'), ('B777/1', 'Ուրիշ Մարդ'), ('B778/1', 'Փակված Քարտ', True), ('A008/6', 'Մենեջեր Քարտ'))
    everyone = [{'code': 'B003/27', 'name': 'Պետրոսյան Արամ'}, {'code': 'B777/1', 'name': 'Ուրիշ Մարդ'}]
    assert options(web).get_json()['trucks'] == [
        {'car': 'CAR1', 'person': 9, 'person_name': DRIVER1, 'code': 'B003/27', 'name': 'Պետրոսյան Արամ', 'candidates': everyone},
        {'car': 'CAR2', 'person': None, 'person_name': None, 'code': None, 'name': None, 'candidates': everyone}]


def test_options_survive_an_erp_read_failure(web):
    def down(isns):
        raise ErpError('down')
    web.state.erp_import_loader = down
    response = options(web)
    assert response.status_code == 200 and response.get_json()['trucks'] == []
    assert response.get_json()['trips'] == [{'id': '1', 'car': 'CAR1', 'stops': 3}]


def test_options_read_orders_and_cards_in_one_call(web):
    asked = []
    loader = web.state.erp_import_loader
    web.state.erp_import_loader = lambda isns: asked.append(sorted(isns)) or loader(isns)
    assert options(web).status_code == 200
    assert asked == [[_isn(1), _isn(2), _isn(3)]]


def test_options_without_a_sent_plan_have_no_truck_rows(web):
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain.native.failure = ec.ServiceError('unknown', 503)
    assert execute(web, token).status_code == 503
    body = options(web, '2026-10-02').get_json()
    assert body['trips'] == [] and body['trucks'] == [] and [r['id'] for r in body['recent']] == [token]


def stale_lease(web, minutes):
    token = preview(web).get_json()['result']['id']
    record = web.state.erp_chain.get(token, 'qa')
    assert guard.acquire(web.state.store, json.loads(record['plan']), token)
    web.state.store._transaction(lambda db: db.execute(
        "UPDATE erp_chain_plan_lease SET created_at=datetime('now',?)", (f'-{minutes} minutes',)), 'test')
    return token


def test_ready_lease_is_released_once_older_than_twelve_minutes(web):
    token = stale_lease(web, 13)
    status = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert status['stale_lease'] is True and status['status'] == 'preview'
    response = web.post('/api/routes/dispatch/erp-chain/release-plan', json={
        'id': token, 'reason': 'ՀԾ-ում փաստաթղթերը ձեռքով ստուգված են', 'csrf': web.chain_csrf})
    assert response.status_code == 200 and response.get_json()['result']['plan_released'] is True
    assert guard.held_operations(web.state.store) == []
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        assert db.execute('SELECT actor FROM plan_lease_releases WHERE operation_id=?', (token,)).fetchone()[0] == 'qa'


def test_young_ready_lease_is_not_released(web):
    token = stale_lease(web, 11)
    status = web.get('/api/routes/dispatch/erp-chain/status/' + token).get_json()['result']
    assert 'stale_lease' not in status
    response = web.post('/api/routes/dispatch/erp-chain/release-plan', json={
        'id': token, 'reason': 'ՀԾ-ում փաստաթղթերը ձեռքով ստուգված են', 'csrf': web.chain_csrf})
    assert response.status_code == 409 and response.get_json()['code'] == 'release_not_allowed'
    assert guard.held_operations(web.state.store) == [token]


def test_retention_delete_passes_a_lease_older_than_the_window(web):
    store = web.state.store
    token = preview(web).get_json()['result']['id']
    record = json.loads(web.state.erp_chain.get(token, 'qa')['plan'])
    assert guard.acquire(store, record, token)
    assert store.save_dispatch('2000-01-01', {}, 'qa') == 1
    store._transaction(lambda db: db.execute("UPDATE erp_chain_plan_lease SET day='2000-01-01'"), 'test')
    assert store.save_dispatch('2999-01-01', {}, 'qa') == 1
    assert store.load_dispatch('2000-01-01') is None


def test_delete_inside_the_window_stays_blocked(web):
    store = web.state.store
    token = preview(web).get_json()['result']['id']
    record = json.loads(web.state.erp_chain.get(token, 'qa')['plan'])
    assert guard.acquire(store, record, token)
    near = (date.today() + timedelta(days=1)).isoformat()
    assert store.save_dispatch(near, {}, 'qa') == 1
    store._transaction(lambda db: db.execute('UPDATE erp_chain_plan_lease SET day=?', (near,)), 'test')
    with pytest.raises(guard.PlanLocked):
        store.delete_dispatch(near, expected_rev=1)


def test_far_future_save_is_not_refused_by_an_old_lease(web):
    store = web.state.store
    token = preview(web).get_json()['result']['id']
    assert guard.acquire(store, json.loads(web.state.erp_chain.get(token, 'qa')['plan']), token)
    assert store.save_dispatch('2999-01-01', {}, 'qa') == 1
    assert store.load_dispatch(DAY) is not None and guard.held_operations(store) == [token]


def with_divisions(web, by_order, documents=()):
    web.state.erp_warehouse_loader = lambda isns: {
        i.upper(): {'division': by_order.get(int(i[-1]), '000000'), 'documents': int(i[-1]) in documents} for i in isns}


def test_override_moves_one_order_to_a_second_load(web):
    loads = previewed_loads(web, warehouses={_isn(2): '015'})
    assert [(x['trip'], x['warehouse'], x['orders']) for x in loads] == [('1/000', '000', [_isn(1), _isn(3)]), ('1/015', '015', [_isn(2)])]


def test_group_override_moves_several_orders(web):
    loads = previewed_loads(web, warehouses={_isn(1): '015', _isn(2): '015'})
    assert [(x['warehouse'], x['orders']) for x in loads] == [('000', [_isn(3)]), ('015', [_isn(1), _isn(2)])]


def test_override_back_to_the_division_value_changes_nothing(web):
    assert [x['warehouse'] for x in previewed_loads(web, warehouses={_isn(1): '000'})] == ['000']


def test_override_keys_ignore_case(web):
    lower = _isn(2).lower()
    assert [x['warehouse'] for x in previewed_loads(web, warehouses={lower: '015'})] == ['000', '015']


def test_unknown_division_passes_with_an_override_and_refuses_without(web):
    with_divisions(web, {2: '000099'})
    response = refused(web, 'order_warehouse')
    assert 'N2' in response.get_json()['error'] and 'N1' not in response.get_json()['error']
    assert [x['warehouse'] for x in previewed_loads(web, warehouses={_isn(2): '015'})] == ['000', '015']


def test_override_outside_native_list_is_refused_with_the_number(web):
    response = refused(web, 'order_warehouse', warehouses={_isn(2): '999'})
    assert 'N2' in response.get_json()['error'] and 'N1' not in response.get_json()['error']


def test_override_for_an_order_outside_the_selection_is_bad_request(web):
    split_trip(web, 'CAR2')
    cards(web, ('B003/27', DRIVER1), ('B002/1', DRIVER2))
    refused(web, 'bad_request', trips=['1'], warehouses={_isn(3): '015'})


@pytest.mark.parametrize('bad', ['015', ['015'], {_isn(1): 15}, {_isn(1): None}])
def test_malformed_overrides_are_bad_request(web, bad):
    refused(web, 'bad_request', warehouses=bad)


def test_execute_rebuild_keeps_overrides_and_the_digest_even_if_the_division_changes(web):
    token = preview(web, warehouses={_isn(2): '015'}).get_json()['result']['id']
    record = web.state.erp_chain.get(token, 'qa')
    with_divisions(web, {1: '000017', 2: '000017', 3: '000017'})
    assert execute(web, token).get_json()['result']['status'] == 'complete'
    assert web.state.erp_chain.get(token, 'qa')['signature'] == ec.digest(json.loads(record['plan']))


def test_execute_refuses_a_stored_warehouse_native_no_longer_offers(web):
    token = preview(web, warehouses={_isn(2): '015'}).get_json()['result']['id']
    native = web.state.erp_chain.native
    original = native.call
    native.call = lambda request: {**original(request), 'warehouses': [{'code': '000', 'name': 'x'}]} if request['action'] == 'options' else original(request)
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == 'order_warehouse' and 'N2' in response.get_json()['error']
    assert not any(r['action'] == 'execute' for r in native.calls)


def test_overrides_are_not_remembered_for_the_next_preview(web):
    previewed_loads(web, warehouses={_isn(2): '015'})
    assert [x['warehouse'] for x in previewed_loads(web)] == ['000']


def test_options_list_the_orders_with_their_warehouse(web):
    with_divisions(web, {2: '000017', 3: '000099'})
    body = options(web).get_json()
    assert body['orders_left_out'] == 0
    assert [(o['isn'], o['number'], o['car'], o['trip'], o['division'], o['warehouse']) for o in body['orders']] == [
        (_isn(1), 'N1', 'CAR1', '1', '000000', '000'), (_isn(2), 'N2', 'CAR1', '1', '000017', '015'), (_isn(3), 'N3', 'CAR1', '1', '000099', None)]
    assert all(o['store'] for o in body['orders'])


def test_options_leave_out_orders_with_documents_and_count_them(web):
    with_divisions(web, {}, documents={2})
    body = options(web).get_json()
    assert [o['isn'] for o in body['orders']] == [_isn(1), _isn(3)] and body['orders_left_out'] == 1


def test_order_list_is_split_by_trip(web):
    split_trip(web, 'CAR2')
    assert [(o['isn'], o['car'], o['trip']) for o in options(web).get_json()['orders']] == [
        (_isn(1), 'CAR1', '1'), (_isn(2), 'CAR1', '1'), (_isn(3), 'CAR2', '2')]


def test_options_survive_a_failed_order_read(web):
    def down(isns):
        raise ErpError('down')
    web.state.erp_warehouse_loader = down
    response = options(web)
    assert response.status_code == 200 and response.get_json()['orders'] is None
    assert response.get_json()['trucks'] and response.get_json()['trips']


def test_options_survive_a_failed_erp_read_for_the_list_too(web):
    def down(isns):
        raise ErpError('down')
    web.state.erp_import_loader = down
    body = options(web).get_json()
    assert body['orders'] is None and body['orders_left_out'] == 0 and body['trucks'] == []


def test_division_names_come_from_the_division_tree():
    from route_optimizer import erp
    assert "fTREEID = 'Division'" in erp.SQL_DIVISION_NAMES and 'WITH (NOLOCK)' in erp.SQL_DIVISION_NAMES
    erp.check_sql(erp.SQL_DIVISION_NAMES)


def test_failed_division_name_read_blocks_nothing(monkeypatch):
    from route_optimizer import erp
    isn = '00000000-0000-0000-0000-000000000001'
    def select(conn, sql, params=()):
        if 'TREES' in sql:
            raise erp.ErpError('names are down')
        return [] if 'DOCPARENTS' in sql or 'HIRESERVATION' in sql else [(isn, '000000')]
    monkeypatch.setattr(erp, 'connect', lambda cs: object())
    monkeypatch.setattr(erp, 'close_quietly', lambda conn: None)
    monkeypatch.setattr(erp, '_select', select)
    assert erp.load_order_warehouses('x', [isn])[isn.upper()] == {'division': '000000', 'division_name': '', 'documents': False,
                                                                  'reserved': [], 'providings': []}


def test_options_return_the_division_name_and_leave_a_missing_one_empty(web):
    web.state.erp_warehouse_loader = lambda isns: {
        i.upper(): {'division': '000017' if i.endswith('2') else '000000', 'division_name': '' if i.endswith('2') else 'Ջուր'} for i in isns}
    rows = options(web).get_json()['orders']
    assert [(o['division'], o['division_name']) for o in rows] == [('000000', 'Ջուր'), ('000017', ''), ('000000', 'Ջուր')]


def with_reserved(web, reserved, divisions=None, documents=()):
    """Склады удерживаемого резерва заказа номер n; направление по умолчанию 000000 (склад 000)."""
    divisions = divisions or {}
    web.state.erp_warehouse_loader = lambda isns: {
        i.upper(): {'division': divisions.get(int(i[-1]), '000000'), 'documents': int(i[-1]) in documents,
                    'reserved': reserved.get(int(i[-1]), [])} for i in isns}


def test_reservation_in_another_warehouse_demands_a_choice(web):
    with_reserved(web, {2: ['015']})
    response = refused(web, 'reserve_choice')
    assert 'N2' in response.get_json()['error'] and 'N1' not in response.get_json()['error']


@pytest.mark.parametrize('choice,expected', [('015', ['000', '015']), ('000', ['000'])])
def test_any_explicit_choice_settles_a_reservation_elsewhere(web, choice, expected):
    with_reserved(web, {2: ['015']})
    assert [x['warehouse'] for x in previewed_loads(web, warehouses={_isn(2): choice})] == expected


def test_reservation_in_the_division_warehouse_needs_no_choice(web):
    with_reserved(web, {1: ['000'], 2: ['000']})
    assert [x['warehouse'] for x in previewed_loads(web)] == ['000']


def test_reservation_split_over_two_warehouses_demands_a_choice(web):
    with_reserved(web, {3: ['000', '015']})
    assert 'N3' in refused(web, 'reserve_choice').get_json()['error']


def test_order_with_documents_reserved_elsewhere_blocks_nothing(web):
    with_reserved(web, {2: ['015']}, documents={2})
    assert [x['warehouse'] for x in previewed_loads(web)] == ['000']


def test_options_mark_orders_reserved_elsewhere(web):
    with_reserved(web, {1: ['000'], 2: ['015']}, {3: '000099'})
    rows = options(web).get_json()['orders']
    assert [(o['warehouse'], o['reserved'], o['reserved_elsewhere']) for o in rows] == [
        ('000', ['000'], False), ('000', ['015'], True), (None, [], False)]


def test_execute_after_a_settled_choice_completes(web):
    with_reserved(web, {2: ['015']})
    token = preview(web, warehouses={_isn(2): '000'}).get_json()['result']['id']
    assert execute(web, token).get_json()['result']['status'] == 'complete'


def test_held_reservation_query_is_read_only_and_nets_the_credits():
    from route_optimizer import erp
    sql = erp.SQL_ORDER_HELD_RESERVATIONS
    erp.check_sql(sql)
    assert 'WITH (NOLOCK)' in sql and "h.fDBCR = 'D'" in sql and '-h.fQUANTITY' in sql and ') > 0' in sql and 'h.fPRODUCTID' in sql


def with_documents(web, providings, documents=None):
    """Заказ номер n оформлен; его տրամադրում — providings[n] (список словарей загрузчика)."""
    documents = set(providings) if documents is None else documents
    web.state.erp_warehouse_loader = lambda isns: {
        i.upper(): {'division': '000000', 'documents': int(i[-1]) in documents,
                    'providings': providings.get(int(i[-1]), [])} for i in isns}


P15 = {'number': '000000053287', 'user': 15, 'made': '2026-11-11 09:47'}


def test_options_summarise_documented_orders_per_trip(web):
    with_documents(web, {1: [P15], 2: [P15]})
    body = options(web).get_json()
    assert [o['isn'] for o in body['orders']] == [_isn(3)] and body['orders_left_out'] == 2
    assert body['documented'] == [{'trip': '1', 'car': 'CAR1', 'count': 2, 'providings': [P15]}]
    assert body['orders_claimed'] == []


def test_documented_summary_is_split_by_trip_and_lists_each_providing_once(web):
    split_trip(web, 'CAR2')
    other = {'number': '000000053290', 'user': 15, 'made': '2026-11-11 10:05'}
    with_documents(web, {1: [P15], 2: [P15, other], 3: [other]})
    body = options(web).get_json()
    assert body['orders'] == [] and body['orders_left_out'] == 3
    assert [(t['trip'], t['car'], t['count'], [p['number'] for p in t['providings']]) for t in body['documented']] == [
        ('1', 'CAR1', 2, ['000000053287', '000000053290']), ('2', 'CAR2', 1, ['000000053290'])]


def test_documented_order_without_a_providing_read_is_still_counted(web):
    with_documents(web, {}, documents={2})
    body = options(web).get_json()
    assert body['documented'] == [{'trip': '1', 'car': 'CAR1', 'count': 1, 'providings': []}]


def claim(web, n, status='complete'):
    """Заказ номер n занят записью сайта в статусе status (как после настоящего execute)."""
    op = str(uuid4())
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        db.execute('INSERT INTO previews VALUES(?,?,?,?,?,?,?)', (op, 'qa', DAY, '{"loads": []}', 'x', '{"loads": []}', status))
        db.execute('INSERT INTO order_claims VALUES(?,?,?)', (web.state.erp_chain.database, str(UUID(_isn(n))), op))
    return op


def plan_orders(loads):
    return sorted(i for load in loads for i in load['orders'])


def test_documented_orders_stay_out_of_the_native_plan(web):
    with_documents(web, {2: [P15]})
    assert plan_orders(previewed_loads(web)) == [_isn(1), _isn(3)]


def test_claimed_orders_stay_out_of_the_native_plan(web):
    claim(web, 2)
    assert plan_orders(previewed_loads(web)) == [_isn(1), _isn(3)]


def test_trip_with_nothing_left_is_refused_before_native(web):
    with_documents(web, {1: [P15], 2: [P15], 3: [P15]})
    refused(web, 'empty')


def test_done_trip_needs_no_card(web):
    split_trip(web, 'CAR2')
    web.people.pop('CAR2')
    cards(web, ('B003/27', DRIVER1))
    with_documents(web, {3: [P15]})  # рейс 2 целиком оформлен офисом, а у CAR2 нет ни PIN, ни карточки
    loads = previewed_loads(web, trips=['1', '2'])
    assert [(load['trip'], load['courier']) for load in loads] == [('1/000', 'B003/27')]


@pytest.mark.parametrize('documents', [{2}, set()])
def test_order_with_an_unknown_division_blocks_nothing_once_documented_or_claimed(web, documents):
    if not documents:
        claim(web, 2, 'refused')
    with_divisions(web, {2: '000099'}, documents=documents)
    assert plan_orders(previewed_loads(web)) == [_isn(1), _isn(3)]


def test_order_documented_after_the_preview_refuses_the_write_as_changed(web):
    token = preview(web).get_json()['result']['id']
    with_documents(web, {2: [P15]})
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == 'changed'
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)


@pytest.mark.parametrize('reserved, code', [({}, 'changed'), ({2: ['015']}, 'reserve_choice')])
def test_order_freed_after_the_preview_refuses_the_write(web, reserved, code):
    with_reserved(web, reserved, documents={2})
    token = preview(web).get_json()['result']['id']
    with_reserved(web, reserved)  # документы удалены: заказ свободен, его склад оператор не видел
    response = execute(web, token)
    assert response.status_code == 409 and response.get_json()['code'] == code
    assert not any(r['action'] == 'execute' for r in web.state.erp_chain.native.calls)
    assert guard.held_operations(web.state.store) == []


def test_completed_run_whose_documents_are_gone_is_shown_as_deleted(web):
    run_ok(web)
    with_documents(web, {})  # документы удалены в ՀԾ: у заказов больше нет детей в DOCPARENTS
    body = options(web).get_json()
    assert body['orders'] == [] and body['orders_left_out'] == 0 and body['documented'] == []
    assert [(o['isn'], o['number'], o['trip'], o['status'], o['deleted']) for o in body['orders_claimed']] == [
        (_isn(1), 'N1', '1', 'complete', True), (_isn(2), 'N2', '1', 'complete', True), (_isn(3), 'N3', '1', 'complete', True)]
    assert all(o['store'] for o in body['orders_claimed'])


def test_order_held_by_an_unresolved_run_is_claimed_not_deleted(web):
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain.native.failure = ec.ServiceError('unknown', 503)
    assert execute(web, token).status_code == 503
    claimed = options(web).get_json()['orders_claimed']
    assert {(o['status'], o['deleted']) for o in claimed} == {('unknown', False)} and len(claimed) == 3


def test_proven_no_write_refusal_frees_the_orders_for_the_list(web):
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain.native.failure = ec.ServiceError('writes_disabled', no_write=True)
    assert execute(web, token).status_code == 409
    body = options(web).get_json()
    assert body['orders_claimed'] == [] and len(body['orders']) == 3


def test_claimed_order_reserved_elsewhere_does_not_demand_a_choice(web):
    claim(web, 2)
    with_reserved(web, {2: ['015']})
    assert plan_orders(previewed_loads(web)) == [_isn(1), _isn(3)]


def test_order_held_by_a_refused_run_is_claimed_not_deleted(web):
    claim(web, 2, 'refused')
    assert [(o['isn'], o['status'], o['deleted']) for o in options(web).get_json()['orders_claimed']] == [(_isn(2), 'refused', False)]


def test_claims_of_another_database_are_ignored(web):
    run_ok(web)
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        db.execute("UPDATE order_claims SET database_id='other'")
    with_documents(web, {})
    body = options(web).get_json()
    assert body['orders_claimed'] == [] and len(body['orders']) == 3


def test_claims_skip_identifiers_that_are_not_order_isns(service):
    assert service.claims(['not-a-uuid', '', _isn(1)]) == {}


def test_providing_query_is_read_only_and_reads_only_providings():
    from route_optimizer import erp
    sql = erp.SQL_ORDER_PROVIDINGS
    erp.check_sql(sql)
    assert sql.count('WITH (NOLOCK)') == 3 and 'p.fDOCTYPE = 19' in sql and 'ORDER BY g.fDATE' in sql


def test_failed_providing_read_blocks_nothing(monkeypatch):
    from route_optimizer import erp
    isn = '00000000-0000-0000-0000-000000000001'
    def select(conn, sql, params=()):
        if 'DOCUMENTSLOG' in sql:
            raise erp.ErpError('log is down')
        if 'DOCPARENTS' in sql:
            return [(isn,)]
        return [] if 'HIRESERVATION' in sql or 'TREES' in sql else [(isn, '000000')]
    monkeypatch.setattr(erp, 'connect', lambda cs: object())
    monkeypatch.setattr(erp, 'close_quietly', lambda conn: None)
    monkeypatch.setattr(erp, '_select', select)
    assert erp.load_order_warehouses('x', [isn])[isn.upper()] == {'division': '000000', 'division_name': '', 'documents': True,
                                                                  'reserved': [], 'providings': []}
