# -*- coding: utf-8 -*-
"""Тревоги карты машин в Telegram-группу (№76, этап 2; route_optimizer.live_alerts): сообщения при начале и окончании
(по-армянски, ссылка на карту Яндекса), окно повтора, тихие часы, переключатели видов, «уже отправлено» переживает
перезапуск, поток только при ROUTES_LIVE_ALERTS=1 с токеном и чатом, сбой HTTP не валит и не теряет сообщение.

Telegram не вызывается: отправка — подделка, HTTP — подделка opener. Запуск:  python -m pytest tests/test_route_live_alerts.py -q
"""
import io
import json
import sys
import threading
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import live, live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_garage_public import app_v2  # noqa: E402,F401  (настоящий app_v2 с временной базой маршрутов)
from test_route_live import live_app  # noqa: E402,F401

Y = ac.YEREVAN
NOW = datetime(2026, 10, 6, 11, 0, tzinfo=Y)
RULES = live.Rules()


def at(minutes_ago, now=NOW):
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')


def alert(kind, start_ago, end_ago=None, **extra):
    return {'kind': kind, 'from': at(start_ago), 'to': at(end_ago) if end_ago is not None else None,
            'active': end_ago is None, **extra}


def card(*alerts, car='CAR1'):
    return {'car_code': car, 'name': 'JAC', 'driver': 'Արամ', 'drivers': ['Արամ'],
            'position': {'lat': 40.1812, 'lon': 44.5133}, 'alerts_log': list(alerts)}


class Sender:
    def __init__(self):
        self.sent = []
        self.fail = False

    def __call__(self, text):
        if self.fail:
            raise la.TelegramError('HTTP 502')
        self.sent.append(text)


def make(tmp_path, cards, rules=RULES, now=NOW, sender=None, name='alerts.json'):
    sender = sender or Sender()
    box = {'cards': cards, 'now': now, 'rules': rules}
    alerter = la.LiveAlerter(lambda: (box['rules'], box['now'], box['cards']), sender, str(tmp_path / name))
    return alerter, sender, box


# ============================== сообщения ==============================

def test_start_message_armenian_with_map_link_car_driver_where_when(tmp_path):
    a = alert('speed', 1, max_kmh=112, lat=40.1923, lon=44.5081)
    alerter, sender, _ = make(tmp_path, {'CAR1': card(a)})
    assert alerter.tick() == 1
    text = sender.sent[0]
    assert text.splitlines() == [
        'Արագության գերազանցում', 'Մեքենա՝ CAR1 · JAC', 'Վարորդ՝ Արամ', 'Արագություն՝ 112 կմ/ժ (սահմանը՝ 90 կմ/ժ)։',
        'Որտեղ՝ https://yandex.ru/maps/?pt=44.5081,40.1923&z=16&l=map', 'Ժամ՝ 10:59']
    # стоянка, центр: свои формулировки; обед — отметка
    alerter, sender, _ = make(tmp_path, {'CAR2': card(alert('stop', 20, minutes=21, lunch=True, lat=40.1, lon=44.5),
                                                      alert('center', 2, lat=40.18, lon=44.51), car='CAR2')}, name='b.json')
    alerter.tick()
    assert 'Կանգառի տևողությունը՝ 21 րոպե (ճաշի ժամին)։' in sender.sent[0]
    assert 'Մեքենան մտել է փոքր կենտրոն' in sender.sent[1]


def test_no_contact_and_gps_use_last_known_position_and_end_messages(tmp_path):
    a = alert('no_contact', 7, minutes=7)
    g = alert('gps', 3, gps='no_permission')
    alerter, sender, box = make(tmp_path, {'CAR1': card(a, g)})
    assert alerter.tick() == 2
    nc, gps = sender.sent
    assert nc.splitlines()[0] == 'Կապ չկա' and 'Կապ չկա՝ 7 րոպե։' in nc
    assert 'Վերջին հայտնի դիրքը՝ https://yandex.ru/maps/?pt=44.5133,40.1812&z=16&l=map' in nc    # места события нет
    assert gps.splitlines()[0] == 'GPS-ի թույլտվությունը չկա'
    # связь вернулась, GPS включили — сообщения об окончании
    box['now'] = NOW + timedelta(minutes=5)
    box['cards'] = {'CAR1': card({**a, 'active': False, 'to': at(0)}, {**g, 'active': False, 'to': at(-4)})}
    assert alerter.tick() == 2
    end_nc, end_gps = sender.sent[2:]
    assert end_nc.splitlines()[0] == 'Կապը վերականգնվեց' and 'Կապ չկար՝ 7 րոպե։' in end_nc and 'Ժամ՝ 11:00' in end_nc
    assert end_gps.splitlines()[0] == 'GPS-ը կրկին միացված է' and 'Անջատված էր՝ 7 րոպե։' in end_gps
    assert alerter.tick() == 0                                   # окончание — один раз


