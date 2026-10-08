# -*- coding: utf-8 -*-
"""«Не успеет в окно» (ответ владельца №87, п.2): прогноз опоздания по тем же ETA, что у онлайн-карты (live.eta_plan).

Правило (live.late_forecast): магазин с окном приёма — прибытие позже конца окна (виды окна — store.CustomerWindow);
без окна — позже плана не меньше чем на late_nowin_min (30); машина — возвращение на склад после всех рейсов позже конца
рабочего дня (принята переработка — её предела). Только незакрытые точки, только сегодня, только от свежего положения.
Показ: карточка машины и журнал тревог (вид late), «Развоз» (/api/routes/dispatch/progress), Telegram (live_alerts:
одно сообщение на магазин в день, снова — при ухудшении на repeat_min). Настройка late_nowin_min — 5…240, целое.

Синтетические данные, без ERP; базы — временные. Запуск из корня проекта:  python -m pytest tests/test_route_late_forecast.py -q
"""
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import live, live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_live import (A, API_NOW, B, C, DAY, DEPOT, LAN, T0, TRUCK, Track, _session_as, app_v2,  # noqa: E402,F401
                             client, facts, live_app, stop)
from test_route_live_alerts import NOW, Sender, card, make  # noqa: E402

Y = ac.YEREVAN
MIDNIGHT = datetime(DAY.year, DAY.month, DAY.day, tzinfo=Y)
RULES = live.Rules()


def hm(h, m=0, s=0):
    return MIDNIGHT + timedelta(hours=h, minutes=m, seconds=s)


def pending(sid, cid, status='pending'):
    return {'stop_id': sid, 'customer_id': cid, 'name': f'Խանութ {cid}', 'status': status}


def late(stops, arrive, planned=None, windows=None, end=None, rules=RULES, here=None):
    """planned — клиент → плановое ETA (у late_forecast — точка → ETA её рейса: здесь у точек один рейс)."""
    by_stop = {s['stop_id']: (planned or {})[s['customer_id']] for s in stops if s['customer_id'] in (planned or {})}
    return live.late_forecast(DAY, stops, {k: (v, True) for k, v in arrive.items()}, by_stop, windows or {}, end,
                              rules, here)


# ============================== правило (чистая функция) ==============================

def test_window_kinds_late_after_window_end_only():
    """«до», «от — до», «в ± допуск» — опоздание = прибытие позже конца окна; раньше начала — машина ждёт, не опоздание;
    «не раньше» (конца нет) — как без окна: план + late_nowin_min (решение ревью L2)."""
    windows = {1: st.CustomerWindow('before', 600).span(),            # до 10:00
               2: st.CustomerWindow('between', 540, 660).span(),      # 09:00–11:00
               3: st.CustomerWindow('at', 720, None, 15).span(),      # 12:00 ± 15
               4: st.CustomerWindow('after', 900).span()}             # не раньше 15:00
    stops = [pending('S1', 1), pending('S2', 2), pending('S3', 3), pending('S4', 4)]
    got = late(stops, {'S1': hm(10, 12), 'S2': hm(10, 59), 'S3': hm(12, 20), 'S4': hm(17)}, planned={4: hm(12)},
               windows=windows)
    assert [(x['target'], x['late_kind'], x['over_min'], x['limit']) for x in got] == [
        ('c1', 'window', 12, hm(10).isoformat()), ('c3', 'window', 5, hm(12, 15).isoformat()),
        ('c4', 'plan', 300, hm(12).isoformat())]
    assert late(stops[3:], {'S4': hm(12, 20)}, planned={4: hm(12)}, windows=windows) == []   # «не раньше»: +20 < 30
    assert got[0] == {'target': 'c1', 'late_kind': 'window', 'stop_id': 'S1', 'customer_id': 1, 'name': 'Խանութ 1',
                      'eta': hm(10, 12).isoformat(), 'limit': hm(10).isoformat(), 'over_min': 12}
    # раньше начала окна и точно в конец окна — не опоздание; полминуты — округляется до 0
    assert late(stops[1:2], {'S2': hm(8, 30)}, windows=windows) == []
    assert late(stops[1:2], {'S2': hm(11)}, windows=windows) == []
    assert late(stops[1:2], {'S2': hm(11, 0, 29)}, windows=windows) == []
    assert late(stops[1:2], {'S2': hm(11, 1)}, windows=windows)[0]['over_min'] == 1


