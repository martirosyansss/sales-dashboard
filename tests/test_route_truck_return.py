# -*- coding: utf-8 -*-
"""Правило владельца 12.10: «машина ещё не вернулась — заказы следующего дня ей не отдаём» (решение: склад её не грузит).

- live.return_status: вернулась ли машина из сегодняшнего дня — 'free' (работы сегодня нет), 'back' (водитель закрыл день
  или все точки закрыты и машина в зоне склада — правило состояния «closed» карты и самого APK), 'out' (день не кончился,
  положение свежее), 'unknown' (данных нет, GPS нет, положение старше 3 ч или уже 20:00, а точка раньше — APK больше не
  пишет трек: возвращения не видно) — «unknown» не запрещает;
- склад, день позже сегодняшнего: машина 'out' — «Բեռնված է» и Բեռնագիր её рейсов — 409 с away и армянской причиной, на
  странице — return {state, at}; снять отметку — можно; вернулась, без работы сегодня, данных нет — как раньше;
- сегодняшний день склада (второй рейс после первого) правило не трогает;
- «Развоз» дня позже сегодняшнего — away (машина → последняя точка GPS), без запрета логисту.

Синтетические данные, без ERP.  Запуск:  python -m pytest tests/test_route_truck_return.py -q
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import live  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer import waybill as wb  # noqa: E402
from test_route_dispatch_same_day import DAY, _build, client  # noqa: E402,F401
from test_route_optimizer import DP_DEPOT  # noqa: E402
from test_route_warehouse import _setup  # noqa: E402

Y = ac.YEREVAN
TODAY = '2026-09-30'                          # среда — сегодня; DAY (01.10) — следующий рабочий: склад грузит на завтра
STORE = (40.1792, 44.4991)                    # магазин в Ереване, далеко от склада
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=Y)


def _track(where, *hm):
    """Трек: точки в where (Ереван) в моменты HH:MM сегодня — по две точки с минутой между ними (одна не скачок)."""
    out = []
    for h, m in hm:
        for k in (0, 1):
            at = datetime(2026, 9, 30, h, m, tzinfo=Y) + timedelta(minutes=k)
            out.append((int(at.timestamp() * 1000), where[0], where[1], 10.0, 0.0, 0.0))
    return out


def _row(stops=('pending',), track=(), closed_at=None):
    return {'stops': [{'stop_id': f'S{i}', 'status': st} for i, st in enumerate(stops)], 'track': list(track),
            'closed_at': closed_at, 'contacts': [], 'last_contact': None}


def _st(row, planned=True, now=NOW):
    return live.return_status(row, planned, DP_DEPOT, now)


# ============================== правило ==============================

def test_return_status_rules():
    # сегодня работы нет — ни рейсов плана, ни точек терминала
    assert _st(None, planned=False) == {'state': 'free', 'at': None}
    assert _st(_row(stops=(), track=_track(STORE, (10, 0))), planned=False)['state'] == 'free'
    # (a) рейс сегодня не кончился: точки открыты, машина у магазина — 'out' с моментом последней точки
    out = _st(_row(stops=('full', 'pending'), track=_track(STORE, (14, 30))))
    assert out == {'state': 'out', 'at': '2026-09-30T14:31:00+04:00'}
    # все точки закрыты, но машина ещё едет на склад — тоже 'out'
    assert _st(_row(stops=('full', 'refused'), track=_track(STORE, (14, 30))))['state'] == 'out'
    # на складе между рейсами дня (точки следующего рейса открыты) — день не кончился: 'out'
    assert _st(_row(stops=('full', 'pending'), track=_track(DP_DEPOT, (14, 30))))['state'] == 'out'
    # точки на терминале без плана сегодня (снят после выпуска) — по факту
    assert _st(_row(stops=('pending',), track=_track(STORE, (14, 30))), planned=False)['state'] == 'out'
    # (b) вернулась: все точки закрыты и последняя точка в зоне склада (правило APK и состояния «closed» карты)…
    back = _st(_row(stops=('full', 'partial'), track=[*_track(STORE, (13, 0)), *_track(DP_DEPOT, (14, 40))]))
    assert back == {'state': 'back', 'at': '2026-09-30T14:41:00+04:00'}
    # …или водитель закрыл день — даже если GPS нет и точки открыты
    closed = _st(_row(stops=('pending',), closed_at='2026-09-30T14:50:00+04:00'))
    assert closed == {'state': 'back', 'at': '2026-09-30T14:50:00+04:00'}
    # (d) данных нет: факта нет, точек на терминале нет, GPS нет — 'unknown' (не запрещает)
    assert _st(None) == {'state': 'unknown', 'at': None}
    assert _st(_row(stops=(), track=_track(STORE, (14, 30)))) == {'state': 'unknown', 'at': '2026-09-30T14:31:00+04:00'}
    assert _st(_row(stops=('pending',))) == {'state': 'unknown', 'at': None}
    # положение старше 3 ч (телефон выключен) — где машина, неизвестно
    assert _st(_row(track=_track(STORE, (11, 58))))['state'] == 'unknown'
    assert _st(_row(track=_track(STORE, (12, 1))))['state'] == 'out'
    # 20:00 — APK сам перестаёт писать трек: точка до 20:00 после 20:00 — возвращения не видно, 'unknown'
    late = datetime(2026, 9, 30, 20, 5, tzinfo=Y)
    assert _st(_row(track=_track(STORE, (19, 40))), now=late) == {'state': 'unknown', 'at': '2026-09-30T19:41:00+04:00'}
    assert _st(_row(track=_track(STORE, (19, 40))), now=late - timedelta(minutes=10))['state'] == 'out'
    # склада нет в настройках — «в зоне склада» не бывает: закрытые точки ещё не возвращение
    assert live.return_status(_row(stops=('full',), track=_track(DP_DEPOT, (14, 0))), True, None, NOW)['state'] == 'out'


def test_day_finished_is_the_live_map_rule():
    """Карта «Մեքենաները առցանց» (состояние «closed») и склад — одно правило конца дня: live.day_finished."""
    import inspect
    assert 'finished = day_finished(stops, closed_at, last, depot)' in inspect.getsource(live.car_view)
    last = ac.TrackFix(NOW, DP_DEPOT[0], DP_DEPOT[1], 10.0, 0.0)
    assert live.day_finished([{'status': 'full'}], None, last, DP_DEPOT)
    assert not live.day_finished([], None, last, DP_DEPOT)                          # точек нет — не «все закрыты»
    assert not live.day_finished([{'status': 'in_progress'}], None, last, DP_DEPOT)
    assert live.day_finished([], NOW, None, None)                                    # водитель закрыл день


# ============================== склад и «Развоз» (API) ==============================

class _Facts:
    """Факт терминалов (live.LiveFacts): день → машина → факт; days — какие дни спрашивали."""

    def __init__(self, rows):
        self.rows, self.days = rows, []

    def fleet(self, day):
        self.days.append(day)
        return self.rows.get(day, {})


def _today_plan(state, cars=('CAR1',)):
    """План сегодня (30.09), выпущенный водителям (№80): у машин cars — рейс."""
    draft = dp.Draft(list(cars), trips=[dp.DraftTrip(i + 1, car, [101 + i]) for i, car in enumerate(cars)],
                     next_id=len(cars) + 1, released={'at': '2026-09-30T07:00:00', 'by': 'logist'})
    assert state.store.save_dispatch(TODAY, draft.to_json(), 'logist') is not None


def _tomorrow(client, monkeypatch, rows, now=NOW, cars=('CAR1',)):
    """Сегодня 30.09: план на завтра (01.10) двумя машинами утверждён (= отправлен); план сегодня — у машин cars; факт
    терминалов сегодня — rows."""
    state = _setup(client, monkeypatch, now=now)
    products = {10: wb.Product(10, '0101', 'Կաթ 1լ', 'հատ', 1.05, 12)}
    state.waybill_loader = lambda isns: wb.Lines({i.upper(): ((10, 30.0),) for i in isns}, frozenset(), products)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    assert d['sent'], d
    _today_plan(state, cars)
    state.live_facts = _Facts({TODAY: rows})
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert w['success'] and w['days'] == [TODAY, DAY], w
    return state, w


def _truck(w, car):
    return next(t for t in w['trucks'] if t['car_code'] == car)


def _mark(client, w, trip, loaded=True):
    return client.post('/api/routes/warehouse/loaded', json={'date': w['day'], 'rev': w['rev'], 'trip': trip,
                                                             'loaded': loaded})


def _wb(client, w, car):
    return client.get('/api/routes/warehouse/waybill', query_string={'date': w['day'], 'truck': car, 'rev': w['rev']})


def test_warehouse_refuses_tomorrow_while_truck_is_out_then_allows_after_return(client, monkeypatch):
    state, w = _tomorrow(client, monkeypatch, {'CAR1': _row(stops=('full', 'pending'), track=_track(STORE, (14, 30)))})
    cars = {t['car_code'] for t in w['trucks']}
    assert 'CAR1' in cars, w
    car1 = _truck(w, 'CAR1')
    assert car1['return'] == {'state': 'out', 'at': '14:31'}
    # (c) CAR2 сегодня без работы — поля нет, всё как раньше
    for t in w['trucks']:
        if t['car_code'] != 'CAR1':
            assert 'return' not in t
    # (a) «Բեռնված է» и Բեռնագիր машины в пути — 409 с away и армянской причиной; ничего не записано
    trip = car1['trips'][0]['id']
    r = _mark(client, w, trip)
    assert r.status_code == 409 and r.get_json() == {'success': False, 'error': views.WAREHOUSE_AWAY, 'conflict': True,
                                                     'away': True}
    assert views.WAREHOUSE_AWAY == 'Մեքենան դեռ չի վերադարձել՝ բեռնել հնարավոր չէ'
    assert all('loaded' not in t for t in state.store.load_dispatch(DAY)[0]['trips'])
    r = _wb(client, w, 'CAR1')
    assert r.status_code == 409 and r.get_json()['away'] is True and r.get_json()['error'] == views.WAREHOUSE_AWAY
    # другая машина плана (сегодня без работы) — грузится и печатается
    for t in w['trucks']:
        if t['car_code'] != 'CAR1':
            assert _wb(client, w, t['car_code']).status_code == 200
            r = _mark(client, w, t['trips'][0]['id'])
            assert r.status_code == 200, r.get_json()
            w = r.get_json()
    # «Развоз» завтра: «Դեռ ճանապարհին» — машина → последняя точка, правки логиста не запрещены
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['away'] == {'CAR1': '14:31'}
    # (b) вернулась: все точки закрыты, машина в зоне склада — можно
    state.live_facts.rows[TODAY]['CAR1'] = _row(stops=('full', 'full'),
                                                track=[*_track(STORE, (14, 30)), *_track(DP_DEPOT, (14, 50))])
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert _truck(w, 'CAR1')['return'] == {'state': 'back', 'at': '14:51'}
    assert _wb(client, w, 'CAR1').status_code == 200
    r = _mark(client, w, _truck(w, 'CAR1')['trips'][0]['id'])
    assert r.status_code == 200, r.get_json()
    assert 'away' not in client.get('/api/routes/dispatch?date=' + DAY).get_json()


def test_unmark_allowed_while_truck_is_out(client, monkeypatch):
    """Отметку, поставленную до выезда (или логистом), склад снимает и пока машина в пути (ответ №78, 19)."""
    state, w = _tomorrow(client, monkeypatch, {'CAR1': _row(stops=('full',), track=[*_track(DP_DEPOT, (14, 0))])})
    trip = _truck(w, 'CAR1')['trips'][0]['id']
    w = _mark(client, w, trip).get_json()
    assert _truck(w, 'CAR1')['trips'][0]['loaded'] is not None
    state.live_facts.rows[TODAY]['CAR1'] = _row(stops=('full', 'pending'), track=_track(STORE, (14, 30)))
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert _truck(w, 'CAR1')['return']['state'] == 'out'
    r = _mark(client, w, trip, loaded=False)
    assert r.status_code == 200 and _truck(r.get_json(), 'CAR1')['trips'][0]['loaded'] is None


def test_no_data_does_not_block_and_shows_unknown(client, monkeypatch):
    """(d) План сегодня у машины был, а терминал ничего не присылал — не запрещаем: «Վերադարձը հայտնի չէ»."""
    state, w = _tomorrow(client, monkeypatch, {})
    assert _truck(w, 'CAR1')['return'] == {'state': 'unknown', 'at': None}
    assert _wb(client, w, 'CAR1').status_code == 200
    assert _mark(client, w, _truck(w, 'CAR1')['trips'][0]['id']).status_code == 200
    assert 'away' not in client.get('/api/routes/dispatch?date=' + DAY).get_json()
    # факт терминалов не прочитан — тоже неизвестно (в журнал), не запрет
    state.live_facts.fleet = lambda day: (_ for _ in ()).throw(RuntimeError('courier.db'))
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert _truck(w, 'CAR1')['return']['state'] == 'unknown'


def test_no_trips_today_allowed_and_rule_off_without_courier(client, monkeypatch):
    """(c) У машины сегодня нет рейсов (план сегодня — у другой) — как раньше; раздела «Առաքիչ» нет — правило выключено."""
    state, w = _tomorrow(client, monkeypatch, {}, cars=('CAR9',))
    assert all('return' not in t for t in w['trucks'])
    state.live_facts = None
    _today_plan(state)
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert all('return' not in t for t in w['trucks'])
    assert _mark(client, w, _truck(w, 'CAR1')['trips'][0]['id']).status_code == 200


def test_today_second_trip_loading_is_not_affected(client, monkeypatch):
    """(e) Сегодняшний день склада: машина в первом рейсе — второй рейс дня грузится как раньше (правило — только для
    следующего дня)."""
    now = datetime(2026, 10, 1, 15, 0, tzinfo=Y)
    state = _setup(client, monkeypatch, now=now)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    state.live_facts = _Facts({DAY: {'CAR1': _row(stops=('full', 'pending'), track=_track(STORE, (14, 30)))}})
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert w['day'] == DAY and all('return' not in t for t in w['trucks'])
    car1 = _truck(w, 'CAR1')
    r = _mark(client, w, car1['trips'][-1]['id'])
    assert r.status_code == 200, r.get_json()
    assert 'away' not in client.get('/api/routes/dispatch?date=' + DAY).get_json()