def test_speed_stop_center_have_no_end_message(tmp_path):
    a = alert('speed', 2, max_kmh=100, lat=40.1, lon=44.5)
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)})
    alerter.tick()
    box['cards'] = {'CAR1': card({**a, 'active': False, 'to': at(1)})}
    assert alerter.tick() == 0 and len(sender.sent) == 1


# ============================== повтор, тихие часы, переключатели ==============================

def test_repeat_window_per_car_and_kind(tmp_path):
    def t(m):
        return NOW + timedelta(minutes=m)

    def speed(minute, active=True):
        return {'kind': 'speed', 'from': t(minute).isoformat(), 'to': None if active else t(minute + 1).isoformat(),
                'active': active, 'max_kmh': 100, 'lat': 1.0, 'lon': 1.0}
    first = speed(0)
    alerter, sender, box = make(tmp_path, {'CAR1': card(first)}, now=t(0))
    assert alerter.tick() == 1
    # через 20 мин новая тревога того же вида у той же машины — в окне 30 мин: пропускается и позже не уходит
    second = speed(20)
    box.update(now=t(20), cards={'CAR1': card(speed(0, False), second)})
    assert alerter.tick() == 0
    box['now'] = t(45)
    assert alerter.tick() == 0 and len(sender.sent) == 1
    # другой вид той же машины и тот же вид у другой машины окном не связаны
    gps = {'kind': 'gps', 'from': t(44).isoformat(), 'to': None, 'active': True, 'gps': 'off'}
    box['cards'] = {'CAR1': card(speed(0, False), speed(20, False), gps), 'CAR2': card(speed(44), car='CAR2')}
    assert alerter.tick() == 2
    # после окна (с начала первого сообщения — 30 мин) новая тревога вида — отправляется
    box.update(now=t(31), cards={'CAR1': card(speed(0, False), speed(30))})
    alerter3, sender3, box3 = make(tmp_path, box['cards'], now=t(31), name='c.json')
    alerter3.state.last['CAR1|speed'] = t(0).isoformat()
    alerter3.state.sent[f'CAR1|speed|{t(0).isoformat()}'] = {'at': t(0).isoformat(), 'start': t(0).isoformat(), 'end': None}
    assert alerter3.tick() == 1
    box3['rules'] = live.Rules(repeat_min=60.0)                       # окно — настройка: в 60 мин то же самое пропущено
    alerter4, sender4, _ = make(tmp_path, {'CAR1': card(speed(30))}, rules=live.Rules(repeat_min=60.0), now=t(31), name='d.json')
    alerter4.state.last['CAR1|speed'] = t(0).isoformat()
    assert alerter4.tick() == 0