def test_no_window_threshold_late_nowin_min():
    stops = [pending('S1', 1)]
    planned = {1: hm(12)}
    assert late(stops, {'S1': hm(12, 29)}, planned) == []
    got = late(stops, {'S1': hm(12, 30)}, planned)
    assert [(x['late_kind'], x['over_min'], x['limit']) for x in got] == [('plan', 30, hm(12).isoformat())]
    assert late(stops, {'S1': hm(12, 30)}, planned, rules=live.Rules(late_nowin_min=45)) == []
    assert late(stops, {'S1': hm(18)}) == []                         # нет планового ETA — не оценивается
    assert late(stops, {'S1': hm(11)}, planned) == []                # раньше плана


def test_return_after_shift_end():
    assert late([], {}, end=hm(18, 1)) == [{'target': 'return', 'late_kind': 'return', 'stop_id': None,
                                            'customer_id': None, 'name': None, 'eta': hm(18, 1).isoformat(),
                                            'limit': hm(18).isoformat(), 'over_min': 1}]
    assert late([], {}, end=hm(18, 0, 20)) == [] and late([], {}, end=hm(17, 40)) == [] and late([], {}) == []
    # магазины — по прибытию, возврат — последним
    got = late([pending('S2', 2), pending('S1', 1)], {'S1': hm(12, 40), 'S2': hm(14)}, {1: hm(12), 2: hm(13)},
               end=hm(18, 30))
    assert [x['target'] for x in got] == ['c1', 'c2', 'return']
    rules = live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'truck_work_end': '17:30', 'late_nowin_min': 40})
    assert (rules.work_end, rules.late_nowin_min) == (1050.0, 40.0)
    assert live.Rules.from_settings(st.DEFAULT_SETTINGS).work_end == 1080.0


def test_closed_and_here_stops_ignored_invoices_merged():
    planned = {1: hm(10), 2: hm(10), 3: hm(10)}
    arrive = {f'S{k}': hm(12) for k in range(1, 9)}
    closed = [pending(f'S{k}', 1, status)
              for k, status in enumerate(('full', 'partial', 'refused', 'covered', 'in_progress'), 1)]
    assert late(closed, arrive, planned) == []                       # доставлено / отказ / у магазина — не прогноз
    assert late([pending('S5', 2)], arrive, planned, here='S5') == []   # машина уже у магазина — прибытие — факт
    assert late([pending('S9', 2)], arrive, planned) == []           # прогноза у точки нет (без координаты)
    # две накладные одного магазина — одна строка, худшее опоздание
    got = late([pending('S6', 3), pending('S7', 3)], {'S6': hm(11), 'S7': hm(12)}, planned)
    assert [(x['target'], x['stop_id'], x['over_min']) for x in got] == [('c3', 'S7', 120)]


# ============================== карточка машины ==============================

def _road():
    return live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0, legs=lambda a, b, minute, here: 6.0,
                     unload=lambda p, kg: 10.0)


