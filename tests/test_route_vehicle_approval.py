"""Server-side approval, including direct API and restoration bypasses. Synthetic data only."""
import copy
import json
import pytest
from flask import g
from route_optimizer import dispatch as dp
from route_optimizer.vehicle_access import VehicleAccess
from test_route_optimizer import client, _dispatch_setup

PASSWORD='test-vehicle-approval'
URL='/api/routes/customer-vehicles'

def prepare(client):
    _dispatch_setup(client, [])
    client.environ_base.pop('HTTP_X_SUPERADMIN_PASSWORD',None)
    return client.application.extensions['route_optimizer'].store

def post(client,body,password=None,url=URL):
    return client.post(url,json=body,headers={} if password is None else {'X-Superadmin-Password':password})

@pytest.mark.parametrize('mode',['allow','deny'])
@pytest.mark.parametrize('password',[None,'wrong',''])
def test_creation_without_valid_password_is_atomic(client,mode,password):
    store=prepare(client)
    r=post(client,{'customer_id':103,'access':{'mode':mode,'trucks':['CAR1']},'window':{'kind':'before','t1':600},'unload_min':25,'solo':True},password)
    assert r.status_code==403,r.get_json()
    assert r.get_json()['superadmin_required'] is True
    b=store.load()
    assert 103 not in b.vehicle_access and 103 not in b.windows and 103 not in b.unload_min and 103 not in b.solo

@pytest.mark.parametrize('mode',['allow','deny'])
def test_correct_password_required_for_each_change_and_removal(client,mode):
    store=prepare(client)
    rule={'mode':mode,'trucks':['CAR1']}
    assert post(client,{'customer_id':103,'access':rule},PASSWORD).status_code==200
    assert store.load().vehicle_access[103]==VehicleAccess(mode,('CAR1',))
    assert post(client,{'customer_id':103,'access':None}).status_code==403
    assert post(client,{'customer_id':103,'access':None},'wrong').status_code==403
    assert post(client,{'customer_id':103,'access':None},PASSWORD).status_code==200
    assert 103 not in store.load().vehicle_access

def test_unchanged_access_and_unload_need_no_password(client):
    store=prepare(client)
    rule={'mode':'allow','trucks':['CAR1']}
    assert post(client,{'customer_id':103,'access':rule},PASSWORD).status_code==200
    assert post(client,{'customer_id':103,'access':rule,'window':{'kind':'before','t1':600},'unload_min':25}).status_code==200
    assert post(client,{'customer_id':103,'unload_min':30}).status_code==200
    assert store.load().vehicle_access[103]==VehicleAccess('allow',('CAR1',))
    assert store.load().unload_min[103]==30

def test_missing_configuration_fails_closed(client,monkeypatch):
    store=prepare(client)
    client.application.config.pop('ROUTES_SUPERADMIN_PASSWORD_HASH')
    monkeypatch.delenv('ROUTES_SUPERADMIN_PASSWORD_HASH',raising=False)
    r=post(client,{'customer_id':103,'access':{'mode':'allow','trucks':['CAR1']}},PASSWORD)
    assert r.status_code==503
    assert not store.load().vehicle_access

def test_rate_limit_wrong_passwords(client):
    store=prepare(client)
    body={'customer_id':103,'access':{'mode':'allow','trucks':['CAR1']}}
    for _ in range(5):assert post(client,body,'wrong').status_code==403
    r=post(client,body,PASSWORD)
    assert r.status_code==429 and r.headers['Retry-After']=='300'
    assert not store.load().vehicle_access

def test_password_does_not_appear_in_storage_or_response(client):
    store=prepare(client)
    r=post(client,{'customer_id':103,'access':{'mode':'deny','trucks':['CAR1']}},PASSWORD)
    assert r.status_code==200 and PASSWORD not in r.get_data(as_text=True)
    rows=store._read(lambda c:c.execute('SELECT * FROM customer_vehicle_access').fetchall())
    assert PASSWORD not in repr(rows)
    assert not client.get_cookie('session')

@pytest.mark.parametrize('operation',['create','change','remove','delete','rewrite'])
def test_daily_lock_mutations_require_approval_in_transaction(client,operation):
    store=prepare(client)
    day='2026-11-05'
    before={'trucks':['CAR1','CAR2'],'trips':[]}
    if operation!='create':before['locks']={'103':'CAR1'}
    assert store.save_dispatch(day,before,'fixture')==1
    after=copy.deepcopy(before)
    if operation=='create':after['locks']={'103':'CAR1'}
    elif operation=='change':after['locks']={'103':'CAR2'}
    else:after.pop('locks',None)
    from route_optimizer.vehicle_authorization import VehicleApprovalError
    for secret in ['', 'wrong', PASSWORD]:
        with client.application.test_request_context(headers={'X-Superadmin-Password':secret}):
            if secret!=PASSWORD:
                with pytest.raises(VehicleApprovalError):
                    if operation=='delete':store.delete_dispatch(day,expected_rev=1)
                    elif operation=='rewrite':store._transaction(lambda c:store.rewrite_dispatch_after(c,'2026-11-04',lambda day,data:after,'qa'),'test')
                    else:store.save_dispatch(day,after,'qa',expected_rev=1)
            else:
                if operation=='delete':assert store.delete_dispatch(day,expected_rev=1)
                elif operation=='rewrite':store._transaction(lambda c:store.rewrite_dispatch_after(c,'2026-11-04',lambda day,data:after,'qa'),'test')
                else:assert store.save_dispatch(day,after,'qa',expected_rev=1)==2
        if secret!=PASSWORD:assert store.load_dispatch(day)==(before,1)

def test_stale_plan_still_conflicts_before_approval(client):
    store=prepare(client)
    before={'locks':{'103':'CAR1'}}
    store.save_dispatch('2026-11-05',before,'fixture')
    with client.application.test_request_context():
        assert store.save_dispatch('2026-11-05',{'locks':{}},'qa',expected_rev=0) is None
    assert store.load_dispatch('2026-11-05')==(before,1)


def test_superadmin_password_does_not_bypass_csrf(client):
    from courier.web_security import init_web_security
    app=client.application
    init_web_security(app,'/api/courier/v1')
    client.environ_base.pop('HTTP_X_SUPERADMIN_PASSWORD',None)
    body={'customer_id':103,'access':{'mode':'allow','trucks':['CAR1']}}
    r=post(client,body,PASSWORD)
    assert r.status_code==403 and r.get_json()['error']=='csrf'
    token='test-csrf-token-012345678901234567890123456789'
    with client.session_transaction() as session:session['_office_csrf']=token
    r=client.post(URL,json=body,headers={'X-CSRF-Token':token})
    assert r.status_code==403 and r.get_json()['superadmin_required']
    r=client.post(URL,json=body,headers={'X-CSRF-Token':token,'X-Superadmin-Password':PASSWORD})
    assert r.status_code==200,r.get_json()
    r=client.post(URL,json={'customer_id':103,'access':None},headers={'X-CSRF-Token':token,'X-Superadmin-Password':PASSWORD,'Origin':'https://other.example'})
    assert r.status_code==403 and r.get_json()['error']=='csrf'
    assert app.extensions['route_optimizer'].store.load().vehicle_access[103]==VehicleAccess('allow',('CAR1',))