def test_quiet_hours_skip_and_never_resend(tmp_path):
    night = datetime(2026, 10, 6, 21, 30, tzinfo=Y)
    a = alert('no_contact', 8, minutes=8)
    a = {**a, 'from': at(8, night)}
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)}, now=night)
    assert alerter.tick() == 0
    for hour in (3, 7):                                          # 20:00–08:00
        box['now'] = datetime(2026, 10, 7, hour, 0, tzinfo=Y)
        assert alerter.tick() == 0
    box['now'] = datetime(2026, 10, 7, 8, 5, tzinfo=Y)          # утром — тревога ещё идёт, но замечена в тихие часы
    assert alerter.tick() == 0 and sender.sent == []
    # границы: 08:00 — уже не тихо, 20:00 — тихо; настройка «с = до» — без тихих часов
    q = live.Rules()
    assert la.in_quiet(q, datetime(2026, 10, 6, 8, 0, tzinfo=Y)) is False
    assert la.in_quiet(q, datetime(2026, 10, 6, 7, 59, tzinfo=Y)) is True
    assert la.in_quiet(q, datetime(2026, 10, 6, 20, 0, tzinfo=Y)) is True
    assert la.in_quiet(q, datetime(2026, 10, 6, 19, 59, tzinfo=Y)) is False
    assert la.in_quiet(live.Rules(quiet=None), datetime(2026, 10, 6, 23, 0, tzinfo=Y)) is False
    assert live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_quiet_from': '09:00', 'live_quiet_to': '09:00'}).quiet is None
    assert live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_quiet_from': '22:00', 'live_quiet_to': '06:30'}).quiet == (1320, 390)


def test_end_message_in_quiet_hours_is_dropped(tmp_path):
    a = {**alert('gps', 1, gps='off')}
    alerter, sender, box = make(tmp_path, {'CAR1': card(a)}, now=datetime(2026, 10, 6, 19, 50, tzinfo=Y))
    a = {**a, 'from': at(1, box['now'])}
    box['cards'] = {'CAR1': card(a)}
    assert alerter.tick() == 1
    box['now'] = datetime(2026, 10, 6, 20, 10, tzinfo=Y)
    box['cards'] = {'CAR1': card({**a, 'active': False, 'to': at(1, box['now'])})}
    assert alerter.tick() == 0
    box['now'] = datetime(2026, 10, 7, 9, 0, tzinfo=Y)
    assert alerter.tick() == 0 and len(sender.sent) == 1


def test_kind_toggles(tmp_path):
    cards = {'CAR1': card(alert('speed', 1, max_kmh=100, lat=1.0, lon=1.0), alert('gps', 1, gps='off'),
                          alert('stop', 20, minutes=20, lat=1.0, lon=1.0))}
    rules = live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_alert_kinds': ['gps']})
    alerter, sender, box = make(tmp_path, cards, rules=rules)
    assert alerter.tick() == 1 and sender.sent[0].startswith('GPS-ն անջատված է')
    box['rules'] = live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_alert_kinds': []})
    assert alerter.tick() == 0
    # включили позже — идущая тревога уходит (отключённые виды не «решаются»)
    box['rules'] = live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_alert_kinds': ['speed', 'gps']})
    assert alerter.tick() == 1 and sender.sent[1].startswith('Արագության գերազանցում')


def test_old_ended_alerts_are_not_sent_after_downtime(tmp_path):
    cards = {'CAR1': card(alert('speed', 120, 119, max_kmh=100, lat=1.0, lon=1.0),     # кончилась час назад
                          alert('speed', 5, 3, max_kmh=100, lat=1.0, lon=1.0))}         # кончилась 3 мин назад — ещё шлём
    cards['CAR1']['alerts_log'][1]['from'] = at(5)
    alerter, sender, _ = make(tmp_path, cards)
    assert alerter.tick() == 1


# ============================== «уже отправлено» ==============================

def test_dedupe_within_tick_loop_and_across_restart(tmp_path):
    cards = {'CAR1': card(alert('no_contact', 6, minutes=6), alert('speed', 1, max_kmh=100, lat=1.0, lon=1.0))}
    alerter, sender, box = make(tmp_path, cards)
    assert alerter.tick() == 2 and alerter.tick() == 0 and len(sender.sent) == 2
    state = json.loads((tmp_path / 'alerts.json').read_text(encoding='utf-8'))
    assert len(state['sent']) == 2 and set(state['last']) == {'CAR1|no_contact', 'CAR1|speed'}
    # перезапуск сервера: новый объект читает файл — тех же тревог не повторяет, окончание «нет связи» шлёт один раз
    again, sender2, box2 = make(tmp_path, cards, sender=Sender())
    assert again.tick() == 0 and sender2.sent == []
    box2['now'] = NOW + timedelta(minutes=2)
    box2['cards'] = {'CAR1': card({**cards['CAR1']['alerts_log'][0], 'active': False, 'to': at(0, box2['now'])},
                                  cards['CAR1']['alerts_log'][1])}
    assert again.tick() == 1 and sender2.sent[0].startswith('Կապը վերականգնվեց')
    third, sender3, box3 = make(tmp_path, box2['cards'], now=box2['now'], sender=Sender())
    assert third.tick() == 0