def _mid(a, b):
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def _day():
    """Рейс 1: A доставлен, машина на полпути к B; рейс 2: C. Участок — 6 мин, у магазина — 10 мин."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(_mid(A, B))
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, seq=2), stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 2), {1: at_a, 2: tr.t - timedelta(minutes=40)}), live.PlanTrip((3,), {3: tr.t})]
    return tr, stops, plan


def _card(tr, stops, plan, now=None, rules=live.Rules(lunch_min=0.0), windows=None, detail=False, **kw):
    now = now or tr.t
    return live.car_view(DAY, now, facts(tr.pts, stops, [T0, now], **kw), plan, TRUCK, DEPOT, rules, _road(), detail,
                         windows)


def test_card_late_list_alerts_and_state():
    tr, stops, plan = _day()
    now = tr.t
    card_ = _card(tr, stops, plan)
    # B: прибытие сейчас + 6 мин, план — 40 мин назад → +46 (≥ 30); C (рейс 2): через склад, плановое — сейчас
    # (B +6, разгрузка 10, склад +6, C +6 = +28 < 30) — не опаздывает
    assert [(x['target'], x['late_kind'], x['over_min']) for x in card_['late']] == [('c2', 'plan', 46)]
    log = [a for a in card_['alerts_log'] if a['kind'] == 'late']
    assert len(log) == 1 and log[0]['active'] and log[0]['from'] == now.isoformat(timespec='seconds')
    assert log[0]['over_min'] == 46 and log[0]['name'] == 'Խանութ 2'
    assert 'late' in card_['alerts']['active'] and card_['state'] == 'moving'   # прогноз — не событие машины
    # окно C кончилось 5 минут назад — магазин с окном, опоздание от конца окна
    end_c = ac.day_minutes(DAY, now) - 5
    card_ = _card(tr, stops, plan, windows={3: (-float('inf'), end_c)})
    c = next(x for x in card_['late'] if x['target'] == 'c3')
    assert c['late_kind'] == 'window' and c['over_min'] == 28 + 5


def test_card_return_late_after_all_trips_not_only_current():
    tr, stops, plan = _day()
    now = tr.t
    # возврат после рейса 1 (return_eta): +6 B, +10, +6 = +22; после всех рейсов: +6 C, +10, +6 = +44
    rules = live.Rules(lunch_min=0.0, work_end=ac.day_minutes(DAY, now) + 30)
    card_ = _card(tr, stops, plan, rules=rules)
    assert card_['return_eta'] == (now + timedelta(minutes=22)).isoformat(timespec='seconds')
    back = card_['late'][-1]
    assert back['target'] == 'return' and back['over_min'] == 14
    assert back['eta'] == (now + timedelta(minutes=44)).isoformat(timespec='seconds')
    assert not [x for x in _card(tr, stops, plan, rules=live.Rules(lunch_min=0.0, work_end=ac.day_minutes(DAY, now) + 44))
                ['late'] if x['target'] == 'return']


def test_no_forecast_without_fresh_gps_and_not_for_other_days():
    tr, stops, plan = _day()
    assert _card(tr, stops, plan, now=tr.t + timedelta(minutes=19))['late']        # положение 19 мин назад — ещё да
    stale = _card(tr, stops, plan, now=tr.t + timedelta(minutes=21))
    assert stale['late'] == [] and 'late' not in stale['alerts']['active']           # давнее положение — нет
    assert live.car_view(DAY, tr.t, facts([], stops, [tr.t]), plan, TRUCK, DEPOT, RULES, _road())['late'] == []   # без GPS
    assert _card(tr, stops, plan, now=tr.t + timedelta(days=1))['late'] == []        # не сегодня — без прогноза
    done = [dict(s, status='full', share=1.0, delivered_at=tr.t.isoformat()) for s in stops]
    assert _card(tr, done, plan)['late'] == []                                       # всё доставлено


def test_eta_plan_end_is_depot_after_last_queued_trip():
    tr, stops, plan = _day()
    rules = live.Rules(lunch_min=0.0)
    queue = [(0, [stops[1]]), (1, [stops[2]])]
    eta = live.eta_plan(DAY, tr.t, tr.pos, queue, {0}, 0, plan, DEPOT, _road(), rules, False, False)
    assert eta.back[0] == tr.t + timedelta(minutes=22) and eta.end == tr.t + timedelta(minutes=44)
    assert live.eta_plan(DAY, tr.t, tr.pos, queue, {0}, 0, plan, None, _road(), rules, False, False).end is None



def test_eta_waits_for_window_opening_before_unloading():
    """Ревью M3: приехала раньше начала окна приёма — ждёт его начала, потом разгрузка; прибытие — без ожидания, следующие
    магазины и возврат — позже на ожидание (как «Развоз»). ETA онлайн-карты меняется так же (точнее)."""
    tr, stops, plan = _day()
    now = tr.t
    rules = live.Rules(lunch_min=0.0)
    m = ac.day_minutes(DAY, now)
    base = _card(tr, stops, plan, detail=True)
    card_ = _card(tr, stops, plan, detail=True, windows={2: (m + 20, float('inf'))})   # B — «не раньше» через 20 мин
    eta = {x['stop_id']: datetime.fromisoformat(x['eta']) for x in card_['stops'] if x['eta']}
    eta0 = {x['stop_id']: datetime.fromisoformat(x['eta']) for x in base['stops'] if x['eta']}
    assert eta['S:B'] == eta0['S:B'] == now.replace(microsecond=0) + timedelta(minutes=6)   # прибытие то же
    assert eta['S:C'] - eta0['S:C'] == timedelta(minutes=14)          # ждал с +6 до +20
    assert datetime.fromisoformat(card_['return_eta']) - datetime.fromisoformat(base['return_eta']) == timedelta(minutes=14)
    # окно уже открыто (начало в прошлом) — без ожидания
    assert _card(tr, stops, plan, detail=True, windows={2: (m - 60, m + 600)})['stops'] == base['stops']
    queue = [(0, [stops[1]]), (1, [stops[2]])]
    end = live.eta_plan(DAY, now, tr.pos, queue, {0}, 0, plan, DEPOT, _road(), rules, False, False,
                        windows={3: (m + 60, m + 120)}).end
    assert end == now + timedelta(minutes=60 + 10 + 6)               # C: прибытие +28, ждёт до +60



def test_truck_at_store_before_window_opens_unloads_from_window_start():
    """Ревью N2: машина уже у магазина, окно ещё не открылось — разгрузка с начала окна (а не «сейчас»), следующие
    магазины — после неё; окно уже открыто — как раньше (остаток стоянки)."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 3)
    now = tr.t
    stops = [stop('S:A', 1, A, 500.0, seq=1), stop('S:B', 2, B, 300.0, seq=2)]
    plan = [live.PlanTrip((1, 2), {})]
    m = ac.day_minutes(DAY, now)

    def eta_b(windows):
        card_ = _card(tr, stops, plan, detail=True, windows=windows)
        assert card_['next']['here'] is True and card_['next']['stop_id'] == 'S:A'
        return datetime.fromisoformat(next(x for x in card_['stops'] if x['stop_id'] == 'S:B')['eta'])
    opened = eta_b({})
    assert eta_b({1: (m + 30, m + 120)}) == now.replace(microsecond=0) + timedelta(minutes=30 + 10 + 6)
    assert eta_b({1: (m - 30, m + 120)}) == opened                   # окно открыто — без ожидания


