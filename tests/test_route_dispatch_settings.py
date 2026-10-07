# -*- coding: utf-8 -*-
"""«Развоз» (№74): правила «чьи заказы везём» в настройках «Маршрутов».

- Правило менеджеров (№69) и новые заказы дня (№72, ревью L2): менеджер, у которого сегодня только новые заказы дня, — в
  фильтре «Մենեջերներ»; правило настроек снимает их и с плашки новых заказов у дня без плана.
- Менеджеры, чьи заказы «ինքն է տանում» всё равно везут машины (Rocarm A000), кроме городов-исключений («գնում է այլ
  մեքենայով»), и клиенты, чьи заказы машины не везут никогда (dp.FleetRule): страница «Развоз», новые заказы дня,
  приложение водителя, настройки и подсказка из ERP. Без новых настроек — всё до байта как раньше.

Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_settings.py -q
"""
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import erp_day as ed  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from test_courier import fake_erp  # noqa: E402,F401
from test_route_dispatch_same_day import D, DAY, _build, _page_setup  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn, client  # noqa: E402,F401


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


# ============================== №74: правило «чьи заказы везут машины» ==============================

CITIES = ['Գյումրի', 'Կապան', 'Գորիս', 'Վանաձոր']
ROCARM = 2      # в синтетике «Rocarm» — менеджер 2: экспедитор его заказов «везёт сам» — он сам (van=2)