def test_state_file_is_atomic_json_and_old_entries_pruned(tmp_path):
    alerter, sender, box = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    alerter.tick()
    assert [p.name for p in tmp_path.iterdir()] == ['alerts.json']        # временного файла не осталось
    box['now'] = NOW + timedelta(days=3)
    box['cards'] = {}
    alerter.state.save(box['now'])
    assert json.loads((tmp_path / 'alerts.json').read_text(encoding='utf-8')) == {'sent': {}, 'last': {}}


def test_corrupt_state_file_starts_empty_without_crash(tmp_path):
    (tmp_path / 'alerts.json').write_text('{not json', encoding='utf-8')
    alerter, sender, _ = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    assert alerter.tick() == 1


# ============================== сбой HTTP ==============================

def test_send_failure_is_logged_retried_with_backoff_and_not_lost(tmp_path, caplog):
    alerter, sender, box = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    sender.fail = True
    clock = [1000.0]
    assert alerter.tick(lambda: clock[0]) == 0 and alerter.failures == 1
    assert 'не отправлено' in caplog.text and 'HTTP 502' in caplog.text
    assert alerter.retry_at == pytest.approx(1000.0 + 2 * la.INTERVAL_S)
    clock[0] += 10
    assert alerter.tick(lambda: clock[0]) == 0 and alerter.failures == 1   # пауза — Telegram не дёргаем
    clock[0] += 60
    assert alerter.tick(lambda: clock[0]) == 0 and alerter.failures == 2   # снова сбой — пауза длиннее
    assert alerter.retry_at - clock[0] == pytest.approx(4 * la.INTERVAL_S)
    for _ in range(10):
        clock[0] += la.BACKOFF_MAX_S + 1
        alerter.tick(lambda: clock[0])
    assert alerter.retry_at - clock[0] <= la.BACKOFF_MAX_S
    sender.fail = False
    clock[0] += la.BACKOFF_MAX_S + 1
    assert alerter.tick(lambda: clock[0]) == 1 and alerter.failures == 0   # дошло, ничего не потеряно
    assert alerter.tick(lambda: clock[0]) == 0


def test_send_telegram_http_success_error_and_token_never_in_message():
    token = '123456:SECRET-TOKEN'
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def ok(req, timeout):
        calls.append((req.full_url, req.data.decode(), timeout))
        return Resp(b'{"ok": true}')
    la.send_telegram(token, '-1001', 'Բարև', opener=ok)
    url, body, timeout = calls[0]
    assert url == f'https://api.telegram.org/bot{token}/sendMessage' and timeout == la.HTTP_TIMEOUT_S
    assert 'chat_id=-1001' in body and '%D4%B2' in body   # текст — UTF-8 в форме

    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    def net_error(req, timeout):
        raise urllib.error.URLError('no route to ' + req.full_url)

    def not_ok(req, timeout):
        return Resp(b'{"ok": false, "description": "Bad Request: chat not found"}')

    def garbage(req, timeout):
        return Resp(b'<html>')
    for opener in (http_error, net_error, not_ok, garbage):
        with pytest.raises(la.TelegramError) as e:
            la.send_telegram(token, '-1001', 'x', opener=opener)
        assert 'SECRET' not in str(e.value)


# ============================== включение ==============================