def test_detail_planned_eta_and_board_delay_from_own_trip():
    """Ревью N3: плановое ETA точки в подробной карточке (и «опоздание» хода дня) — из её рейса, как у «не успеет»."""
    tr, stops, plan = _day()
    plan = [plan[0], live.PlanTrip((3, 2), {3: tr.t, 2: tr.t + timedelta(minutes=50)})]
    b = next(x for x in _card(tr, stops, plan, detail=True)['stops'] if x['stop_id'] == 'S:B')
    assert b['planned_eta'] == (tr.t - timedelta(minutes=40)).isoformat(timespec='seconds')


# ============================== API: карта и «Развоз» ==============================

def _refresh(state):
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # новый факт — пересчёт кэша


def _set(state, depot=DEPOT, **settings):
    b = state.store.load()
    state.store.save(st.Changes({**b.settings, **settings}, True, depot, (), ()), 'qa')
    _refresh(state)


def test_live_api_card_and_journal_show_late(client, live_app):
    state = live_app.app.extensions['route_optimizer']
    state.store.save_customer_window(8, st.CustomerWindow('before', 600), 'qa')
    _refresh(state)
    _session_as(client, 'boss', base=LAN)
    cars = {t['car_code']: t for t in client.get('/api/routes/live', base_url=LAN).get_json()['trucks']}
    lt = cars['CAR1']['late']
    assert [(x['customer_id'], x['late_kind']) for x in lt] == [(8, 'window')] and lt[0]['over_min'] > 0
    assert cars['CAR1']['state'] != 'alert' and 'alerts_log' not in cars['CAR1'] and cars['CAR9']['late'] == []
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert [a['customer_id'] for a in truck['alerts_log'] if a['kind'] == 'late'] == [8]
    # «Гараж» видит карту (только чтение) — с прогнозом; «Развоз» — нет
    _session_as(client, 'garage1', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).get_json()['trucks'][0]['late'] == lt
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).status_code == 403