def test_city_match_real_spellings_city_field_and_aliases():
    r = dp.FleetRule(frozenset({ROCARM}), tuple(CITIES))
    yes = {('ՍՅՈՒՆԻՔ, ԿԱՊԱՆ,Լեռնագործների 4-րդ նրբանցք\xa0թիվ\xa029/1', 'Արա Աբգարյան ԱՁ'): 'Կապան',
           ('Շիրակ, Գյումրի, Արագած փ. 1ա բն. 21', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ'): 'Գյումրի',
           ('', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ'): 'Գյումրի',                                   # адреса нет — название после «/»
           ('Լոռի, Վանաձոր, Թումանյան փ. 12', 'Էմ Պլյուս ՍՊԸ/վանաձոր'): 'Վանաձոր',
           ('Սյունիքի մարզ, ք.Գորիս Բակունցի 21', 'Ա/Ձ Բաղդասարյան Հայրապետ Գավրուշի/գորիս'): 'Գորիս',
           ('Սյունիքի մարզ, ք․ Կապան Շինարարների 1', 'Գանձասար ֆուտբոլային ակումբ ՀԿ'): 'Կապան',
           ('ՀՀ, Սյունիք, Կապան, Շահումյան 5', ''): 'Կապան', ('г. Капан, ул. Шаумяна 5', ''): 'Կապան',
           ('Gyumri, Rustaveli 1', ''): 'Գյումրի', ('Լենինական, 5', ''): 'Գյումրի', ('Ղափան, 1', ''): 'Կապան',
           ('г.  Ванадзор', ''): 'Վանաձոր', ('Гюмри, 3', ''): 'Գյումրի', ('Բաբայան 2/17', 'Խանութ/ԿԱՊԱՆ'): 'Կապան'}
    no = [('Երևան, Կոմիտաս 1', 'Կապան Մարկետ ՍՊԸ'),                 # ревью M2: адрес — Ереван, город в названии не важен
          ('Երևան, Կոմիտաս 1', 'Մարկետ ՍՊԸ/Կապան'),
          ('Երևան, Վանաձոր փողոց 4', ''), ('Վանաձոր փողոց 4, Երևան', ''), ('ԳՅՈՒՄՐԻ-ԵՐԵՎԱՆ ԽՃՂ., Աշտարակ', ''),
          ('Գորիս-Կապան մայրուղի, Սիսիան', ''), ('', 'Gyumri-Market LLC, Yerevan'), ('Gyumri-Market LLC, Yerevan', ''),
          ('Երևան, Կապանի փող. 3', ''), ('Կապանցի 3, Երևան', ''), ('Երևան, Աջափնյակ, Հալաբյան փ. 16', ''), ('', ''),
          ('', 'Կապան Մարկետ ՍՊԸ'), ('Կոտայք, Աբովյան, Գյումրու խճ. 2', ''), ('Արարատ, Մասիս, Կապանի 1', ''),
          ('Կապան Մարկետ ՍՊԸ\nԵրևան, Կոմիտաս 1', '')]                  # ре-ревью: в адресе строка с названием
    assert {k: r.matched_city(k) for k in yes} == yes
    assert [k for k in no if r.matched_city(k)] == []
    assert dp.FleetRule(cities=('Սիսիան',)).matched_city(('ՍՅՈՒՆԻՔ, ՍԻՍԻԱՆ, 5', '')) == 'Սիսիան'   # не из словаря — как ввели
    assert dp.FleetRule(cities=()).matched_city(('Սյունիք, Կապան', '')) is None
    # «և» = «եւ» = «ԵՎ» (ревью L2)
    sevan = dp.FleetRule(cities=('Սևան',))
    assert all(sevan.matched_city((a, '')) == 'Սևան' for a in ('Գեղարքունիք, ՍԵՎԱՆ, 1', 'Գեղարքունիք, Սեւան, 1',
                                                                 'Գեղարքունիք, Սեվան, 1'))
    assert dp.FleetRule(cities=('ՍԵՎԱՆ',)).matched_city(('Գեղարքունիք, Սևան, 1', '')) == 'ՍԵՎԱՆ'
    assert dp.city_field('Երևան, Աջափնյակ, Հալաբյան 16') == dp.fold_text('Երևան') and dp.city_field('Բաբայան 2/17') is None
    assert dp.city_field('Ա/Ձ Մարկետ\nՍյունիք, Կապան, 5\n') == dp.fold_text('Կապան')            # последняя непустая строка


def test_kind_precedence():
    where = {105: ('ՍՅՈՒՆԻՔ, ԿԱՊԱՆ, 1', ''), 104: ('Երևան, 1', '')}
    r = dp.FleetRule(frozenset({ROCARM}), tuple(CITIES), frozenset({108}))

    def k(o):
        return r.kind(o, lambda c: where.get(c, ('', '')))
    assert k(_dorder(1, 104, 1.0, agent=ROCARM, van=ROCARM)) == dp.FLEET            # «ինքն է տանում» — машинами
    assert k(_dorder(2, 105, 1.0, agent=ROCARM, van=ROCARM)) == dp.OTHER_VEHICLE    # город-исключение
    assert k(_dorder(3, 105, 1.0, agent=1)) == dp.FLEET                              # исключение — только «ինքն է տանում»
    assert k(_dorder(4, 104, 1.0, agent=3, van=3)) == dp.SELF_DELIVERY               # менеджер не из правила
    assert k(_dorder(5, 108, 1.0, agent=ROCARM, van=ROCARM)) == dp.CUSTOMER_OFF      # клиент — главнее всего
    assert k(_dorder(6, 108, 1.0)) == dp.CUSTOMER_OFF
    # место спрашивается только когда нужно
    asked = []
    r.kind(_dorder(7, 104, 1.0), lambda c: asked.append(c) or ('', ''))
    r.kind(_dorder(8, 104, 1.0, agent=3, van=3), lambda c: asked.append(c) or ('', ''))
    assert asked == []
    assert dp.NO_RULE.kind(_dorder(9, 104, 1.0, agent=ROCARM, van=ROCARM)) == dp.SELF_DELIVERY


ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=ROCARM),
          _dorder(5, 104, 100.0, agent=ROCARM, van=ROCARM),                 # Rocarm, Ереван — машинами
          _dorder(7, 105, 200.0, agent=ROCARM, van=ROCARM),                 # Rocarm, Капан — другие машины
          _dorder(8, 107, 150.0, agent=ROCARM, van=ROCARM),                 # Rocarm, Гюмри в названии
          _dorder(9, 108, 50.0),                                            # внутренний счёт — не везём
          _dorder(10, 109, 70.0, agent=3, van=3),                           # другой менеджер везёт сам
          _dorder(11, 105, 60.0),                                           # Капан, но заказ для машин
          _dorder(12, 105, 30.0, day=date(2026, 9, 28), agent=ROCARM, van=ROCARM),   # прошлые дни: Капан
          _dorder(13, 104, 20.0, day=date(2026, 9, 28), agent=ROCARM, van=ROCARM)]   # прошлые дни: Ереван