def test_config_from_env():
    env = {la.ENABLE_ENV: '1', la.TOKEN_ENV: 't', la.CHAT_ENV: '-100'}
    assert la.config_from_env(env) == ('t', '-100')
    assert la.config_from_env({la.ENABLE_ENV: '1', la.TOKEN_FALLBACK_ENV: 'fb', la.CHAT_ENV: '-100'}) == ('fb', '-100')
    assert la.config_from_env({**env, la.TOKEN_FALLBACK_ENV: 'fb'}) == ('t', '-100')           # свой токен главнее
    assert la.config_from_env({k: v for k, v in env.items() if k != la.ENABLE_ENV}) is None   # выключено
    assert la.config_from_env({**env, la.ENABLE_ENV: '0'}) is None and la.config_from_env({**env, la.ENABLE_ENV: 'true'}) is None
    assert la.config_from_env({la.ENABLE_ENV: '1', la.TOKEN_ENV: 't'}) is None                 # чата нет
    assert la.config_from_env({la.ENABLE_ENV: '1', la.CHAT_ENV: '-100'}) is None               # токена нет
    assert la.config_from_env({la.ENABLE_ENV: '1', la.TOKEN_FALLBACK_ENV: 'fb', 'TELEGRAM_CHAT_ID': '1'}) is None   # чат личный — не группа


def test_thread_not_started_without_env_and_started_with_it(app_v2, monkeypatch, tmp_path):
    import route_optimizer
    for k in (la.ENABLE_ENV, la.TOKEN_ENV, la.TOKEN_FALLBACK_ENV, la.CHAT_ENV):
        monkeypatch.delenv(k, raising=False)
    sent = []
    assert route_optimizer.start_live_alerts(app_v2.app, send=sent.append) is None
    monkeypatch.setenv(la.ENABLE_ENV, '1')
    assert route_optimizer.start_live_alerts(app_v2.app, send=sent.append) is None   # токена и чата нет
    monkeypatch.setenv(la.TOKEN_ENV, 'tok')
    assert route_optimizer.start_live_alerts(app_v2.app, send=sent.append) is None   # чата нет
    assert sent == []

    # включено: поток читает карточки флота и шлёт новую тревогу; после «перезапуска» (новый поток, тот же файл) — нет
    from route_optimizer import views
    state = app_v2.app.extensions['route_optimizer']
    db = tmp_path / 'routes.db'
    monkeypatch.setattr(state.store, 'path', str(db))
    monkeypatch.setenv(la.CHAT_ENV, '-100')
    a = alert('gps', 1, gps='off')
    cards = {'CAR1': card(a)}
    ctx = type('Ctx', (), {'rules': RULES})()
    monkeypatch.setattr(route_optimizer, '_live_cards', lambda s, day: (ctx, NOW, {}, cards))
    state.live_facts = object()
    t1 = route_optimizer.start_live_alerts(app_v2.app, send=sent.append, interval_s=0.05)
    try:
        for _ in range(100):
            if sent:
                break
            time.sleep(0.02)
        assert len(sent) == 1 and sent[0].startswith('GPS-ն անջատված է')
    finally:
        t1.stop_event.set()
        t1.join(2)
    t2 = route_optimizer.start_live_alerts(app_v2.app, send=sent.append, interval_s=0.05)
    try:
        time.sleep(0.4)
        assert len(sent) == 1                                             # перезапуск тех же тревог не повторяет
        assert (tmp_path / la.STATE_FILE).exists()
    finally:
        t2.stop_event.set()
        t2.join(2)


def test_thread_survives_source_failure(app_v2, monkeypatch, tmp_path):
    import route_optimizer
    monkeypatch.setenv(la.ENABLE_ENV, '1')
    monkeypatch.setenv(la.TOKEN_ENV, 'tok')
    monkeypatch.setenv(la.CHAT_ENV, '-100')
    state = app_v2.app.extensions['route_optimizer']
    monkeypatch.setattr(state.store, 'path', str(tmp_path / 'routes.db'))
    state.live_facts = object()
    calls = []

    def boom(s, day):
        calls.append(1)
        raise RuntimeError('courier.db is locked')
    monkeypatch.setattr(route_optimizer, '_live_cards', boom)
    t = route_optimizer.start_live_alerts(app_v2.app, send=lambda text: None, interval_s=0.03)
    try:
        time.sleep(0.3)
        assert len(calls) >= 3 and t.is_alive()                          # упало, но поток живёт и пробует снова
    finally:
        t.stop_event.set()
        t.join(2)


# ============================== настройки ==============================

