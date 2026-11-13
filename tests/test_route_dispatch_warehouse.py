import sys
from pathlib import Path
from datetime import date,datetime,timedelta
from dataclasses import replace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from route_optimizer import dispatch as dp,views
from courier.routes_link import RoutesView,pick_orders
from test_route_optimizer import client,_dispatch_setup,_dorder,_isn
DAY=date(2026,10,1)
@pytest.fixture(autouse=True)
def clock(monkeypatch):
 monkeypatch.setattr(views,'_roads',lambda *a,**kw:None)
 monkeypatch.setattr(views,'_clock',lambda:datetime(2026,9,30,18))
 monkeypatch.setattr(views,'_same_day_now',lambda:datetime(2026,9,30,18))
def orders():
 return [_dorder(1,101,100),_dorder(2,102,200,agent=13,van=13),_dorder(3,104,300,agent=13,van=13,day=DAY)]
def setup(c):
 _dispatch_setup(c,orders())
 r=c.post('/api/routes/settings',json={'settings':{'dispatch_optional_agents':[13]}})
 assert r.status_code==200,r.get_json()
 r=c.post('/api/routes/dispatch/build',json={'date':str(DAY),'trucks':['CAR1','CAR2']})
 assert r.status_code==200,r.get_json()
 return r.get_json()
def edit(c,d,**kw):
 r=c.post('/api/routes/dispatch/edit',json={'date':str(DAY),'rev':d['rev'],**kw})
 assert r.status_code==200,r.get_json()
 return r.get_json()
def test_optional_rule_roundtrip_and_default():
 rule=dp.FleetRule(optional_agents=frozenset({13}))
 assert dp.FleetRule.from_json(rule.to_json())==rule
 assert not rule.same_as(dp.NO_RULE)
 assert not dp.NO_RULE.active and dp.NO_RULE.to_json() is None
 assert rule.kind(orders()[1])=='optional'
def test_selection_excludes_unselected_and_protects_customer_off():
 os=orders(); rule=dp.FleetRule(optional_agents=frozenset({13}))
 sel=dp.to_deliver(os,DAY,DAY-timedelta(days=1),rule)
 assert [o.isn for o in sel.main]==[_isn(1)]
 assert {o.isn for o in sel.backlog}=={_isn(2),_isn(3)}
 assert not dp.same_day_candidates(os,DAY,rule)
 blocked=dp.to_deliver(os,DAY,DAY-timedelta(days=1),replace(rule,customers_off=frozenset({102})))
 assert _isn(2) not in {o.isn for o in blocked.backlog}
 shipped=dp.to_deliver([replace(os[1],shipped=DAY-timedelta(days=1))],DAY,DAY-timedelta(days=1),rule)
 assert not shipped.backlog
 def future(): return replace(os[1],order_date=DAY+timedelta(days=1))
 assert not dp.to_deliver([future()],DAY,DAY-timedelta(days=1),rule).backlog

def test_api_individual_selection_reload_rebuild_and_remove(client):
 d=setup(client)
 assert d['warehouse_enabled'] and d['orders']['kg']==100
 assert {o['isn'] for o in d['backlog'] if o['warehouse']}=={_isn(2),_isn(3)}
 assert not any(o['taken'] for o in d['backlog'])
 d=edit(client,d,action='include',order=_isn(2))
 assert d['orders']['kg']==300
 assert next(o for o in d['backlog'] if o['isn']==_isn(2))['taken']
 assert not next(o for o in d['backlog'] if o['isn']==_isn(3))['taken']
 d=client.get('/api/routes/dispatch?date='+str(DAY)).get_json()
 assert d['orders']['kg']==300
 d=edit(client,d,action='include',order=_isn(3))
 assert d['orders']['kg']==600
 d=client.post('/api/routes/dispatch/build',json={'date':str(DAY),'trucks':['CAR1','CAR2']}).get_json()
 assert d['orders']['kg']==600
 d=edit(client,d,action='exclude',order=_isn(2))
 assert d['orders']['kg']==400
 assert not next(o for o in d['backlog'] if o['isn']==_isn(2))['taken']

def test_courier_never_takes_unselected_warehouse_order():
 rule=dp.FleetRule(optional_agents=frozenset({13}))
 view=RoutesView(plan_exists=True,released=True,trips=(('CAR1',(101,102,104)),),fleet=rule,added=frozenset({_isn(3)}))
 assert {o.isn for o in pick_orders(orders(),DAY,view,'CAR1')}=={_isn(1),_isn(3)}
 assert not pick_orders(orders(),DAY,replace(view,released=False),'CAR1')

def test_setting_validation_and_persistence(client):
 _dispatch_setup(client,orders())
 assert client.post('/api/routes/settings',json={'settings':{'dispatch_optional_agents':[True]}}).status_code==400
 assert client.post('/api/routes/settings',json={'settings':{'dispatch_optional_agents':[13,13]}}).status_code==200
 assert client.get('/api/routes/settings').get_json()['settings']['dispatch_optional_agents']==[13]


def test_existing_plan_exposes_warehouse_activation_without_changing_plan(client):
 _dispatch_setup(client,orders())
 d=client.post('/api/routes/dispatch/build',json={'date':str(DAY),'trucks':['CAR1','CAR2']}).get_json()
 original=d['rev']
 assert not d['warehouse_enabled']
 assert client.post('/api/routes/settings',json={'settings':{'dispatch_optional_agents':[13]}}).status_code==200
 d=client.get('/api/routes/dispatch?date='+str(DAY)).get_json()
 assert d['warehouse_available'] and not d['warehouse_enabled']
 assert d['rev']==original
 d=edit(client,d,action='apply_settings')
 assert d['warehouse_enabled'] and d['orders']['kg']==100
 assert len([o for o in d['backlog'] if o['warehouse']])==2
 assert not any(o['taken'] for o in d['backlog'])