def test_progress_returns_late_and_overtime_accepted(client, live_app):
    state = live_app.app.extensions['route_optimizer']
    _session_as(client, 'boss', base=LAN)
    _set(state, truck_work_end='11:00', truck_overtime_end='20:00', truck_lunch_min=0)   # обед — внутри дня
    body = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()
    ret = body['returns']['CAR1']
    assert ret['limit'] == '11:00' and ret['late_min'] > 0 and ret['eta'] > '11:00'
    assert body['trucks']['CAR1']['8']['s'] in ('pending', 'late')
    # логист принял переработку (№32) — «не успеет вернуться» только позже её предела
    draft = dp.Draft.from_json(state.store.load_dispatch('2026-10-03')[0])
    draft.overtime_ok = True
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    _refresh(state)
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['returns'] == {}
    # без склада возврата не считаем
    _set(state, depot=None, truck_work_end='11:00', truck_lunch_min=0)
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['returns'] == {}


def test_progress_no_window_store_late_by_threshold(client, live_app):
    """Без окна: прогноз позже плана на late_nowin_min и больше. План магазина 8 — 11:20, прогноз ~11:1x: при пороге 5
    не опаздывает; план, сдвинутый на час раньше (10:20), — опаздывает на ~55 мин."""
    state = live_app.app.extensions['route_optimizer']
    _session_as(client, 'boss', base=LAN)
    _set(state, late_nowin_min=5)
    car = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['trucks']['CAR1']
    assert car['8']['s'] == 'pending' and 'late_kind' not in car['8']
    draft = dp.Draft.from_json(state.store.load_dispatch('2026-10-03')[0])
    draft.prediction = {'trucks': {'CAR1': {'trips': [{'depart': '09:25', 'stops': [[7, '09:40'], [8, '10:20']]}]}}}
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    _refresh(state)
    car = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['trucks']['CAR1']
    assert car['8']['s'] == 'late' and car['8']['late_kind'] == 'plan' and car['8']['delay'] >= 5
    assert abs(car['8']['late_min'] - car['8']['delay']) <= 1        # без окна — насколько позже плана


# ============================== Telegram ==============================

def late_alert(target, over, kind='plan', name=None, eta='2026-10-06T11:40:00+04:00', limit='2026-10-06T11:00:00+04:00'):
    return {'kind': 'late', 'from': NOW.isoformat(), 'to': None, 'active': True, 'target': target, 'late_kind': kind,
            'stop_id': None if target == 'return' else 'S:' + target, 'customer_id': None, 'name': name,
            'eta': eta, 'limit': limit, 'over_min': over}