NAMES = {101: ('C101', 'Клиент 101'), 102: ('C102', 'Клиент 102'), 104: ('C104', 'Клиент 104'),
         105: ('C105', 'Արա Աբգարյան ԱՁ'), 107: ('C107', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ'), 108: ('7092', 'Վարչական/ ռոքարմ'),
         109: ('C109', 'Клиент 109'), 999: ('C999', 'Новый')}
ADDRESSES = {101: 'Ереван, 1', 104: 'Երևան, Աջափնյակ', 105: 'ՍՅՈՒՆԻՔ, ԿԱՊԱՆ,Լեռնագործների 4'}
RULE = {'dispatch_fleet_agents': [ROCARM], 'dispatch_other_cities': CITIES, 'dispatch_customers_off': [108]}


def _nums(orders):
    return sorted(int(o.isn.replace('-', ''), 16) for o in orders)


def test_to_deliver_buckets_and_empty_rule_identical():
    place = dp.place_of(NAMES, ADDRESSES)
    since = date(2026, 9, 30)
    plain = dp.to_deliver(ORDERS, D, since)
    assert dp.to_deliver(ORDERS, D, since, dp.FleetRule(cities=tuple(CITIES)), place) == plain   # без менеджеров — как было
    assert _nums(plain.self_delivery) == [5, 7, 8, 10] and _nums(plain.main) == [1, 2, 9, 11]
    sel = dp.to_deliver(ORDERS, D, since, dp.FleetRule.from_settings(RULE), place)
    assert _nums(sel.main) == [1, 2, 5, 11] and _nums(sel.backlog) == [13]
    assert _nums(sel.self_delivery) == [10] and _nums(sel.other_vehicle) == [7, 8] and _nums(sel.customers_off) == [9]
    assert sel.shipped_before == plain.shipped_before
    # каждый заказ дня — ровно в одном списке
    day = [o for o in ORDERS if o.order_date >= since]
    assert len(sel.main) + len(sel.self_delivery) + len(sel.other_vehicle) + len(sel.customers_off) == len(day)


def test_same_day_candidates_follow_rule():
    orders = [replace(o, order_date=D) for o in ORDERS[:9]]
    place = dp.place_of(NAMES, ADDRESSES)
    assert dp.same_day_candidates(orders, D) == dp.same_day_candidates(orders, D, dp.NO_RULE, place)
    assert _nums(dp.same_day_candidates(orders, D)) == [1, 2, 9, 11]
    assert _nums(dp.same_day_candidates(orders, D, dp.FleetRule.from_settings(RULE), place)) == [1, 2, 5, 11]


def test_settings_defaults_and_validation():
    assert (st.DEFAULT_SETTINGS['dispatch_fleet_agents'], st.DEFAULT_SETTINGS['dispatch_customers_off']) == ([], [])
    assert st.DEFAULT_SETTINGS['dispatch_other_cities'] == CITIES
    raw = dict(st.DEFAULT_SETTINGS)
    for k in ('dispatch_fleet_agents', 'dispatch_other_cities', 'dispatch_customers_off'):
        del raw[k]
    out, errors = st.validate_settings(raw, None)                                   # база до №74
    assert errors == {}
    assert (out['dispatch_fleet_agents'], out['dispatch_other_cities'], out['dispatch_customers_off']) == ([], CITIES, [])
    out, errors = st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_fleet_agents': [13, 2, 13],
                                        'dispatch_customers_off': [9462, 7092, 9462],
                                        'dispatch_other_cities': [' Կապան ', 'ԿԱՊԱՆ', 'Նոր   Հաճն', 'Գորիս']}, None)
    assert errors == {}
    assert (out['dispatch_fleet_agents'], out['dispatch_customers_off'], out['dispatch_other_cities']) == \
        ([2, 13], [7092, 9462], ['Կապան', 'Նոր Հաճն', 'Գորիս'])
    assert st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_other_cities': []}, None)[1] == {}
    bad_values = [('dispatch_fleet_agents', x) for x in ('2', None, [0], [True], [2.0], [2 ** 31],
                                                         list(range(1, st.MAX_FLEET_AGENTS + 2)))]
    bad_values += [('dispatch_customers_off', x) for x in ({}, [-1], ['7092'], list(range(1, st.MAX_CUSTOMERS_OFF + 2)))]
    bad_values += [('dispatch_other_cities', x) for x in ('Կապան', [''], ['  '], ['123'], [5], ['Կապան, Գորիս'],
                                                          ['ա' * (st.CITY_NAME_MAX + 1)],
                                                          ['Ք' + str(i) for i in range(st.MAX_OTHER_CITIES + 1)])]
    for key, bad in bad_values:
        _, errors = st.validate_settings({**st.DEFAULT_SETTINGS, key: bad}, None)
        assert set(errors) == {key}, (key, bad)
    assert st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_customers_off': list(range(1, st.MAX_CUSTOMERS_OFF + 1)),
                                 'dispatch_other_cities': ['ա' * st.CITY_NAME_MAX]}, None)[1] == {}


def _setup(client):
    _dispatch_setup(client, ORDERS)
    state = client.application.extensions['route_optimizer']
    data = state.dispatch_loader(None, None, None)
    state.dispatch_loader = lambda since, until, day: replace(data, customers=NAMES, addresses=ADDRESSES)
    return state