def test_settings_defaults_and_validation():
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors
    assert out['live_alert_kinds'] == ['speed', 'stop', 'no_contact', 'gps', 'center']      # по умолчанию все
    assert (out['live_quiet_from'], out['live_quiet_to'], out['live_repeat_min']) == ('20:00', '08:00', 30)
    out, errors = st.validate_settings({**vals, 'live_alert_kinds': ['gps', 'speed', 'gps']}, None)
    assert not errors and out['live_alert_kinds'] == ['speed', 'gps']                       # порядок канонический, без повторов
    assert st.validate_settings({**vals, 'live_alert_kinds': []}, None)[0]['live_alert_kinds'] == []   # «ничего не слать»
    for key, bad in (('live_alert_kinds', ['speed', 'fire']), ('live_alert_kinds', 'speed'), ('live_alert_kinds', [1]),
                     ('live_alert_kinds', None), ('live_quiet_from', '25:00'), ('live_quiet_to', '8:00'),
                     ('live_quiet_from', None), ('live_repeat_min', 0), ('live_repeat_min', 1441),
                     ('live_repeat_min', None), ('live_repeat_min', True)):
        _, errors = st.validate_settings({**vals, key: bad}, None)
        assert key in errors, (key, bad)
    rules = live.Rules.from_settings({**vals, 'live_alert_kinds': ['stop'], 'live_repeat_min': 45})
    assert rules.alert_kinds == ('stop',) and rules.repeat_min == 45 and rules.quiet == (1200.0, 480.0)


def test_real_cards_flow_into_message(live_app):
    """Сквозной: факт флота → views._live_cards → карточка с журналом тревог → сообщение (GPS выключен у CAR1)."""
    from route_optimizer import views
    from test_route_live import API_NOW
    state = live_app.app.extensions['route_optimizer']
    row = state.live_facts.data['2026-10-03']['CAR1']
    off = (API_NOW - timedelta(minutes=2)).isoformat()
    row['device'] = {**row['device'], 'gps': 'off', 'at': off}
    row['devices'] = [(off, 'off')]
    ctx, now, _, cards = views._live_cards(state, API_NOW.date())
    assert 'gps' in [a['kind'] for a in cards['CAR1']['alerts_log']] and cards['CAR9']['alerts_log'] == []
    msgs, _ = la.plan_messages(cards, ctx.rules, now, la.AlertState('nonexistent-dir/none.json'))
    gps = next(m for m in msgs if m.kind == 'gps')
    assert gps.text.startswith('GPS-ն անջատված է\nՄեքենա՝ CAR1') and 'Վարորդ՝ Արամ' in gps.text
    assert 'yandex.ru/maps/?pt=44.47,40.17&z=16&l=map' in gps.text


# ============================== по ревью этапа 2 ==============================

def test_corrupt_state_values_are_dropped_on_load_and_one_bad_car_does_not_stop_others(tmp_path):
    good_key = f'CAR2|gps|{at(5)}'
    (tmp_path / 'alerts.json').write_text(json.dumps({
        'sent': {'CAR1|gps|x': {'at': 'garbage', 'start': None, 'end': None},          # битый момент
                 'CAR1|speed|y': {'at': '2026-10-06T10:00:00', 'start': None},          # без часового пояса
                 'CAR1|stop|z': {'at': at(3), 'start': 5, 'end': None},                  # start — не строка
                 good_key: {'at': at(3), 'start': at(3), 'end': None}},
        'last': {'CAR1|speed': 'oops', 'CAR2|gps': at(3)}}), encoding='utf-8')
    state = la.AlertState(str(tmp_path / 'alerts.json'))
    assert list(state.sent) == [good_key] and list(state.last) == ['CAR2|gps']
    # у CAR1 тревога с битым «до» — машину пропускаем с записью в журнал, CAR3 получает своё сообщение
    bad = {**alert('gps', 4, 3, gps='off'), 'to': 'not-a-date'}
    cards = {'CAR1': card(bad), 'CAR3': card(alert('gps', 1, gps='off'), car='CAR3')}
    alerter, sender, _ = make(tmp_path, cards, name='other.json')
    assert alerter.tick() == 1 and 'CAR3' in sender.sent[0]
    # битое «последнее начало» в памяти (файл правили вручную после загрузки) — та же защита
    alerter.state.last['CAR3|stop'] = 'junk'
    cards['CAR3']['alerts_log'].append(alert('stop', 20, minutes=20, lat=1.0, lon=1.0))
    assert alerter.tick() == 0


