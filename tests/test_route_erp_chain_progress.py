"""Ход записи ERP-цепочки для окна: подсчёт шагов по ERP и API (ERP подменяется)."""
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import erp
from route_optimizer.erp import ErpError
from test_route_dispatch_erp_write import day, client, _sent, _isn, DAY  # noqa: F401
from test_route_erp_chain_service import web, preview  # noqa: F401

A, B, C = (str(uuid4()).upper() for _ in range(3))
URL = '/api/routes/dispatch/erp-chain/progress/'
STEPS = {'assignment': [1, 2], 'providing': [0, 1], 'sale': [0, 2]}


def order(isn, version='v0'):
    return {'isn': isn, 'version': version}


def test_counts_each_step_by_load():
    loads = [{'orders': [order(A), order(B)]}, {'orders': [order(C)]}]
    stamps = {A: 'v1', B: 'v1', C: 'v0'}
    children = [(A, 19, 'P1'), (B, 19, 'P1'), (A, 2, 'S1')]
    assert erp.chain_progress(loads, stamps, children) == {'assignment': [2, 3], 'providing': [1, 2], 'sale': [1, 3]}


def test_driver_step_needs_a_changed_stamp_and_ignores_case():
    loads = [{'orders': [order(A.lower()), order(B), order(C, version=None)]}]
    stamps = {A: 'v0', C: 'v9'}   # A unchanged, B gone from ERP, C has no preview stamp
    assert erp.chain_progress(loads, stamps, [(A.lower(), 2, 's1')]) == {
        'assignment': [0, 3], 'providing': [0, 1], 'sale': [1, 3]}


def test_nothing_written_yet():
    loads = [{'orders': [order(A)]}]
    assert erp.chain_progress(loads, {A: 'v0'}, []) == {'assignment': [0, 1], 'providing': [0, 1], 'sale': [0, 1]}


def test_api_reads_erp_for_the_previewed_loads(web):
    token = preview(web).get_json()['result']['id']
    asked = []
    web.state.erp_chain_progress_loader = lambda loads: asked.append(loads) or dict(STEPS)
    reply = web.get(URL + token)
    assert reply.status_code == 200
    assert reply.get_json()['progress'] == {**STEPS, 'status': 'ready'}
    assert asked and all(load['orders'] and all(o['isn'] for o in load['orders']) for load in asked[0])


def test_api_is_private_to_the_operator_and_admin_only(web):
    token = preview(web).get_json()['result']['id']
    web.state.erp_chain_progress_loader = lambda loads: dict(STEPS)
    with web.session_transaction() as session:
        session['username'] = 'someone-else'
    assert web.get(URL + token).status_code == 404
    with web.session_transaction() as session:
        session['username'] = 'qa'
    web.role['value'] = 'garage'
    assert web.get(URL + token).status_code == 403


def test_api_reports_an_erp_failure_and_a_missing_reader(web):
    token = preview(web).get_json()['result']['id']

    def down(loads):
        raise ErpError('down')
    web.state.erp_chain_progress_loader = down
    reply = web.get(URL + token)
    assert reply.status_code == 503 and reply.get_json()['code'] == 'erp_unavailable'
    web.state.erp_chain_progress_loader = None
    assert web.get(URL + token).status_code == 404
    assert web.get(URL + 'not-a-token').status_code in (400, 404)