def test_dispatch_page_counters_and_byte_identical_without_rule(client):
    _setup(client)
    raw_before = client.get('/api/routes/dispatch?date=' + DAY).data
    before = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert 'other_vehicle' not in before['orders'] and 'customers_off' not in before['orders']
    assert (before['orders']['count'], before['orders']['self_delivery'], before['orders']['self_delivery_kg']) == (4, 4, 520)
    # новые ключи с прежними значениями (и другие города без менеджеров) — ответ тот же до байта
    _settings(client, dispatch_fleet_agents=[], dispatch_customers_off=[], dispatch_other_cities=['Սիսիան'])
    assert client.get('/api/routes/dispatch?date=' + DAY).data == raw_before
    _settings(client, **RULE)
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    o = d['orders']
    assert (o['count'], o['kg']) == (4, 860)                                         # 101, 102, 104 (Rocarm), 105 (не сам)
    assert (o['self_delivery'], o['self_delivery_kg']) == (1, 70)
    assert (o['other_vehicle'], o['other_vehicle_kg'], o['customers_off'], o['customers_off_kg']) == (2, 350, 1, 50)
    # итог сходится: заказы дня = в развозе + сам + другие машины + не везём
    assert o['count'] + o['self_delivery'] + o['other_vehicle'] + o['customers_off'] == 8
    assert [b['isn'] for b in d['backlog']] == [_isn(13)]                            # прошлые дни: Капан — не наш
    assert ROCARM in {a['agent_id'] for a in d['agents']}
    s = client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    assert (s['orders']['count'], s['orders']['kg']) == (4, 860)
    # «այլ մեքենայով» — списком: магазин, менеджер, город (ревью M2)
    assert [(x['customer_id'], x['agent_code'], x['city']) for x in d['other_vehicle']] == \
        [(105, 'A002', 'Կապան'), (107, 'A002', 'Գյումրի')]
    # сборка: магазин 104 (Rocarm, Ереван) в развозе, 107, 108, 109 — нет
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    planned = {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']} \
        | {s['customer_id'] for s in d['plan']['unassigned']} | {s['customer_id'] for s in d['stops_no_coords']}
    assert 104 in planned and not {107, 108, 109} & planned


def test_missing_coordinates_list_follows_rule(client):
    # «магазины без точки» — только те, что повезут машины: клиент «не везём» из списка уходит
    _setup(client)
    rows = lambda: [ln.split(';')[0] for ln in client.get('/api/routes/coordinates/missing.csv')   # noqa: E731
                    .get_data(as_text=True).lstrip('\ufeff').splitlines()[1:]]
    assert rows() == ['105', '108']
    _settings(client, **RULE)
    assert rows() == ['105']
    # ревью L6: и правило менеджеров (№69) — снятый менеджер 1 (заказы 105 и 108) — не наши магазины
    _settings(client, dispatch_agents_off=[1], dispatch_customers_off=[])
    assert rows() == []


def test_rule_and_manager_filter_interplay(client):
    # правило менеджеров (№69) снимает Rocarm — его заказы «машинами» уходят в «не везём сегодня», город — по-прежнему отдельно
    _setup(client)
    _settings(client, **RULE, dispatch_agents_off=[ROCARM])
    o = client.get('/api/routes/dispatch?date=' + DAY).get_json()['orders']
    assert (o['count'], o['agents_off'], o['agents_off_kg'], o['other_vehicle']) == (2, 2, 400, 2)


def test_new_orders_of_today_follow_rule_and_next_day_skips_taken(client, monkeypatch):
    # Rocarm (менеджер 2) сегодня: заказ 13 у клиента 101 (van=2) — «ինքն է տանում»
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    assert _isn(13) not in {o['isn'] for o in d['same_day']['orders']}             # без правила — не для машин

    def apply():
        r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'apply_settings'})
        assert r.status_code == 200, r.get_json()
        return r.get_json()
    # правило включили после сборки: собранный день — со своим правилом, пока логист не применит настройки
    _settings(client, dispatch_fleet_agents=[ROCARM])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert _isn(13) not in {o['isn'] for o in d['same_day']['orders']} and d['settings_differ'] == {'fleet': True}
    d = apply()
    assert _isn(13) in {o['isn'] for o in d['same_day']['orders']}
    # клиент 101 в Гюмри — заказ уходит «այլ մեքենայով», с плашки пропадает; клиент 103 — в списке «не везём»
    loader = state.same_day_loader
    state.same_day_loader = lambda day: replace(loader(day), addresses={**loader(day).addresses, 101: 'Շիրակ, Գյումրի'})
    state.same_day_cache.clear()
    _settings(client, dispatch_customers_off=[103])
    d = apply()
    rows = {o['isn'] for o in d['same_day']['orders']}
    assert _isn(13) not in rows and _isn(10) not in rows and _isn(11) in rows
    # Гюмри убрали из городов: заказ Rocarm везём — берём его в развоз сегодня; завтра его нет (D и D+1)
    _settings(client, dispatch_other_cities=['Կապան'], dispatch_customers_off=[])
    d = apply()
    opts = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(13)]}).get_json()
    assert opts['orders'] == [_isn(13)], opts
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day',
                                                       'orders': [_isn(13)], 'option': opts['options'][0]['key']})
    assert r.status_code == 200, r.get_json()
    assert _isn(13) in dp.Draft.from_json(state.store.load_dispatch(DAY)[0]).same_day
    assert client.get('/api/routes/dispatch?date=2026-10-02').get_json()['orders'].get('same_day_taken') == 1
    # приложение водителя на D+1: взятый не везём; без взятия правило отдало бы его машине заказа
    view = rl.routes_view(state, date(2026, 10, 2))
    every = [_dorder(13, 101, 30.0, day=D, agent=ROCARM, van=ROCARM, car='CAR1')]
    view = replace(view, plan_exists=True, released=True, trips=(('CAR1', (101,)),))   # утверждённый план D+1 (№80)
    assert rl.pick_orders(every, date(2026, 10, 2), view, 'CAR1', lambda ids: {}) == []
    assert rl.pick_orders(every, date(2026, 10, 2), replace(view, taken=frozenset()), 'CAR1', lambda ids: {}) == every


