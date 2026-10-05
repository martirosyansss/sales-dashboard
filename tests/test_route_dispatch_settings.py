# -*- coding: utf-8 -*-
"""«Развоз» (№74): правила «чьи заказы везём» в настройках «Маршрутов».

- Правило менеджеров (№69) и новые заказы дня (№72, ревью L2): менеджер, у которого сегодня только новые заказы дня, — в
  фильтре «Մենեջերներ»; правило настроек снимает их и с плашки новых заказов у дня без плана.

Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_settings.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from test_route_dispatch_same_day import D, DAY, _build, _page_setup  # noqa: E402
from test_route_optimizer import _dorder, _isn, client  # noqa: E402,F401


def _settings(client, **values):
    r = client.post('/api/routes/settings', json={'settings': values})
    assert r.status_code == 200, r.get_json()


# ============================== №72 L2: менеджеры новых заказов дня ==============================

def _agents(d):
    return {a['agent_id']: a for a in d['agents']}


def test_manager_with_only_new_orders_of_today_is_in_filter_and_rule_hides_them(client, monkeypatch):
    # менеджер 3 сегодня принял только новый заказ (клиент 103) — заказов развоза дня у него нет
    _page_setup(client, monkeypatch, extra_orders=[_dorder(20, 103, 70.0, day=D, agent=3, rev=500.0)])
    _settings(client, dispatch_agents_off=[3])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    ag = _agents(d)
    assert ag[3]['off'] is True and (ag[3]['count'], ag[3]['kg']) == (0, 0)
    assert ag[3]['same_day'] == {'count': 1, 'kg': 70}
    assert ag[1]['same_day'] == {'count': 2, 'kg': 290} and ag[2]['same_day'] == {'count': 1, 'kg': 90}
    # у дня без плана правило настроек снимает и его новые заказы с плашки (№69 + №72)
    assert _isn(20) not in {o['isn'] for o in d['same_day']['orders']} and d['same_day']['count'] == 3
    s = client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    assert s['same_day']['count'] == 3 and s['same_day']['sig'] == d['same_day']['sig']
    # первая сборка начинает с правила; вернуть менеджера 3 в развоз дня — его новый заказ снова на плашке
    d = _build(client)
    assert d['agents_off'] == [3] and _isn(20) not in {o['isn'] for o in d['same_day']['orders']}
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'agents', 'off': []}).get_json()
    assert _isn(20) in {o['isn'] for o in d['same_day']['orders']} and _agents(d)[3]['off'] is False


def test_taken_new_order_counts_in_its_manager(client, monkeypatch):
    _page_setup(client, monkeypatch)
    d = _build(client)
    before = _agents(d)[1]
    opts = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day',
                                                       'orders': opts['orders'], 'option': opts['options'][0]['key']}).get_json()
    after = _agents(d)[1]
    assert after['count'] == before['count'] + 1 and after['kg'] == before['kg'] + 250
    assert after['same_day']['count'] == before['same_day']['count'] - 1           # взятый — уже не «ещё решать»


def test_no_new_orders_agents_list_unchanged(client, monkeypatch):
    # загрузчика новых заказов нет — список менеджеров тот же, без same_day
    _page_setup(client, monkeypatch, same=False)
    d = _build(client)
    assert all('same_day' not in a for a in d['agents'])
    assert sorted(a['agent_id'] for a in d['agents']) == [1, 2]


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
