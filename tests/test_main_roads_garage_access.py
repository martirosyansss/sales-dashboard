"""The restricted garage role can load the same road geometry, including through the public host."""
from types import SimpleNamespace as NS

import pytest
from route_optimizer import views, roads as rd
from test_garage_role import dashboard, _login_as, _no_road_map
from test_route_main_roads import graph, point


@pytest.mark.parametrize('public',[False,True])
def test_garage_road_geometry_with_real_role_gate(dashboard,monkeypatch,public):
    import app_v2
    host='https://araqich.orix.am' if public else 'http://localhost'
    with dashboard.session_transaction(base_url=host) as session:
        app_v2._stamp_session(session,'garage1',dashboard.users['garage1'])
        session['_office_csrf']='x'*40
    g=graph()
    roads=rd.RoadDistances.for_graph(g)
    provider=NS(get=lambda:roads,bypass=lambda current,zone:current)
    monkeypatch.setattr(app_v2.app.extensions['route_optimizer'],'roads',provider)
    payload={'lines':[[point(g,0),point(g,3)]],'avoid_center':True}
    headers={'X-CSRF-Token':'x'*40,'Origin':host}
    response=dashboard.post('/api/routes/garage/road-lines',base_url=host,json=payload,headers=headers)
    assert response.status_code==200,response.get_json()
    assert response.get_json()['routing_policy']=='main_roads_v1'
    assert list(point(g,2)) in response.get_json()['lines'][0]
    assert dashboard.post('/api/routes/garage/road-lines',base_url=host,json=payload).status_code==403
    # The separate administrator endpoint remains unavailable to this role.
    assert dashboard.post('/api/routes/road-lines',base_url=host,json=payload,headers=headers).status_code in (403,404)
    assert dashboard.post('/api/routes/dispatch/edit',base_url=host,json={},headers=headers).status_code in (403,404)