def test_no_restored_message_when_no_contact_alert_just_disappears():
    """«Կապը վերականգնվեց» — только при настоящем возвращении связи: тревога «нет связи» может пропасть из журнала и без
    неё (день закрыт, 20:00, дольше NO_CONTACT_MAX) — сообщения об окончании не будет."""
    from route_optimizer import live as lv
    from test_route_live import DEPOT, T0, Track, facts, stop, A, TRUCK, ROAD

    tr = Track().park(DEPOT, 5).drive(A).park(A, 2)
    stops = [stop('S:A', 1, A, 100.0, seq=1)]
    last = T0 + timedelta(minutes=5)
    dev = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}

    def card_at(now, closed_at=None):
        f = facts(tr.pts, stops, [T0, last], dev, last_contact=last, closed_at=closed_at)
        c = lv.car_view(T0.date(), now, f, [lv.PlanTrip((1,), {})], TRUCK, DEPOT, live.Rules(), ROAD, False)
        return {'CAR1': {**c, 'car_code': 'CAR1', 'name': 'JAC', 'driver': 'Արամ'}}
    now = last + timedelta(minutes=12)
    cards = card_at(now)
    assert [a['kind'] for a in cards['CAR1']['alerts_log'] if a['active']] == ['no_contact']
    state = la.AlertState('nonexistent-dir/none.json')
    msgs, _ = la.plan_messages(cards, live.Rules(), now, state)
    assert [m.kind for m in msgs if m.kind == 'no_contact'] == ['no_contact']
    key = msgs[0].key
    state.sent[key] = {'at': now.isoformat(), 'start': now.isoformat(), 'end': None}
    # тревога пропала не из-за возвращения связи
    for later, closed in ((datetime(2026, 10, 5, 20, 5, tzinfo=Y), None),                    # после 20:00
                          (last + timedelta(hours=4), None),                                 # дольше NO_CONTACT_MAX
                          (now, last + timedelta(minutes=8))):                               # день закрыт
        c = card_at(later, closed)
        assert not any(a['kind'] == 'no_contact' and a['active'] for a in c['CAR1']['alerts_log'])
        out, _ = la.plan_messages(c, live.Rules(), later, state)
        assert [m for m in out if m.phase == 'end' and m.kind == 'no_contact'] == [], later


def test_halts_after_five_client_errors_until_settings_change(tmp_path, caplog):
    cards = {'CAR1': card(alert('gps', 1, gps='off'))}
    alerter, sender, box = make(tmp_path, cards)

    def forbidden(text):
        raise la.TelegramError('HTTP 403', 403)
    alerter.send = forbidden
    clock = [0.0]
    for _ in range(la.CLIENT_ERRORS_MAX):
        clock[0] += la.BACKOFF_MAX_S + 1
        alerter.tick(lambda: clock[0])
    assert alerter.halted is not None and 'ОСТАНОВЛЕНЫ' in caplog.text
    n = caplog.text.count('ОСТАНОВЛЕНЫ')
    alerter.send = sender
    clock[0] += la.BACKOFF_MAX_S + 1
    assert alerter.tick(lambda: clock[0]) == 0 and sender.sent == []          # остановлено: Telegram не дёргаем
    assert caplog.text.count('ОСТАНОВЛЕНЫ') == n                              # и в журнал не пишем снова
    box['rules'] = live.Rules(repeat_min=45.0)                                # настройки тревог сменили — пробуем
    assert alerter.tick(lambda: clock[0]) == 1 and alerter.halted is None
    # 429 и сетевые сбои «4xx подряд» не копят
    a2, s2, _ = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))}, name='b.json')
    a2.send = lambda text: (_ for _ in ()).throw(la.TelegramError('HTTP 429', 429))
    for _ in range(la.CLIENT_ERRORS_MAX + 2):
        clock[0] += la.BACKOFF_MAX_S + 1
        a2.tick(lambda: clock[0])
    assert a2.halted is None