def test_late_message_groups_stores_of_a_car_once_per_day(tmp_path):
    a = late_alert('c7', 12, 'window', 'Խանութ 7', '2026-10-06T11:12:00+04:00')
    b = late_alert('c8', 40, 'plan', 'Խանութ 8')
    r = late_alert('return', 25, 'return', eta='2026-10-06T18:25:00+04:00', limit='2026-10-06T18:00:00+04:00')
    alerter, sender, box = make(tmp_path, {'CAR1': card(a, b, r)})
    assert alerter.tick() == 1
    assert sender.sent[0].splitlines() == [
        'Չի հասցնում ժամանակին (կանխատեսում)', 'Մեքենա՝ CAR1 · JAC', 'Վարորդ՝ Արամ',
        'Խանութ 7 — կուշանա պատուհանից 12 րոպեով (ժամանում ≈ 11:12, պատուհանը՝ մինչև 11:00)։',
        'Խանութ 8 — կուշանա պլանից 40 րոպեով (ժամանում ≈ 11:40, պլանով՝ 11:00)։',
        'Չի հասցնում վերադառնալ պահեստ՝ +25 րոպե (վերադարձ ≈ 18:25, աշխատանքային օրը՝ մինչև 18:00)։',
        'Որտեղ է հիմա՝ https://yandex.ru/maps/?pt=44.5133,40.1812&z=16&l=map', 'Ժամ՝ 11:00']
    assert alerter.tick() == 0                                       # тот же прогноз — не повторяется
    # хуже, но меньше чем на repeat_min (30) — молчим; на 30 и больше — снова, только этот магазин
    box['cards'] = {'CAR1': card({**a, 'over_min': 41}, b, r)}
    assert alerter.tick() == 0
    box['cards'] = {'CAR1': card({**a, 'over_min': 42}, b, r)}
    assert alerter.tick() == 1 and 'Խանութ 7 — կուշանա պատուհանից 42 րոպեով' in sender.sent[1]
    assert 'Խանութ 8' not in sender.sent[1] and 'պահեստ' not in sender.sent[1]
    # прогноз улучшился (строки нет) и сразу вернулся — тот же случай, без сообщения (ревью: удержание LATE_CLEAR_MIN)
    box['cards'] = {'CAR1': card(b, r)}
    assert alerter.tick() == 0 and alerter.state.sent['CAR1|late|2026-10-06|c7'].get('calm_since')
    box['cards'] = {'CAR1': card({**a, 'over_min': 42}, b, r)}
    assert alerter.tick() == 0 and 'calm_since' not in alerter.state.sent['CAR1|late|2026-10-06|c7']
    # спокойно 16 минут подряд — запись снята; снова «опаздывает» — снова сообщение (ревью L3)
    box['cards'] = {'CAR1': card(b, r)}
    alerter.tick()
    box['now'] = NOW + timedelta(minutes=16)
    assert alerter.tick() == 0 and not [k for k in alerter.state.sent if k.endswith('|c7')]
    box['cards'] = {'CAR1': card({**a, 'over_min': 42}, b, r)}
    assert alerter.tick() == 1 and 'Խանութ 7' in sender.sent[2] and 'Խանութ 8' not in sender.sent[2]
    # перезапуск — «уже отправлено» в файле
    again, sender2, _ = make(tmp_path, {'CAR1': card({**a, 'over_min': 42}, b, r)}, now=box['now'])
    assert again.tick() == 0 and sender2.sent == []
    # другой день — снова
    box['now'] = NOW + timedelta(days=1)
    assert alerter.tick() == 1


def test_late_flapping_at_threshold_is_one_message(tmp_path):
    """Прогноз у порога: «опаздывает» / нет каждые 10 с в течение 10 минут — одно сообщение; потом спокойно 16 минут
    подряд — новый случай, снова «опаздывает» — новое сообщение. Нет свежего GPS (строки нет) — так же, как «не опаздывает»."""
    a = late_alert('c7', 30, name='Խանութ 7')
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)})
    for i in range(60):                                              # 10 минут, пересчёт раз в 10 с
        box['now'] = NOW + timedelta(seconds=10 * i)
        box['cards'] = {'CAR1': card(a) if i % 2 == 0 else card()}
        alerter.tick()
    assert len(sender.sent) == 1
    box['cards'] = {'CAR1': card()}
    start = box['now'] + timedelta(seconds=10)
    for i in range(0, 16 * 60 + 1, 10):                              # 16 минут без опоздания
        box['now'] = start + timedelta(seconds=i)
        alerter.tick()
    box['cards'] = {'CAR1': card(a)}
    box['now'] += timedelta(seconds=10)
    assert alerter.tick() == 1 and len(sender.sent) == 2


def test_late_repeat_step_at_least_15_minutes(tmp_path):
    """Повтор при ухудшении — на max(live_repeat_min, 15): при повторе 5 мин прогноз, скачущий на минуты, не шлёт
    сообщение каждые 5 минут."""
    a = late_alert('c7', 35, name='Խանութ 7')
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)}, rules=live.Rules(repeat_min=5.0))
    assert alerter.tick() == 1
    box['cards'] = {'CAR1': card({**a, 'over_min': 49})}
    assert alerter.tick() == 0                                       # +14 < 15
    box['cards'] = {'CAR1': card({**a, 'over_min': 50})}
    assert alerter.tick() == 1                                       # +15
    assert alerter.state.sent['CAR1|late|2026-10-06|c7']['over'] == 50