def test_courier_selection_follows_rule(client):
    state = _setup(client)
    assert rl.routes_view(state, D).fleet == dp.FleetRule(cities=tuple(CITIES))     # менеджеров нет — правила нет
    _settings(client, **RULE)
    view = rl.routes_view(state, D)
    assert view.fleet == dp.FleetRule(frozenset({ROCARM}), tuple(CITIES), frozenset({108})) and not view.plan_exists
    asked = []

    def places(ids):
        asked.append(list(ids))
        return {c: (ADDRESSES.get(c, ''), NAMES[c][1]) for c in ids}
    with_car = [replace(o, car_code='CAR1') for o in ORDERS]
    assert rl.pick_orders(with_car, D, view, 'CAR1', places) == [] and asked == []   # плана нет — ничего (№80)
    # утверждённый план отдал машине всех клиентов (№80): отбор — как «Развоз»
    every = (('CAR1', tuple(sorted({o.customer_id for o in ORDERS}))),)
    view = replace(view, plan_exists=True, released=True, trips=every)
    assert _nums(rl.pick_orders(with_car, D, view, 'CAR1', places)) == [1, 2, 5, 11]    # как «Развоз» без плана
    assert asked == [[104, 105, 107]]                                                # ERP — только про Rocarm «сам»
    assert _nums(rl.pick_orders(with_car, D, rl.RoutesView(plan_exists=True, released=True, trips=every), 'CAR1',
                                places)) == [1, 2, 9, 11]                            # правила нет
    # план есть: клиент 105 в рейсах (его заказ 11 — машинам), но заказ Rocarm в Капане — никому
    assert client.post('/api/routes/geo-override', json={'customer_id': 105, 'lat': 40.2, 'lon': 44.55}).status_code == 200
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    view = replace(rl.routes_view(state, D), released=True)                         # как после «Հաստատել» (№80)
    owner = {s['customer_id']: tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert 105 in owner
    mine = _nums(rl.pick_orders(ORDERS, D, view, owner[105], places))
    assert 11 in mine and not {7, 8, 9} & set(mine)


def test_erp_day_passes_place_lookup_to_pick(fake_erp, monkeypatch):
    calls = []
    monkeypatch.setattr(erp, 'place_texts', lambda conn, ids: calls.append(list(ids)) or {12: ('Կապան', '')})
    seen = []

    def pick(orders, places):
        seen.append(places([12]))
        return []
    ed.load_day('cs', '991AT61', date(2026, 10, 2), (date(2026, 9, 29), date(2026, 10, 2)), pick)
    assert calls == [[12]] and seen == [{12: ('Կապան', '')}]


def test_erp_place_texts_and_hint_reason(monkeypatch):
    def fake(conn, sql, params=()):
        if sql.startswith(erp.SQL_CUSTOMERS.split('{')[0]):
            return [(105, 'C105', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ', '', None, False, None)]
        if sql.startswith(erp.SQL_ADDRESS_TEXT.split('{')[0]):
            return [(105, 'Շիրակ, Գյումրի, 1')]
        raise AssertionError(sql[:60])
    monkeypatch.setattr(erp, '_select', fake)
    assert erp.place_texts(object(), [105, 106]) == {105: ('Շիրակ, Գյումրի, 1', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ'), 106: ('', '')}
    hr = dp.hint_reason
    assert hr([], []) == dp.NO_ADDRESS and hr(['  '], [(0, 0)]) == dp.NO_ADDRESS
    assert hr([''], [(40.16, 44.27)]) is None and hr(['Երևան, 1'], []) is None
    assert hr(['г. Краснодар,ул. Ставраполская 125'], []) == dp.ABROAD
    assert hr(['ՌԴ, Մոսկվայի մարզ, ք. Վիդնոե'], []) == dp.ABROAD
    assert hr(['Երևան'], [(45.03, 38.97)]) == dp.ABROAD                              # точка — Краснодар
    assert hr(['г. Ереван, ул. Абовяна 1, Երևան'], []) is None                        # кириллица с армянским — не за границей
    # ревью L3: по-русски, но в Армении
    assert [a for a in ('г. Ереван, ул. Абовяна 1', 'Армения, Армавир, ул. Мира 2', 'г. Гюмри, ул. Ширакаци 3',
                        'Республика Армения, Котайк') if hr([a], [])] == []


def test_erp_customer_hints_and_find(monkeypatch):
    seen = []

    def fake(conn, sql, params=()):
        seen.append((sql, list(params)))
        if sql == erp.SQL_CUSTOMER_ORDERS:
            return [(7092, 13, 31, 271076.0, date(2026, 10, 5)), (7092, 3180, 1, 100.0, date(2026, 9, 1)),
                    (12300, 13, 4, 18044999.0, datetime(2026, 9, 25)), (500, 3160, 9, 9.0, date(2026, 10, 1))]
        if sql.startswith(erp.SQL_DEFAULT_PLACES.split('{')[0]):
            return [(12300, 'г. Краснодар', None, None), (500, 'Երևան, 5', 40.1, 44.5), (7092, '', None, None)]
        if sql.startswith(erp.SQL_CUSTOMERS.split('{')[0]):
            return [(7092, '7092', 'Վարչական/ ռոքարմ', '', None, False, None),
                    (12300, '12300', 'ИП Магдасян', '', None, False, None)]
        if sql == erp.SQL_CUSTOMER_FIND:
            return [(29793, '7092 ', 'Վարչական/ ռոքարմ', None)]
        raise AssertionError(sql[:60])
    monkeypatch.setattr(erp, '_select', fake)
    hints = erp.customer_hints(object(), date(2026, 8, 6), date(2026, 10, 6))
    assert [(h.customer_id, h.reason, h.orders, round(h.revenue), h.last_day, h.agents) for h in hints] == [
        (12300, dp.ABROAD, 4, 18044999, date(2026, 9, 25), (13,)),
        (7092, dp.NO_ADDRESS, 32, 271176, date(2026, 10, 5), (13, 3180))]
    assert hints[0].address == 'г. Краснодар' and hints[1].name == 'Վարչական/ ռոքարմ'
    # поиск: % _ [ ! — буквально; код с начала, название — частью, точный код первым
    refs = erp.find_customers(object(), ' 50%_[x!  ')
    assert refs == [erp.CustomerRef(29793, '7092', 'Վարչական/ ռոքարմ', '')]
    assert seen[-1][1] == ['50!%!_![x!!%', '%50!%!_![x!!%', '50%_[x!']
    assert erp.find_customers(object(), '   ') == []
    for name in ('SQL_CUSTOMER_FIND', 'SQL_CUSTOMER_REFS', 'SQL_CUSTOMER_ORDERS', 'SQL_DEFAULT_PLACES'):
        erp.check_sql(getattr(erp, name).format(ph='?'))


def test_settings_api_customer_search_and_hints(client):
    state = client.application.extensions['route_optimizer']
    state.customer_ref_loader = None
    state.customer_hint_loader = None
    assert client.get('/api/routes/settings/customers?q=7092').get_json() == {'success': True, 'customers': []}
    assert client.get('/api/routes/settings/customer-hints').get_json()['customers'] == []
    calls = []

    def refs(query, ids):
        calls.append((query, ids))
        return [erp.CustomerRef(29793, '7092', 'Վարչական/ ռոքարմ', '')]
    state.customer_ref_loader = refs
    d = client.get('/api/routes/settings/customers?q=7092').get_json()
    assert d['customers'] == [{'customer_id': 29793, 'code': '7092', 'name': 'Վարչական/ ռոքարմ', 'address': ''}]
    client.get('/api/routes/settings/customers?ids=29793,5,5')
    assert calls == [('7092', []), ('', [5, 29793])]
    assert client.get('/api/routes/settings/customers').get_json()['customers'] == [] and len(calls) == 2
    for bad in ('ids=x', 'ids=0', 'ids=1,,2', 'ids=-1', 'ids=99999999999', 'q=' + 'a' * 101,
                'ids=' + ','.join(map(str, range(1, 202)))):
        assert client.get('/api/routes/settings/customers?' + bad).status_code == 400, bad
    assert client.get('/api/routes/settings/customers?ids=' + ','.join(map(str, range(1, 201)))).status_code == 200
    calls.pop()
    got = []
    hint = erp.CustomerHint(29793, '7092', 'Վարչական/ ռոքարմ', '', dp.NO_ADDRESS, 31, 271076.4, date(2026, 10, 5), (1, 77))
    state.customer_hint_loader = lambda since, until: got.append((since, until)) or [hint]
    d = client.get('/api/routes/settings/customer-hints').get_json()
    assert d['days'] == 60 and (got[0][1] - got[0][0]).days == 61
    assert d['customers'] == [{'customer_id': 29793, 'code': '7092', 'name': 'Վարչական/ ռոքարմ', 'address': '',
                               'reason': 'no_address', 'orders': 31, 'revenue': 271076, 'last_day': '2026-10-05',
                               'agents': [{'agent_id': 1, 'code': 'A001', 'name': 'Менеджер 1'},
                                          {'agent_id': 77, 'code': '', 'name': ''}]}]

    def down(*a):
        raise erp.ErpError('нет связи')
    state.customer_ref_loader = down
    assert client.get('/api/routes/settings/customers?q=x').status_code == 503


def test_settings_api_saves_rules_and_lists_rule_managers(client):
    d = client.get('/api/routes/settings').get_json()
    assert (d['settings']['dispatch_fleet_agents'], d['settings']['dispatch_other_cities'],
            d['settings']['dispatch_customers_off']) == ([], CITIES, [])
    _settings(client, dispatch_fleet_agents=[77, 2], dispatch_customers_off=[7092], dispatch_other_cities=['Կապան'])
    d = client.get('/api/routes/settings').get_json()
    assert (d['settings']['dispatch_fleet_agents'], d['settings']['dispatch_customers_off'],
            d['settings']['dispatch_other_cities']) == ([2, 77], [7092], ['Կապան'])
    assert 77 in {a['agent_id'] for a in d['dispatch_agents']}                     # менеджер правила — в списке
    bad = client.post('/api/routes/settings', json={'settings': {'dispatch_other_cities': ['']}})
    assert bad.status_code == 400 and 'settings.dispatch_other_cities' in bad.get_json()['errors']


def test_settings_and_dispatch_pages_have_rule_texts():
    page = (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    djs = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'id="fleet"' in page and 'id="custoff"' in page and "routes_settings.js') }}?v=35" in page
    dpage = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert 'id="dpRuleDiff"' in dpage and 'Կիրառել կարգավորումները' in dpage and 'id="dpOther"' in dpage
    assert "action: 'apply_settings'" in djs and 'other_vehicle' in djs
    assert "'agents', 'fleet', 'custoff'" in js and '/api/routes/settings/customer-hints' in js
    assert 'գնում է այլ մեքենայով' in djs and 'չենք տանում՝ կարգավորումներով' in djs
    assert "routes_dispatch.js') }}?v=92" in (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')



# ============================== ревью M1/M3: собранный день держит свои правила ==============================

def _built(client, state):
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    return d, dp.Draft.from_json(state.store.load_dispatch(DAY)[0])


def test_built_day_keeps_its_rule_when_settings_change(client, monkeypatch):
    state = _setup(client)
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN))   # накануне
    assert client.post('/api/routes/geo-override', json={'customer_id': 105, 'lat': 40.2, 'lon': 44.55}).status_code == 200
    _settings(client, **RULE)
    d, draft = _built(client, state)
    assert draft.fleet == {'agents': [ROCARM], 'cities': CITIES, 'customers_off': [108]}
    assert 'settings_differ' not in d
    plan_before = d['plan']['trucks']
    view_before = rl.routes_view(state, D)
    # настройки поменяли днём: Rocarm снят, Капан и 108 — снова наши; собранный день — как был
    _settings(client, dispatch_fleet_agents=[], dispatch_customers_off=[], dispatch_agents_off=[1])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['plan']['trucks'] == plan_before and (d['orders']['count'], d['orders']['other_vehicle']) == (4, 2)
    assert d['settings_differ'] == {'agents': True, 'fleet': True}
    assert d['new_since_build'] == {'count': 0, 'kg': 0, 'revenue': 0} and d['removed_since_build']['count'] == 0
    view = rl.routes_view(state, D)
    assert view.fleet == view_before.fleet and view.agents_off == frozenset()
    places = lambda ids: {c: (ADDRESSES.get(c, ''), NAMES[c][1]) for c in ids}   # noqa: E731
    for car in ('CAR1', 'CAR2'):
        assert rl.pick_orders(ORDERS, D, view, car, places) == rl.pick_orders(ORDERS, D, view_before, car, places)
    # день без плана — уже по новым настройкам
    nxt = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert 'settings_differ' not in nxt and nxt['agents_off'] == [1]
    assert rl.routes_view(state, date(2026, 10, 2)).fleet == dp.FleetRule(cities=tuple(CITIES))


def test_draft_without_rule_is_byte_identical(client):
    state = _setup(client)
    _built(client, state)
    stored = state.store.load_dispatch(DAY)[0]
    assert 'fleet' not in stored and dp.Draft.from_json(stored).to_json() == dp.Draft.from_json(stored).to_json()
    raw = dp.Draft.from_json(stored).to_json()
    assert 'fleet' not in raw
    # битое правило черновика — пустое
    assert dp.Draft.from_json({**raw, 'fleet': 'x'}).fleet is None
    assert dp.Draft.from_json({**raw, 'fleet': {'agents': ['x', True], 'customers_off': []}}).fleet is None
    assert dp.Draft.from_json({**raw, 'fleet': {'agents': [2], 'cities': ['Կապան', 5]}}).fleet == \
        {'agents': [2], 'cities': ['Կապան'], 'customers_off': []}


def test_apply_settings_button(client, monkeypatch):
    state = _setup(client)
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN))   # накануне
    d, _ = _built(client, state)
    _settings(client, **RULE, dispatch_agents_off=[3])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['settings_differ'] == {'agents': True, 'fleet': True}
    stale = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'] - 1, 'action': 'apply_settings'})
    assert stale.status_code == 409
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'apply_settings'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert 'settings_differ' not in d and d['agents_off'] == [3] and d['orders']['other_vehicle'] == 2
    draft = dp.Draft.from_json(state.store.load_dispatch(DAY)[0])
    assert draft.fleet == {'agents': [ROCARM], 'cities': CITIES, 'customers_off': [108]} and draft.agents_off == {3}
    planned = {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert 108 not in planned                                                     # клиент «не везём» ушёл из рейсов
    assert d['removed_since_build']['count'] == 0                                 # убраны правилом — не «убраны после сборки»
    # прошедший день — нельзя
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 9, 0, tzinfo=YEREVAN))
    _settings(client, dispatch_customers_off=[])
    assert 'settings_differ' not in client.get('/api/routes/dispatch?date=' + DAY).get_json()   # прошедшему — не предлагаем
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'apply_settings'})
    assert r.status_code == 400 and r.get_json()['error'] == views.PAST_DAY_SETTINGS


