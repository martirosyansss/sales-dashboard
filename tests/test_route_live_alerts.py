# -*- coding: utf-8 -*-
"""Тревоги карты машин в Telegram (№76 этап 2 → бот №91; route_optimizer.live_alerts + tg_bot): сообщение при начале
(HTML по-армянски, уровень 🔴/🟠/⚪, кнопки), окончание — правкой того же сообщения, окно повтора, тихие часы,
переключатели видов, «уже отправлено» переживает перезапуск (SQLite), бот только при ROUTES_LIVE_ALERTS=1 с токеном и
чатом, сбой HTTP не валит и не теряет сообщение, остановка после 4xx подряд.

Telegram не вызывается: Bot API — подделка (tests/tg_fake.py). Запуск:  python -m pytest tests/test_route_live_alerts.py -q
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import live, live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer.tg_api import TelegramError  # noqa: E402
from test_garage_public import app_v2  # noqa: E402,F401  (настоящий app_v2 с временной базой маршрутов)
from test_route_live import live_app  # noqa: E402,F401
from tg_fake import FakeTelegram, Harness, err  # noqa: E402

Y = ac.YEREVAN
NOW = datetime(2026, 10, 6, 11, 0, tzinfo=Y)
RULES = live.Rules()
TG = la.TgRules()


def at(minutes_ago, now=NOW):
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')


def alert(kind, start_ago, end_ago=None, now=NOW, **extra):
    return {'kind': kind, 'from': at(start_ago, now), 'to': at(end_ago, now) if end_ago is not None else None,
            'active': end_ago is None, **extra}


def card(*alerts, car='CAR1'):
    return {'car_code': car, 'name': 'JAC', 'driver': 'Արամ', 'drivers': ['Արամ'],
            'position': {'lat': 40.1812, 'lon': 44.5133}, 'alerts_log': list(alerts)}


def make(tmp_path, cards, now=NOW, settings=None, **kw):
    return Harness(tmp_path, cards, now, settings, **kw)


# ============================== сообщения ==============================

def test_start_message_html_level_buttons_car_driver_detail_time(tmp_path):
    a = alert('speed', 1, max_kmh=112, lat=40.1923, lon=44.5081)
    h = make(tmp_path, {'CAR1': card(a)})
    assert h.tick() == 1
    msg = h.api.sent('-100')[0]
    assert msg['parse_mode'] == 'HTML' and msg['link_preview_options'] == {'is_disabled': True}
    assert 'disable_notification' not in msg                                   # 🟠 — со звуком
    assert msg['text'].splitlines() == [
        '🟠 <b>Արագության գերազանցում</b>', 'Մեքենա՝ <b>CAR1</b> · JAC', 'Վարորդ՝ Արամ',
        'Արագություն՝ 112 կմ/ժ (սահմանը՝ 90 կմ/ժ)։', 'Ժամ՝ 10:59']
    row = msg['reply_markup']['inline_keyboard'][0]
    assert [b['text'] for b in row] == [la.ACK_TEXT, la.MAP_TEXT, la.WHERE_TEXT]
    assert row[1]['url'] == 'https://yandex.ru/maps/?pt=44.5081,40.1923&z=16&l=map'
    assert row[0]['callback_data'].startswith(f'a:{h.rec("alert:CAR1|speed").id}|')
    # стоянка (обед — отметка), центр — 🔴
    h2 = make(tmp_path, {'CAR2': card(alert('stop', 20, minutes=21, lunch=True, lat=40.1, lon=44.5),
                                      alert('center', 2, lat=40.18, lon=44.51), car='CAR2')}, db='b.db')
    h2.tick()
    stop, center = h2.api.texts()
    assert 'Կանգառի տևողությունը՝ 21 րոպե (ճաշի ժամին)։' in stop and stop.startswith('🟠')
    assert center.startswith('🔴 <b>Փոքր կենտրոնում') and 'Մեքենան մտել է փոքր կենտրոն' in center


def test_html_is_escaped():
    c = {**card(), 'driver': '<b>Ա&Բ</b>', 'name': 'J<A>C'}
    body = la.alert_body(c, alert('center', 1, lat=1.0, lon=1.0), RULES, 'critical')
    assert '&lt;b&gt;Ա&amp;Բ&lt;/b&gt;' in body and 'J&lt;A&gt;C' in body and '<b>Ա' not in body


def test_no_contact_is_info_without_sim_and_end_edits_the_same_message(tmp_path):
    a = alert('no_contact', 7, minutes=7)
    g = alert('gps', 3, gps='no_permission')
    h = make(tmp_path, {'CAR1': card(a, g)})
    assert h.tick() == 2
    nc, gps = h.api.sent('-100')
    # без SIM «нет связи» — ⚪: без звука и кнопок, место — ссылкой в тексте (последняя известная точка)
    assert nc['text'].startswith('⚪ <b>Կապ չկա</b>') and nc['disable_notification'] is True and 'reply_markup' not in nc
    assert 'Վերջին հայտնի դիրքը՝ <a href="https://yandex.ru/maps/?pt=44.5133,40.1812&amp;z=16&amp;l=map">' in nc['text']
    assert gps['text'].startswith('🔴 <b>GPS-ի թույլտվությունը չկա</b>') and gps['reply_markup']['inline_keyboard'][0]
    nc_id, gps_id = h.rec('alert:CAR1|no_contact').message_id, h.rec('alert:CAR1|gps').message_id
    # связь вернулась, GPS включили — правки тех же сообщений, новых нет
    h.set(now=NOW + timedelta(minutes=5),
          cards={'CAR1': card({**a, 'active': False, 'to': at(0)}, {**g, 'active': False, 'to': at(-4)})})
    assert h.tick() == 2 and len(h.api.sent()) == 2
    assert h.api.text('-100', nc_id).startswith('✅ <b>Վերջացավ</b> · տևեց 7 րոպե\n⚪ <b>Կապ չկա</b>')
    assert h.api.text('-100', gps_id).startswith('✅ <b>Վերջացավ</b> · տևեց 7 րոպե\n🔴')
    assert h.api.buttons('-100', gps_id) == [la.MAP_TEXT, la.WHERE_TEXT]           # «Տեսա» убрана
    assert h.rec('alert:CAR1|gps').phase == 'ended'
    assert h.tick() == 0                                                         # окончание — один раз


def test_sim_installed_makes_no_contact_critical_and_levels_follow_settings(tmp_path):
    h = make(tmp_path, {'CAR1': card(alert('no_contact', 7, minutes=7), alert('speed', 1, max_kmh=99, lat=1.0, lon=1.0))},
             settings={'tg_sim_installed': True, 'tg_levels': {**st.DEFAULT_SETTINGS['tg_levels'], 'speed': 'info'}})
    h.tick()
    nc, speed = h.api.sent()
    assert nc['text'].startswith('🔴 <b>Կապ չկա</b>') and 'disable_notification' not in nc
    assert speed['text'].startswith('⚪') and speed['disable_notification'] is True and 'reply_markup' not in speed
    assert la.level_of('late', {'late_kind': 'window'}, TG) == 'critical'
    assert la.level_of('late', {'late_kind': 'return'}, TG) == 'critical'
    assert la.level_of('late', {'late_kind': 'plan'}, TG) == 'warning'
    assert [la.level_of(k, {}, TG) for k in ('gps', 'center', 'stop', 'deviation', 'sequence')] == \
        ['critical', 'critical', 'warning', 'info', 'info']


def test_every_kind_with_an_end_is_edited_and_already_ended_alert_goes_with_check(tmp_path):
    a = alert('speed', 2, max_kmh=100, lat=40.1, lon=44.5)
    h = make(tmp_path, {'CAR1': card(a)})
    h.tick()
    h.set(cards={'CAR1': card({**a, 'active': False, 'to': at(1)})})
    assert h.tick() == 1 and len(h.api.sent()) == 1                            # правка, не новое сообщение
    assert h.api.text('-100', h.rec('alert:CAR1|speed').message_id).startswith('✅ <b>Վերջացավ</b> · տևեց 1 րոպե')
    # тревога кончилась до отправки (не старше RECENT_MIN) — сразу с ✅ и без «Տեսա»
    h2 = make(tmp_path, {'CAR1': card(alert('center', 4, 2, lat=40.18, lon=44.51))}, db='b.db')
    assert h2.tick() == 1
    msg = h2.api.sent()[0]
    assert msg['text'].startswith('✅ <b>Վերջացավ</b> · տևեց 2 րոպե\n🔴')
    assert [b['text'] for b in msg['reply_markup']['inline_keyboard'][0]] == [la.MAP_TEXT, la.WHERE_TEXT]


# ============================== повтор, тихие часы, переключатели ==============================

def test_repeat_window_per_car_and_kind(tmp_path):
    def t(m):
        return NOW + timedelta(minutes=m)

    def speed(minute, active=True, car='CAR1'):
        return {'kind': 'speed', 'from': t(minute).isoformat(), 'to': None if active else t(minute + 1).isoformat(),
                'active': active, 'max_kmh': 100, 'lat': 1.0, 'lon': 1.0}
    h = make(tmp_path, {'CAR1': card(speed(0))}, now=t(0))
    assert h.tick() == 1
    # через 20 мин новая тревога того же вида у той же машины — в окне 30 мин: пропускается и позже не уходит
    h.set(now=t(20), cards={'CAR1': card(speed(0, False), speed(20))})
    h.tick()
    h.set(now=t(45))
    h.tick()
    assert len(h.api.sent()) == 1 and h.bot.records[la.alert_key('CAR1', 'speed', t(20).isoformat())].payload == \
        {'why': 'repeat'}
    # другой вид той же машины и тот же вид у другой машины окном не связаны
    gps = {'kind': 'gps', 'from': t(44).isoformat(), 'to': None, 'active': True, 'gps': 'off'}
    h.set(cards={'CAR1': card(speed(0, False), speed(20, False), gps), 'CAR2': card(speed(44), car='CAR2')})
    assert h.tick() == 2
    # после окна (с начала первого сообщения — 30 мин) новая тревога вида — отправляется; окно — настройка
    h.set(now=t(31), cards={'CAR1': card(speed(0, False), speed(30))})
    k0 = la.alert_key('CAR1', 'speed', t(0).isoformat())
    h2 = make(tmp_path, h.box['cards'], now=t(31), db='c.db')
    h2.bot.records[k0] = la.Rec(k0, 'speed', 'active', t(0).isoformat(), car='CAR1')
    assert h2.tick() == 1
    h3 = make(tmp_path, {'CAR1': card(speed(30))}, now=t(31), db='d.db', settings={'live_repeat_min': 60})
    h3.bot.records[k0] = la.Rec(k0, 'speed', 'active', t(0).isoformat(), car='CAR1')
    assert h3.tick() == 0


def test_quiet_hours_skip_and_never_resend(tmp_path):
    night = datetime(2026, 10, 6, 21, 30, tzinfo=Y)
    h = make(tmp_path, {'CAR1': card(alert('gps', 8, now=night, gps='off'))}, now=night)
    assert h.tick() == 0
    for hour in (3, 7):                                          # 20:00–08:00
        h.set(now=datetime(2026, 10, 7, hour, 0, tzinfo=Y))
        assert h.tick() == 0
    h.set(now=datetime(2026, 10, 7, 8, 5, tzinfo=Y))             # утром — тревога ещё идёт, но замечена в тихие часы
    assert h.tick() == 0 and h.api.sent() == []
    # границы: 08:00 — уже не тихо, 20:00 — тихо; настройка «с = до» — без тихих часов
    q = live.Rules()
    assert la.in_quiet(q, datetime(2026, 10, 6, 8, 0, tzinfo=Y)) is False
    assert la.in_quiet(q, datetime(2026, 10, 6, 7, 59, tzinfo=Y)) is True
    assert la.in_quiet(q, datetime(2026, 10, 6, 20, 0, tzinfo=Y)) is True
    assert la.in_quiet(q, datetime(2026, 10, 6, 19, 59, tzinfo=Y)) is False
    assert la.in_quiet(live.Rules(quiet=None), datetime(2026, 10, 6, 23, 0, tzinfo=Y)) is False
    assert live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_quiet_from': '09:00', 'live_quiet_to': '09:00'}).quiet is None
    assert live.Rules.from_settings({**st.DEFAULT_SETTINGS, 'live_quiet_from': '22:00', 'live_quiet_to': '06:30'}).quiet == (1320, 390)


def test_end_in_quiet_hours_is_a_silent_edit_never_a_new_message(tmp_path):
    evening = datetime(2026, 10, 6, 19, 50, tzinfo=Y)
    a = alert('gps', 1, now=evening, gps='off')
    h = make(tmp_path, {'CAR1': card(a)}, now=evening)
    assert h.tick() == 1
    night = datetime(2026, 10, 6, 20, 10, tzinfo=Y)
    h.set(now=night, cards={'CAR1': card({**a, 'active': False, 'to': at(1, night)})})
    assert h.tick() == 1 and len(h.api.sent()) == 1                           # правка беззвучна — и ночью
    assert h.api.of('editMessageText')[-1]['text'].startswith('✅')
    # сообщения нет (перенесено из прежнего потока) — новое ⚪ об окончании, но не ночью
    h.bot.records[la.alert_key('CAR1', 'gps', a['from'])].message_id = None
    h.bot.records[la.alert_key('CAR1', 'gps', a['from'])].phase = 'active'
    assert h.tick() == 0 and len(h.api.sent()) == 1


def test_kind_toggles(tmp_path):
    cards = {'CAR1': card(alert('speed', 1, max_kmh=100, lat=1.0, lon=1.0), alert('gps', 1, gps='off'),
                          alert('stop', 20, minutes=20, lat=1.0, lon=1.0))}
    h = make(tmp_path, cards, settings={'live_alert_kinds': ['gps']})
    assert h.tick() == 1 and h.api.texts()[0].startswith('🔴 <b>GPS-ն անջատված է')
    h.set(settings={**h.box['settings'], 'live_alert_kinds': []})
    assert h.tick() == 0
    # включили позже — идущая тревога уходит (отключённые виды не «решаются»)
    h.set(settings={**h.box['settings'], 'live_alert_kinds': ['speed', 'gps']})
    assert h.tick() == 1 and 'Արագության գերազանցում' in h.api.texts()[1]


def test_old_ended_alerts_are_not_sent_after_downtime(tmp_path):
    cards = {'CAR1': card(alert('speed', 120, 119, max_kmh=100, lat=1.0, lon=1.0),     # кончилась 2 ч назад
                          alert('speed', 5, 3, max_kmh=100, lat=1.0, lon=1.0))}         # 3 мин назад — ещё шлём
    h = make(tmp_path, cards)
    assert h.tick() == 1
    assert {r.payload.get('why') for r in h.bot.records.values()} == {'old', None}


def test_minor_and_explained_are_neither_sent_nor_recorded(tmp_path):
    h = make(tmp_path, {'CAR1': card(alert('deviation', 3, km=1.0, lat=1.0, lon=1.0, minor=True),
                                     alert('sequence', 3, skipped=[], explained={'reason': 'road'}))},
             settings={'live_alert_kinds': list(st.LIVE_ALERT_KINDS)})
    assert h.tick() == 0 and h.bot.records == {}


# ============================== «уже отправлено» ==============================

def test_dedupe_within_tick_loop_and_across_restart(tmp_path):
    a = alert('gps', 6, gps='off')
    cards = {'CAR1': card(a, alert('speed', 1, max_kmh=100, lat=1.0, lon=1.0))}
    h = make(tmp_path, cards)
    assert h.tick() == 2 and h.tick() == 0 and len(h.api.sent()) == 2
    gps_mid = h.rec('alert:CAR1|gps').message_id
    # перезапуск сервера: новый бот читает базу — тех же тревог не повторяет, окончание — правка прежнего сообщения
    h.restart()
    assert h.tick() == 0 and len(h.api.sent()) == 2
    h.set(now=NOW + timedelta(minutes=2), cards={'CAR1': card({**a, 'active': False, 'to': at(0, NOW + timedelta(minutes=2))},
                                                              cards['CAR1']['alerts_log'][1])})
    assert h.tick() == 1 and h.api.of('editMessageText')[-1]['message_id'] == gps_mid
    h.restart()
    assert h.tick() == 0


def test_records_older_than_keep_days_are_pruned(tmp_path):
    h = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    h.tick()
    h.set(now=NOW + timedelta(days=9), cards={})
    h.tick()
    assert h.bot.records == {} and h.store.tg_messages('') == []


def test_corrupt_payload_in_db_does_not_crash_and_is_not_resent(tmp_path, caplog):
    import sqlite3
    from contextlib import closing
    a = alert('gps', 1, gps='off')
    h = make(tmp_path, {'CAR1': card(a)})
    h.tick()
    with closing(sqlite3.connect(h.store.path)) as conn:
        conn.execute("UPDATE tg_message SET payload = '{not json'")
        conn.commit()
    h.restart()
    assert h.tick() == 0 and 'битый payload' in caplog.text


# ============================== сбой HTTP ==============================

def test_send_failure_is_logged_retried_with_backoff_and_not_lost(tmp_path, caplog):
    h = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    h.api.fail['sendMessage'] = [err(502, 'Bad Gateway')]
    assert h.tick() == 0 and h.bot.failures == 1
    assert 'не отправлено' in caplog.text and 'HTTP 502' in caplog.text
    assert h.bot.retry_at == pytest.approx(1000.0 + 2 * la.INTERVAL_S) and h.bot.records == {}
    h.clock[0] += 10
    assert h.tick() == 0 and len(h.api.of('sendMessage')) == 1             # пауза — Telegram не дёргаем
    h.clock[0] += 60
    h.api.fail['sendMessage'] = [TelegramError('URLError')]
    assert h.tick() == 0 and h.bot.failures == 2                            # снова сбой — пауза длиннее
    assert h.bot.retry_at - h.clock[0] == pytest.approx(4 * la.INTERVAL_S)
    for _ in range(10):
        h.clock[0] += la.BACKOFF_MAX_S + 1
        h.api.fail['sendMessage'] = [TelegramError('timeout')]
        h.tick()
    assert h.bot.retry_at - h.clock[0] <= la.BACKOFF_MAX_S
    h.clock[0] += la.BACKOFF_MAX_S + 1
    assert h.tick() == 1 and h.bot.failures == 0                            # дошло, ничего не потеряно
    assert h.tick() == 0


def test_429_waits_at_least_retry_after(tmp_path):
    h = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    h.api.fail['sendMessage'] = [err(429, 'Too Many Requests: retry after 900', retry_after=900)]
    h.tick()
    assert h.bot.retry_at - h.clock[0] == pytest.approx(900) and h.bot.client_errors == 0


def test_halts_after_five_client_errors_until_settings_change(tmp_path, caplog):
    h = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))})
    for _ in range(la.CLIENT_ERRORS_MAX):
        h.clock[0] += la.BACKOFF_MAX_S + 1
        h.api.fail['sendMessage'] = [err(403, 'Forbidden: bot was kicked from the supergroup chat')]
        h.tick()
    assert h.bot.halted is not None and 'ОСТАНОВЛЕНЫ' in caplog.text
    n = caplog.text.count('ОСТАНОВЛЕНЫ')
    h.clock[0] += la.BACKOFF_MAX_S + 1
    calls = len(h.api.calls)
    assert h.tick() == 0 and len(h.api.calls) == calls                      # остановлено: Telegram не дёргаем
    assert caplog.text.count('ОСТАНОВЛЕНЫ') == n                             # и в журнал не пишем снова
    h.set(settings={**h.box['settings'], 'live_repeat_min': 45})             # настройки тревог сменили — пробуем
    assert h.tick() == 1 and h.bot.halted is None
    # 429 и сетевые сбои «4xx подряд» не копят
    h2 = make(tmp_path, {'CAR1': card(alert('gps', 1, gps='off'))}, db='b.db')
    for _ in range(la.CLIENT_ERRORS_MAX + 2):
        h2.clock[0] += la.BACKOFF_MAX_S + 1
        h2.api.fail['sendMessage'] = [err(429, 'Too Many Requests', retry_after=1)]
        h2.tick()
    assert h2.bot.halted is None


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


def _wait(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_threads_not_started_without_env_and_started_with_it(app_v2, monkeypatch, tmp_path):
    import route_optimizer
    for k in (la.ENABLE_ENV, la.TOKEN_ENV, la.TOKEN_FALLBACK_ENV, la.CHAT_ENV):
        monkeypatch.delenv(k, raising=False)
    api = FakeTelegram()
    assert route_optimizer.start_live_alerts(app_v2.app, api=api) is None
    monkeypatch.setenv(la.ENABLE_ENV, '1')
    assert route_optimizer.start_live_alerts(app_v2.app, api=api) is None   # токена и чата нет
    monkeypatch.setenv(la.TOKEN_ENV, 'tok')
    assert route_optimizer.start_live_alerts(app_v2.app, api=api) is None   # чата нет
    assert api.calls == []

    # включено: поток тревог читает карточки флота и шлёт новую тревогу, поток обновлений читает getUpdates;
    # после «перезапуска» (новые потоки, та же база) — тех же тревог нет
    state = app_v2.app.extensions['route_optimizer']
    monkeypatch.setattr(state.store, 'path', str(tmp_path / 'routes.db'))
    monkeypatch.setenv(la.CHAT_ENV, '-100')
    cards = {'CAR1': card(alert('gps', 1, gps='off'))}
    ctx = type('Ctx', (), {'rules': RULES})()
    monkeypatch.setattr(route_optimizer, '_live_cards', lambda s, day: (ctx, datetime.now(Y), {}, cards))
    cards['CAR1']['alerts_log'][0]['from'] = (datetime.now(Y) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(state, 'live_facts', object())
    monkeypatch.setattr(la, 'in_quiet', lambda rules, now: False)
    t1 = route_optimizer.start_live_alerts(app_v2.app, api=api, interval_s=0.05, poll_timeout=0)
    try:
        assert _wait(lambda: api.sent() and api.of('getUpdates'))
        assert len(api.sent()) == 1 and 'GPS-ն անջատված է' in api.sent()[0]['text']
        assert t1.updates.is_alive() and api.of('deleteWebhook')
    finally:
        t1.stop_event.set()
        t1.join(2)
        t1.updates.join(2)
    t2 = route_optimizer.start_live_alerts(app_v2.app, api=api, interval_s=0.05, poll_timeout=0)
    try:
        time.sleep(0.4)
        assert len(api.sent()) == 1                                         # перезапуск тех же тревог не повторяет
    finally:
        t2.stop_event.set()
        t2.join(2)
        t2.updates.join(2)


def test_thread_survives_source_failure(app_v2, monkeypatch, tmp_path):
    import route_optimizer
    monkeypatch.setenv(la.ENABLE_ENV, '1')
    monkeypatch.setenv(la.TOKEN_ENV, 'tok')
    monkeypatch.setenv(la.CHAT_ENV, '-100')
    state = app_v2.app.extensions['route_optimizer']
    monkeypatch.setattr(state.store, 'path', str(tmp_path / 'routes.db'))
    monkeypatch.setattr(state, 'live_facts', object())
    calls = []

    def boom(s, day):
        calls.append(1)
        raise RuntimeError('courier.db is locked')
    monkeypatch.setattr(route_optimizer, '_live_cards', boom)
    api = FakeTelegram()
    api.fail['getUpdates'] = [err(409, 'Conflict: terminated by other getUpdates request')]
    t = route_optimizer.start_live_alerts(app_v2.app, api=api, interval_s=0.03, poll_timeout=0)
    try:
        assert _wait(lambda: len(calls) >= 3)
        assert t.is_alive() and t.updates.is_alive()                        # упало, но потоки живут и пробуют снова
    finally:
        t.stop_event.set()
        t.join(2)
        t.updates.join(2)


def test_unreadable_store_does_not_start_bot_and_does_not_raise(app_v2, monkeypatch, tmp_path, caplog):
    import route_optimizer
    monkeypatch.setenv(la.ENABLE_ENV, '1')
    monkeypatch.setenv(la.TOKEN_ENV, 'tok')
    monkeypatch.setenv(la.CHAT_ENV, '-100')
    state = app_v2.app.extensions['route_optimizer']
    bad = tmp_path / 'routes.db'
    bad.write_text('not a database', encoding='utf-8')
    monkeypatch.setattr(state.store, 'path', str(bad))
    assert route_optimizer.start_live_alerts(app_v2.app, api=FakeTelegram()) is None
    assert 'Telegram-бот не запущен' in caplog.text


# ============================== настройки ==============================

def test_settings_defaults_and_validation():
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors
    assert out['live_alert_kinds'] == ['speed', 'stop', 'no_contact', 'gps', 'center', 'late']   # по умолчанию все, кроме deviation
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


def test_tg_settings_defaults_and_validation():
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors
    assert out['tg_levels'] == {'no_contact': 'critical', 'gps': 'critical', 'center': 'critical',
                                'late_window': 'critical', 'late_return': 'critical', 'late_plan': 'warning',
                                'speed': 'warning', 'stop': 'warning', 'deviation': 'info', 'sequence': 'info'}
    assert (out['tg_sim_installed'], out['tg_escalate_min'], out['tg_escalate_to'], out['tg_summary_at']) == \
        (False, 10, [838786551], '19:30')
    assert out['tg_report_plan'] is out['tg_report_summary'] is out['tg_report_week'] is True
    # нет ключей (база до №91) — по умолчанию; часть уровней — остальные по умолчанию; вид из будущей версии — мимо
    old = {k: v for k, v in vals.items() if not k.startswith('tg_') or k in ('tg_escalate_min', 'tg_summary_at')}
    out, errors = st.validate_settings(old, None)
    assert not errors and out['tg_levels']['gps'] == 'critical' and out['tg_escalate_to'] == [838786551]
    out, errors = st.validate_settings({**vals, 'tg_levels': {'speed': 'critical', 'future': 'xxx'}}, None)
    assert not errors and out['tg_levels']['speed'] == 'critical' and 'future' not in out['tg_levels']
    out, _ = st.validate_settings({**vals, 'tg_escalate_to': [5, 838786551, 5], 'tg_escalate_min': 0}, None)
    assert out['tg_escalate_to'] == [5, 838786551] and out['tg_escalate_min'] == 0      # без повторов; 0 — без эскалации
    for key, bad in (('tg_levels', {'gps': 'red'}), ('tg_levels', ['gps']), ('tg_escalate_to', [0]),
                     ('tg_escalate_to', ['838786551']), ('tg_escalate_to', [True]), ('tg_escalate_to', list(range(1, 30))),
                     ('tg_escalate_min', 241), ('tg_escalate_min', 2.5), ('tg_escalate_min', None),
                     ('tg_sim_installed', 1), ('tg_report_week', 'yes'), ('tg_summary_at', '7:30')):
        _, errors = st.validate_settings({**vals, key: bad}, None)
        assert key in errors, (key, bad)
    tg = la.TgRules.from_settings({**vals, 'tg_escalate_min': 0, 'tg_summary_at': '18:45', 'tg_report_week': False})
    assert (tg.escalate_min, tg.summary_at, tg.report_week, tg.sim) == (0.0, 1125.0, False, False)
    assert la.TgRules.from_settings(vals) == la.TgRules()


def test_tg_settings_round_trip_through_store(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    bundle = s.load()
    changes, errors = st.validate_payload({'settings': {'tg_sim_installed': True, 'tg_escalate_to': [1, 2],
                                                        'tg_levels': {**bundle.settings['tg_levels'], 'stop': 'info'}}},
                                          bundle, st.RefData(frozenset(), frozenset(), frozenset()))
    assert not errors
    s.save(changes, 'qa')
    got = s.load().settings
    assert got['tg_sim_installed'] is True and got['tg_escalate_to'] == [1, 2] and got['tg_levels']['stop'] == 'info'


# ============================== сквозной и по ревью этапа 2 ==============================

def test_real_cards_flow_into_message(live_app):
    """Сквозной: факт флота → views._live_cards → карточка с журналом тревог → действие с текстом (GPS выключен у CAR1)."""
    from route_optimizer import views
    from test_route_live import API_NOW
    state = live_app.app.extensions['route_optimizer']
    row = state.live_facts.data['2026-10-03']['CAR1']
    off = (API_NOW - timedelta(minutes=2)).isoformat()
    row['device'] = {**row['device'], 'gps': 'off', 'at': off}
    row['devices'] = [(off, 'off')]
    ctx, now, _, cards = views._live_cards(state, API_NOW.date())
    assert 'gps' in [a['kind'] for a in cards['CAR1']['alerts_log']] and cards['CAR9']['alerts_log'] == []
    got = la.plan(cards, ctx.rules, TG, now, {})
    gps = next(a for a in got.actions if isinstance(a, la.Send) and a.kind == 'gps')
    assert gps.body.startswith('🔴 <b>GPS-ն անջատված է</b>\nՄեքենա՝ <b>CAR1</b>') and 'Վարորդ՝ Արամ' in gps.body
    assert gps.map == 'https://yandex.ru/maps/?pt=44.47,40.17&z=16&l=map' and gps.key in got.active


def test_one_bad_car_does_not_stop_others(tmp_path):
    bad = {**alert('gps', 4, 3, gps='off'), 'to': 'not-a-date'}
    h = make(tmp_path, {'CAR1': card(bad), 'CAR3': card(alert('gps', 1, gps='off'), car='CAR3')})
    assert h.tick() == 1 and '<b>CAR3</b>' in h.api.texts()[0]


def test_no_end_when_no_contact_alert_just_disappears():
    """Окончание «нет связи» — только при настоящем возвращении связи: тревога может пропасть из журнала и без неё (день
    закрыт, 20:00, дольше NO_CONTACT_MAX) — правки «✅» не будет."""
    from test_route_live import DEPOT, T0, Track, facts, stop, A, TRUCK, ROAD

    tr = Track().park(DEPOT, 5).drive(A).park(A, 2)
    stops = [stop('S:A', 1, A, 100.0, seq=1)]
    last = T0 + timedelta(minutes=5)
    dev = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}

    def card_at(now, closed_at=None):
        f = facts(tr.pts, stops, [T0, last], dev, last_contact=last, closed_at=closed_at)
        c = live.car_view(T0.date(), now, f, [live.PlanTrip((1,), {})], TRUCK, DEPOT, live.Rules(), ROAD, False)
        return {'CAR1': {**c, 'car_code': 'CAR1', 'name': 'JAC', 'driver': 'Արամ'}}
    now = last + timedelta(minutes=12)
    cards = card_at(now)
    assert [a['kind'] for a in cards['CAR1']['alerts_log'] if a['active']] == ['no_contact']
    got = la.plan(cards, live.Rules(), TG, now, {})
    send = [a for a in got.actions if isinstance(a, la.Send) and a.kind == 'no_contact']
    assert len(send) == 1
    records = {send[0].key: la.Rec(send[0].key, 'no_contact', 'active', now.isoformat(), car='CAR1', message_id=7)}
    for later, closed in ((datetime(2026, 10, 5, 20, 5, tzinfo=Y), None),                    # после 20:00
                          (last + timedelta(hours=4), None),                                 # дольше NO_CONTACT_MAX
                          (now, last + timedelta(minutes=8))):                               # день закрыт
        c = card_at(later, closed)
        assert not any(a['kind'] == 'no_contact' and a['active'] for a in c['CAR1']['alerts_log'])
        out = la.plan(c, live.Rules(), TG, later, records)
        assert [a for a in out.actions if isinstance(a, la.End)] == [] and send[0].key not in out.active, later


def test_bot_key_is_the_live_page_case_since_and_stays_stable(tmp_path):
    """После слияния live-alarm (b22f122): страница отмечает «Տեսա» по случаю alerts.since — это from последней идущей
    тревоги вида. Ключ бота — машина|вид|from той же тревоги: случай у бота и у страницы один; пересчёт карточки
    позже (тот же случай) нового сообщения не даёт, конец — правка того же сообщения."""
    from test_route_live import DEPOT, T0, Track, facts, stop, A, TRUCK, ROAD

    tr = Track().park(DEPOT, 5).drive(A).park(A, 2)
    stops = [stop('S:A', 1, A, 100.0, seq=1)]
    last = T0 + timedelta(minutes=5)
    dev = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}

    def cards_at(now, contact):
        f = facts(tr.pts, stops, sorted({T0, last, contact}), dev, last_contact=contact)
        c = live.car_view(T0.date(), now, f, [live.PlanTrip((1,), {})], TRUCK, DEPOT, live.Rules(), ROAD, False)
        return {'CAR1': {**c, 'car_code': 'CAR1', 'name': 'JAC', 'driver': 'Արամ'}}
    now = last + timedelta(minutes=12)
    cards = cards_at(now, last)
    since = cards['CAR1']['alerts']['since']['no_contact']
    active = [a for a in cards['CAR1']['alerts_log'] if a['kind'] == 'no_contact' and a['active']]
    assert len(active) == 1 and active[0]['from'] == since
    tg = la.TgRules(levels=tuple((k, 'critical' if k == 'no_contact' else v) for k, v in la.TgRules().levels), sim=True)
    got = la.plan(cards, live.Rules(), tg, now, {})
    send = [a for a in got.actions if isinstance(a, la.Send)]
    assert [a.key for a in send] == [la.alert_key('CAR1', 'no_contact', since)]
    records = {send[0].key: la.Rec(send[0].key, 'no_contact', 'active', now.isoformat(), car='CAR1', level='critical',
                                   message_id=5, id=1)}
    later = now + timedelta(minutes=7)                                   # тот же случай: пересчёт — тот же from
    again = cards_at(later, last)
    assert again['CAR1']['alerts']['since']['no_contact'] == since
    assert la.plan(again, live.Rules(), tg, later, records).actions == []
    back = cards_at(later + timedelta(minutes=1), later + timedelta(minutes=1))   # связь вернулась — конец того же
    ends = [a for a in la.plan(back, live.Rules(), tg, later + timedelta(minutes=1), records).actions
            if isinstance(a, la.End)]
    assert [e.key for e in ends] == [send[0].key] and 'no_contact' not in back['CAR1']['alerts']['since']


def test_legacy_state_file_is_migrated_once_and_nothing_is_resent(tmp_path, caplog):
    """После выкладки: «уже отправлено» прежнего потока (route_live_alerts.json) переносится — тех же тревог бот не шлёт;
    окончание перенесённой «нет связи» — отдельным ⚪ (правке нечего править), у перенесённой скорости — без сообщения."""
    gps = alert('gps', 20, gps='off')
    speed = alert('speed', 30, max_kmh=100, lat=1.0, lon=1.0)
    late_a = {'kind': 'late', 'from': at(1), 'to': None, 'active': True, 'target': 'c501', 'late_kind': 'window',
              'name': 'Խանութ', 'over_min': 20, 'eta': at(-60), 'limit': at(-40)}
    legacy = tmp_path / la.STATE_FILE
    legacy.write_text(json.dumps({
        'sent': {f'CAR1|gps|{gps["from"]}': {'at': at(20), 'start': at(20), 'end': None},
                 f'CAR1|speed|{speed["from"]}': {'at': at(30), 'start': at(30), 'end': None},
                 f'CAR1|stop|{at(50)}': {'at': at(50), 'start': None, 'end': None, 'skipped': 'quiet'},
                 f'CAR1|late|2026-10-06|c501': {'at': at(10), 'start': at(10), 'end': None, 'over': 20},
                 'CAR9|gps|2026-10-01T10:00:00+04:00': {'at': '2026-10-01T10:00:00+04:00', 'start': None}},
        'last': {'CAR1|gps': at(20)}}), encoding='utf-8')
    before = legacy.read_bytes()
    h = make(tmp_path, {'CAR1': card(gps, speed, late_a)}, legacy=str(legacy))
    assert {r.key: r.phase for r in h.bot.records.values()} == {
        la.alert_key('CAR1', 'gps', gps['from']): 'active', la.alert_key('CAR1', 'speed', speed['from']): 'active',
        la.alert_key('CAR1', 'stop', at(50)): 'skipped', la.late_key('CAR1', '2026-10-06'): 'active'}   # старше 48 ч — нет
    assert h.tick() == 0 and h.api.sent() == []                       # ни тревог, ни «не успеет» повторно
    assert legacy.read_bytes() == before                              # файл не тронут (откат на прежнюю версию)
    h.set(now=NOW + timedelta(minutes=1), cards={'CAR1': card({**gps, 'active': False, 'to': at(0)},
                                                              {**speed, 'active': False, 'to': at(0)}, late_a)})
    assert h.tick() == 1
    assert len(h.api.sent()) == 1 and h.api.texts()[0].startswith('⚪ <b>GPS-ը կրկին միացված է</b>')
    assert h.api.sent()[0]['disable_notification'] is True
    # второй запуск — перенос не повторяется
    h.restart()
    assert h.store.tg_kv('legacy_json')['records'] == 4
