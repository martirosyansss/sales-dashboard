"""Явная сверка частичной операции, права, повтор и сбои; только fake native и временный SQLite."""
import copy
import json
import sqlite3
from uuid import uuid4

import pytest

from test_route_erp_chain_service import web, day, client, preview, DAY  # noqa: F401
from route_optimizer import erp_chain_service as ec, erp_chain_guard as guard
from test_route_erp_chain_guard import leased


@pytest.fixture
def partial(web):
    token=preview(web).get_json()['result']['id']
    native=web.state.erp_chain.native
    original=native.call
    reviews={}
    native.recovery_failure=None
    native.recovery_missing=1
    def call(request):
        action=request['action']
        if action.startswith('reconcile-'):
            native.calls.append(copy.deepcopy(request))
            if action=='reconcile-preview':
                review_id=str(uuid4())
                result={'id':token,'database':ec.DATABASE,'review_id':review_id,'can_resume':True,
                        'missing_count':native.recovery_missing,'missing_sales':[]}
                reviews[review_id]=result
                return result
            if native.recovery_failure:
                raise native.recovery_failure
            native.operations[token]['status']='complete'
            return native.operations[token]
        result=original(request)
        if action=='execute': result['status']='partial'
        return result
    native.call=call
    response=web.post('/api/routes/dispatch/erp-chain/execute',json={'id':token,'csrf':web.chain_csrf})
    assert response.get_json()['result']['status']=='partial' and leased(web)==1
    web.partial_token=token
    return web


def review(web):
    return web.post('/api/routes/dispatch/erp-chain/reconcile-preview',json={'id':web.partial_token,'csrf':web.chain_csrf})


def confirm(web,review_id,**changes):
    return web.post('/api/routes/dispatch/erp-chain/reconcile',json={'review_id':review_id,'reason':'Փաստաթղթերի ստուգումը կատարված է','csrf':web.chain_csrf,**changes})


def test_composite_trip_id_survives_reconciliation(partial):
    web=partial
    plan=json.loads(web.state.erp_chain.get(web.partial_token,'qa')['plan'])
    assert [x['trip'] for x in plan['loads']]==['1/000']
    result=review(web).get_json()['result']
    assert confirm(web,result['review_id']).get_json()['result']['status']=='complete'
    assert leased(web)==0


def test_review_is_read_only_and_confirmation_completes_once(partial):
    web=partial
    result=review(web).get_json()['result']
    assert leased(web)==1
    assert not any(x['action']=='reconcile-execute' for x in web.state.erp_chain.native.calls)
    assert confirm(web,result['review_id']).get_json()['result']['status']=='complete'
    assert leased(web)==0
    assert confirm(web,result['review_id']).get_json()['result']['status']=='complete'
    assert sum(x['action']=='reconcile-execute' for x in web.state.erp_chain.native.calls)==1
    with sqlite3.connect(web.state.erp_chain.journal) as db:
        assert db.execute('SELECT reason,status FROM reconciliation_reviews').fetchone()==('Փաստաթղթերի ստուգումը կատարված է','complete')


@pytest.mark.parametrize('reason',[None,'','short','x'*501])
def test_reason_is_required_before_recovery_native_call(partial,reason):
    result=review(partial).get_json()['result']
    assert confirm(partial,result['review_id'],reason=reason).status_code==400
    assert leased(partial)==1
    assert not any(x['action']=='reconcile-execute' for x in partial.state.erp_chain.native.calls)


def test_recovery_requires_csrf_admin_and_superadmin(partial):
    result=review(partial).get_json()['result']
    assert confirm(partial,result['review_id'],csrf='bad').status_code==403
    with partial.session_transaction() as session: session['username']='another-admin'
    assert confirm(partial,result['review_id']).status_code==404
    with partial.session_transaction() as session: session['username']='qa'
    response=partial.post('/api/routes/dispatch/erp-chain/reconcile',json={'review_id':result['review_id'],
        'reason':'Փաստաթղթերի ստուգումը կատարված է','csrf':partial.chain_csrf},headers={'X-Superadmin-Password':''})
    assert response.status_code==403
    partial.role['value']='garage'
    assert review(partial).status_code==403
    assert not any(x['action']=='reconcile-execute' for x in partial.state.erp_chain.native.calls)


@pytest.mark.parametrize('failure',[ec.ServiceError('busy',no_write=True),ec.ServiceError('unknown',503),SystemExit('crash')])
def test_recovery_failure_keeps_original_claim_and_plan_guard(partial,failure):
    result=review(partial).get_json()['result']
    partial.state.erp_chain.native.recovery_failure=failure
    if isinstance(failure,SystemExit):
        with pytest.raises(SystemExit): confirm(partial,result['review_id'])
    else:
        assert confirm(partial,result['review_id']).status_code in (409,503)
    assert leased(partial)==1
    # Старое подтверждение не запускается повторно даже после доказанного busy этой попытки.
    assert confirm(partial,result['review_id']).get_json()['result']['status']=='partial'
    assert sum(x['action']=='reconcile-execute' for x in partial.state.erp_chain.native.calls)==1
    with sqlite3.connect(partial.state.erp_chain.journal) as db:
        assert db.execute('SELECT COUNT(*) FROM order_claims').fetchone()[0]>0


def test_new_review_can_confirm_all_existing_sales_without_new_original_execution(partial):
    partial.state.erp_chain.native.recovery_missing=0
    result=review(partial).get_json()['result']
    assert result['missing_count']==0
    assert confirm(partial,result['review_id']).get_json()['result']['status']=='complete'
    assert sum(x['action']=='execute' for x in partial.state.erp_chain.native.calls)==1


def test_foreign_operation_cannot_steal_existing_plan_lease(partial):
    with pytest.raises(guard.PlanLocked):
        guard.acquire(partial.state.store,json.loads(partial.state.erp_chain.get(partial.partial_token,'qa')['plan']),str(uuid4()),keep_existing=True)