def test_apply_settings_keeps_started_trips_and_works_on_approved_plan(client, monkeypatch):
    state = _setup(client)
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN))
    assert client.post('/api/routes/geo-override', json={'customer_id': 108, 'lat': 40.2, 'lon': 44.55}).status_code == 200
    d, _ = _built(client, state)
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    assert d.get('approved')
    owner = {s['customer_id']: tr for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert 108 in owner                                                            # внутренний счёт — пока в рейсе
    _settings(client, dispatch_customers_off=[108])
    # утверждённый план — ручная правка разрешена (рейсы ещё не грузятся)
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'apply_settings'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d.get('approved') and 108 not in {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips']
                                            for s in tr['stops']}
    # снова собрать с 108 и «сейчас» — в разгаре дня: рейс с 108 уже в пути — его заказы настройки не снимают
    client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'})
    state.store.delete_dispatch(DAY)   # с чистого листа: выпущенный план «Ջնջել երթերը» не стирает (№80)
    _settings(client, dispatch_customers_off=[])
    d, _ = _built(client, state)
    _settings(client, dispatch_customers_off=[108])
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 1, 17, 0, tzinfo=YEREVAN))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'apply_settings'})
    assert r.status_code == 400 and r.get_json()['error'] == views.SETTINGS_ON_STARTED
    assert 'fleet' not in state.store.load_dispatch(DAY)[0]                       # план не тронут


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