def test_store_plan_eta_of_its_own_trip():
    """Плановое ETA точки — из её рейса (live.trip_of), а не из последнего рейса с тем же клиентом (ревью L1)."""
    tr, stops, plan = _day()
    plan = [plan[0], live.PlanTrip((3, 2), {3: tr.t, 2: tr.t + timedelta(minutes=50)})]
    b = next(x for x in _card(tr, stops, plan)['late'] if x['target'] == 'c2')
    assert b['stop_id'] == 'S:B' and b['over_min'] == 46             # план рейса 1 — 40 мин назад


def test_late_quiet_hours_toggle_and_other_kinds_unaffected(tmp_path):
    a = late_alert('c7', 35, name='Խանութ 7')
    quiet = NOW.replace(hour=21)
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)}, now=quiet)
    assert alerter.tick() == 0 and not [k for k in alerter.state.sent if '|late|' in k]   # тихие часы: не отмечено
    box['now'] = NOW.replace(hour=21) + timedelta(hours=11, minutes=30)                   # 08:30 следующего дня
    assert alerter.tick() == 1                                       # прогноз ещё в силе — уходит после тихих часов
    # вид выключен в настройках — не шлём
    off, sender_off, _ = make(tmp_path, {'CAR1': card(a)}, rules=live.Rules(alert_kinds=('speed',)), name='off.json')
    assert off.tick() == 0
    # «не успеет» не занимает окно повтора других видов машины и само им не ограничено
    sp = {'kind': 'speed', 'from': NOW.isoformat(), 'to': None, 'active': True, 'max_kmh': 100, 'lat': 1.0, 'lon': 1.0}
    both, sender_b, box_b = make(tmp_path, {'CAR1': card(a, sp)}, name='both.json')
    assert both.tick() == 2 and 'CAR1|late' not in both.state.last
    box_b['cards'] = {'CAR1': card(a, late_alert('c9', 31, name='Խանութ 9'), sp)}
    assert both.tick() == 1 and 'Խանութ 9' in sender_b.sent[-1]


def test_late_send_failure_not_recorded_and_retried(tmp_path):
    sender = Sender()
    sender.fail = True
    alerter, _, _ = make(tmp_path, {'CAR1': card(late_alert('c7', 35, name='Խանութ 7'))}, sender=sender)
    assert alerter.tick(lambda: 0.0) == 0 and not [k for k in alerter.state.sent if '|late|' in k]
    sender.fail = False
    assert alerter.tick(lambda: 1e9) == 1 and len(sender.sent) == 1


def test_real_cards_flow_into_late_message(live_app):
    state = live_app.app.extensions['route_optimizer']
    state.store.save_customer_window(8, st.CustomerWindow('before', 600), 'qa')
    _refresh(state)
    ctx, now, _, cards = views._live_cards(state, API_NOW.date())
    msgs, _ = la.plan_messages(cards, ctx.rules, now, la.AlertState('nonexistent-dir/none.json'))
    m = next(m for m in msgs if m.kind == 'late')
    assert m.text.startswith('Չի հասցնում ժամանակին (կանխատեսում)\nՄեքենա՝ CAR1')
    assert 'Խանութ 8 — կուշանա պատուհանից' in m.text and 'պատուհանը՝ մինչև 10:00' in m.text
    assert [k for k, _ in m.late] == ['CAR1|late|2026-10-03|c8']


# ============================== настройки ==============================

def test_late_nowin_min_setting_validation():
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors and out['late_nowin_min'] == 30 and 'late' in out['live_alert_kinds']
    for ok in (5, 240, 45, 45.0):
        out, errors = st.validate_settings({**vals, 'late_nowin_min': ok}, None)
        assert not errors and out['late_nowin_min'] == ok, ok
    for bad in (4, 241, 30.5, None, True, '30', float('nan')):
        _, errors = st.validate_settings({**vals, 'late_nowin_min': bad}, None)
        assert 'late_nowin_min' in errors, bad
    assert live.Rules.from_settings({**vals, 'late_nowin_min': 45}).late_nowin_min == 45


def test_late_nowin_min_settings_api_admin_only(client, live_app):
    h = _session_as(client, 'boss', base=LAN)
    bad = client.post('/api/routes/settings', json={'settings': {'late_nowin_min': 3}}, base_url=LAN, headers=h)
    assert bad.status_code == 400 and 'settings.late_nowin_min' in bad.get_json()['errors']
    ok = client.post('/api/routes/settings', json={'settings': {'late_nowin_min': 45}}, base_url=LAN, headers=h)
    assert ok.status_code == 200, ok.get_json()
    assert client.get('/api/routes/settings', base_url=LAN).get_json()['settings']['late_nowin_min'] == 45
    for who in ('u', 'garage1'):
        h = _session_as(client, who, base=LAN)
        r = client.post('/api/routes/settings', json={'settings': {'late_nowin_min': 60}}, base_url=LAN, headers=h)
        assert r.status_code == 403, who


def _kinds_db(tmp_path, kinds, marker=None):
    """База «Маршрутов» со списком видов тревог, сохранённым программой, знавшей виды marker (None — до №87, строки нет)."""
    store = st.Store(str(tmp_path / 'routes.db'))
    store.load()
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('live_alert_kinds', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(kinds),))
        conn.execute('DELETE FROM settings WHERE key = ?', (st.LIVE_KINDS_KNOWN_KEY,))
        if marker is not None:
            conn.execute('INSERT INTO settings(key, value) VALUES(?, ?)', (st.LIVE_KINDS_KNOWN_KEY, json.dumps(marker)))
    return store


def test_alert_kinds_of_old_database_get_late(tmp_path):
    """База до №87 хранит список видов без late (сохранение пишет все ключи): late добавляется включённым — владелец
    просил тревогу в Telegram; выключенное после №87 — остаётся выключенным; пустой список («ничего не слать») — пустым."""
    old = ['speed', 'stop', 'no_contact', 'gps']                     # «центр» владелец снял до №87
    assert _kinds_db(tmp_path, old).load().settings['live_alert_kinds'] == old + ['late']
    assert _kinds_db(tmp_path, old, list(st.LIVE_ALERT_KINDS)).load().settings['live_alert_kinds'] == old
    assert _kinds_db(tmp_path, []).load().settings['live_alert_kinds'] == []
    assert _kinds_db(tmp_path, old, 'garbage').load().settings['live_alert_kinds'] == old + ['late']   # битая отметка


def test_saving_kinds_writes_known_marker(tmp_path):
    store = _kinds_db(tmp_path, ['speed'])
    b = store.load()
    assert b.settings['live_alert_kinds'] == ['speed', 'late']
    store.save(st.Changes({**b.settings, 'live_alert_kinds': ['speed']}, False, None, (), ()), 'qa')   # late сняли
    with closing(sqlite3.connect(store.path)) as conn:
        marker = conn.execute('SELECT value FROM settings WHERE key = ?', (st.LIVE_KINDS_KNOWN_KEY,)).fetchone()
    assert json.loads(marker[0]) == list(st.LIVE_ALERT_KINDS)
    assert store.load().settings['live_alert_kinds'] == ['speed']


def test_unknown_alert_kind_in_database_is_dropped_not_fatal(tmp_path):
    """Вид из более новой версии (откат программы) — при чтении молча мимо: база не «повреждена»; в API — по-прежнему
    ошибка (validate_settings)."""
    store = _kinds_db(tmp_path, ['speed', 'fire', 'late'], [*st.LIVE_ALERT_KINDS, 'fire'])
    assert store.load().settings['live_alert_kinds'] == ['speed', 'late']
    _, errors = st.validate_settings({**st.DEFAULT_SETTINGS, 'live_alert_kinds': ['speed', 'fire']}, None)
    assert 'live_alert_kinds' in errors
