# -*- coding: utf-8 -*-
"""Общая «Տեսա» Telegram-бота и карты «Մեքենաները առցանց» (№91 + схема 28 live_ack): нажата в Telegram — отметка карты
(Store.live_ack_put: вид и начало тревоги; «не успеет» — late:window / late:plan без начала); нажата на карте — сообщение
в Telegram правится «✔ Տեսավ <кто>», кнопка снимается, эскалации нет; у отклонения начало ±3 мин; без петли.

Telegram — подделка (tests/tg_fake.py); карта — настоящий Store и настоящий вывод live.car_view.
Запуск:  python -m pytest tests/test_route_tg_live_ack.py -q
"""
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from route_optimizer import live, live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import tg_bot  # noqa: E402
from test_route_tg_bot import NOW, OWNER, alert, at, callback, card, gps_card, late  # noqa: E402
from tg_fake import Harness  # noqa: E402

DAY = NOW.date().isoformat()


def rows(h, day=DAY):
    return {(r['car'], r['key']): r for r in h.store.live_acks(day)}


# ============================== Telegram → карта ==============================

def test_tg_ack_puts_live_ack_with_kind_and_alert_start(tmp_path):
    g = alert('gps', 3, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    rec = h.rec('alert:')
    h.set(now=NOW + timedelta(minutes=1))
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    row = rows(h)[('CAR1', 'gps')]
    assert row['since'] == g['from'] and row['user'] == 'Գոռ' and row['at'] == (NOW + timedelta(minutes=1)).isoformat()


def test_tg_ack_on_late_puts_keys_of_active_rows_without_since(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, 'window'), late('c2', 40, 'plan'))}, NOW)
    h.tick()
    h.bot.handle(callback(h, h.bot.sign(f'a:{h.rec("late:").id}')))
    got = rows(h)
    assert set(got) == {('CAR1', 'late:window'), ('CAR1', 'late:plan')}
    assert all(r['since'] is None and r['user'] == 'Գոռ' for r in got.values())
    # только к плану — один ключ late:plan (как alarmSev страницы)
    h2 = Harness(tmp_path, {'CAR2': card(late('c3', 40, 'plan'), car='CAR2')}, NOW, db='b.db')
    h2.tick()
    h2.bot.handle(callback(h2, h2.bot.sign(f'a:{h2.rec("late:").id}')))
    assert set(rows(h2)) == {('CAR2', 'late:plan')}


def test_tg_ack_day_is_the_alert_start_day_in_yerevan(tmp_path):
    rec = la.Rec('alert:CAR1|gps|2026-10-05T23:58:00+04:00', 'gps', 'active', '2026-10-06T00:01:00+04:00', car='CAR1')
    assert la.map_ack_items(rec) == ('2026-10-05', [('CAR1', 'gps', '2026-10-05T23:58:00+04:00')])
    rec2 = la.Rec('alert:CAR1|gps|2026-10-05T20:30:00+00:00', 'gps', 'active', '', car='CAR1')
    assert la.map_ack_items(rec2)[0] == '2026-10-06'                       # 00:30 по Еревану


# ============================== карта → Telegram ==============================

def test_map_ack_marks_tg_message_removes_button_and_does_not_write_back(tmp_path):
    g = alert('gps', 3, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    rec = h.rec('alert:')
    page_at = (NOW + timedelta(minutes=2)).isoformat()
    h.store.live_ack_put(DAY, [('CAR1', 'gps', g['from'])], 'dispatcher', page_at)
    h.set(now=NOW + timedelta(minutes=3))
    assert h.tick() == 1
    got = h.rec('alert:')
    assert (got.acked_by, got.acked_name, got.acked_at) == (tg_bot.MAP_ACKED_BY, 'dispatcher', page_at)
    assert h.api.text('-100', rec.message_id).endswith('✔ Տեսավ dispatcher · 11:02')
    assert la.ACK_TEXT not in h.api.buttons('-100', rec.message_id)
    assert rows(h)[('CAR1', 'gps')]['at'] == page_at                       # обратно на карту не писали — петли нет
    assert h.tick() == 0
    # кнопка в Telegram после этого — «уже отмечено» этим человеком
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    assert h.api.of('answerCallbackQuery')[-1]['text'] == 'Արդեն նշված է՝ dispatcher'


def test_map_ack_of_another_case_or_kind_does_not_count(tmp_path):
    g = alert('gps', 3, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    h.store.live_ack_put(DAY, [('CAR1', 'gps', at(30))], 'x', NOW.isoformat())          # прежний случай
    h.store.live_ack_put(DAY, [('CAR1', 'speed', g['from'])], 'x', NOW.isoformat())      # другой вид
    h.store.live_ack_put(DAY, [('CAR2', 'gps', g['from'])], 'x', NOW.isoformat())        # другая машина
    h.tick()
    assert h.rec('alert:').acked_by is None


def test_deviation_matches_since_within_three_minutes_like_the_page(tmp_path):
    dev = alert('deviation', 10, km=2.0, lat=1.0, lon=1.0)
    kinds = {'live_alert_kinds': list(st.LIVE_ALERT_KINDS),
             'tg_levels': {**st.DEFAULT_SETTINGS['tg_levels'], 'deviation': 'warning'}}
    start = NOW - timedelta(minutes=10)
    for shift, acked in ((2, True), (-3, True), (4, False)):
        h = Harness(tmp_path, {'CAR1': card(dev)}, NOW, settings=kinds, db=f'd{shift}.db')
        h.tick()
        h.store.live_ack_put(DAY, [('CAR1', 'deviation', (start + timedelta(minutes=shift)).isoformat())], 'd',
                             NOW.isoformat())
        h.tick()
        assert (h.rec('alert:').acked_by is not None) is acked, shift


def test_map_ack_of_late_matches_page_key_and_current_case(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, 'window'))}, NOW)
    h.store.live_ack_put(DAY, [('CAR1', 'late:window', None)], 'd', (NOW - timedelta(minutes=5)).isoformat())
    h.tick()                                                               # отметка раньше случая — не в счёт
    assert h.rec('late:').acked_by is None
    h.store.live_ack_put(DAY, [('CAR1', 'late:plan', None)], 'd', NOW.isoformat())
    h.tick()                                                               # к окну нужна late:window
    assert h.rec('late:').acked_by is None
    h.store.live_ack_put(DAY, [('CAR1', 'late:window', None)], 'd', (NOW + timedelta(seconds=30)).isoformat())
    h.tick()
    assert h.rec('late:').acked_name == 'd'
    # только к плану: подходит и late:plan, и late:window (как serverRow страницы)
    for key in ('late:plan', 'late:window'):
        h2 = Harness(tmp_path, {'CAR2': card(late('c2', 40, 'plan'), car='CAR2')}, NOW, db=f'{key[5:]}.db')
        h2.tick()
        h2.store.live_ack_put(DAY, [('CAR2', key, None)], 'd', NOW.isoformat())
        h2.tick()
        assert h2.rec('late:').acked_name == 'd', key


def test_page_ack_cancels_escalation_also_right_before_sending(tmp_path, monkeypatch):
    g = alert('gps', 1, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    h.set(now=NOW + timedelta(minutes=10))
    h.store.live_ack_put(DAY, [('CAR1', 'gps', g['from'])], 'd', (NOW + timedelta(minutes=9)).isoformat())
    h.tick()
    assert not [t for t in h.api.texts() if t.startswith('❗')] and h.api.sent(str(OWNER)) == []
    # отметка пришла между синхронизацией и отправкой эскалации — перепроверка её видит
    h2 = Harness(tmp_path, {'CAR1': card(g)}, NOW, db='b.db')
    h2.tick()
    h2.set(now=NOW + timedelta(minutes=10))
    real = h2.bot._sync_map_acks

    def late_ack():
        out = real()
        h2.store.live_ack_put(DAY, [('CAR1', 'gps', g['from'])], 'd', (NOW + timedelta(minutes=10)).isoformat())
        return out
    monkeypatch.setattr(h2.bot, '_sync_map_acks', late_ack)
    h2.tick()
    assert not [t for t in h2.api.texts() if t.startswith('❗')] and h2.rec('alert:').acked_name == 'd'


def test_no_loop_between_telegram_and_map(tmp_path):
    g = alert('gps', 3, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    before = rows(h)[('CAR1', 'gps')]
    edits = len(h.api.of('editMessageText'))
    for i in range(3):
        h.set(now=NOW + timedelta(minutes=2 + i))
        h.tick()
    assert rows(h)[('CAR1', 'gps')] == before and len(h.api.of('editMessageText')) == edits
    assert h.rec('alert:').acked_name == 'Գոռ'


# ============================== по ревью ==============================

def test_ack_of_ended_record_does_not_overwrite_map(tmp_path):
    g = alert('gps', 5, 2, gps='off')                                       # кончилась до отправки — запись ended
    h = Harness(tmp_path, {'CAR1': card(g)}, NOW)
    h.tick()
    rec = h.rec('alert:')
    rec.phase = 'ended'
    h.store.live_ack_put(DAY, [('CAR1', 'gps', at(1))], 'page', NOW.isoformat())   # новый случай отмечен на карте
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    assert rows(h)[('CAR1', 'gps')]['user'] == 'page' and rows(h)[('CAR1', 'gps')]['since'] == at(1)


def test_tg_ack_on_late_is_kept_fresh_on_the_map_and_stale_map_rows_do_not_count(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, 'window'))}, NOW)
    h.tick()
    h.bot.handle(callback(h, h.bot.sign(f'a:{h.rec("late:").id}')))
    first = rows(h)[('CAR1', 'late:window')]
    h.set(now=NOW + timedelta(minutes=20))
    h.tick()
    row = rows(h)[('CAR1', 'late:window')]
    assert row['seen_at'] == (NOW + timedelta(minutes=20)).isoformat() and (row['user'], row['at']) == \
        (first['user'], first['at'])                                          # подтверждено ботом, кто/когда — те же
    # отметка карты без начала, подтверждённая 20 мин назад, — страница её уже не признаёт, и бот тоже
    h2 = Harness(tmp_path, {'CAR2': card(late('c2', 20, 'window'), car='CAR2')}, NOW, db='b.db')
    h2.tick()
    h2.store.live_ack_put(DAY, [('CAR2', 'late:window', None)], 'd', (NOW + timedelta(seconds=10)).isoformat())
    h2.set(now=NOW + timedelta(minutes=21), cards={'CAR2': card(late('c2', 20, 'window', now=NOW + timedelta(minutes=21)),
                                                                car='CAR2')})
    h2.tick()
    assert h2.rec('late:').acked_by is None


def test_info_records_are_not_synced_and_pending_tg_ack_wins(tmp_path):
    nc = alert('no_contact', 7, minutes=7)                                  # без SIM — ⚪, кнопки нет
    h = Harness(tmp_path, {'CAR1': card(nc)}, NOW)
    h.tick()
    h.store.live_ack_put(DAY, [('CAR1', 'no_contact', nc['from'])], 'd', NOW.isoformat())
    edits = len(h.api.of('editMessageText'))
    h.tick()
    assert h.rec('alert:').acked_by is None and len(h.api.of('editMessageText')) == edits
    g = alert('gps', 3, gps='off')
    h2 = Harness(tmp_path, {'CAR1': card(g)}, NOW, db='b.db')
    h2.tick()
    rec = h2.rec('alert:')
    h2.bot.pending_acks.add(rec.id)                                        # «Տեսա» в Telegram ждёт записи
    h2.store.live_ack_put(DAY, [('CAR1', 'gps', g['from'])], 'd', NOW.isoformat())
    h2.tick()
    assert h2.rec('alert:').acked_by is None


def test_broken_record_and_db_failure_do_not_break_the_pass(tmp_path, caplog, monkeypatch):
    g = alert('gps', 3, gps='off')
    h = Harness(tmp_path, {'CAR1': card(g), 'CAR2': card(alert('center', 3, lat=1.0, lon=1.0), car='CAR2')}, NOW)
    h.tick()
    h.store.live_ack_put(DAY, [('CAR2', 'center', at(3))], 'd', NOW.isoformat())
    real = la.map_ack_match

    def broken(rec, *a, **kw):
        if rec.car == 'CAR1':
            raise KeyError('x')
        return real(rec, *a, **kw)
    monkeypatch.setattr(la, 'map_ack_match', broken)
    h.set(plan=None)
    h.tick()
    assert 'не сверена' in caplog.text and h.rec('alert:CAR2').acked_name == 'd'
    monkeypatch.setattr(la, 'map_ack_match', real)

    def down(day):
        raise st.StoreError('database is locked')
    monkeypatch.setattr(h.store, 'live_acks', down)
    h.set(now=NOW + timedelta(minutes=10))
    h.tick()                                                               # эскалация CAR1 идёт, хоть карта не читается
    assert [t for t in h.api.texts() if t.startswith('❗')] and 'не прочитаны' in caplog.text


def test_review2_weakened_late_keeps_window_row_fresh(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, 'window'))}, NOW)
    h.tick()
    h.bot.handle(callback(h, h.bot.sign(f'a:{h.rec("late:").id}')))
    assert set(rows(h)) == {('CAR1', 'late:window')}
    t = NOW + timedelta(minutes=15)                                        # теперь опаздывает только к плану
    h.set(now=t, cards={'CAR1': card(late('c2', 40, 'plan', now=t))})
    h.tick()
    assert rows(h)[('CAR1', 'late:window')]['seen_at'] == t.isoformat() and set(rows(h)) == {('CAR1', 'late:window')}


def test_review2_old_deviation_button_does_not_overwrite_newer_case_on_the_map(tmp_path):
    dev = alert('deviation', 60, km=2.0, lat=1.0, lon=1.0)
    kinds = {'live_alert_kinds': list(st.LIVE_ALERT_KINDS),
             'tg_levels': {**st.DEFAULT_SETTINGS['tg_levels'], 'deviation': 'warning'}}
    h = Harness(tmp_path, {'CAR1': card(dev)}, NOW, settings=kinds)
    h.tick()
    old = h.rec('alert:')
    newer = at(10)                                                         # страница отметила более поздний случай
    h.store.live_ack_put(DAY, [('CAR1', 'deviation', newer)], 'page', NOW.isoformat())
    h.bot.handle(callback(h, h.bot.sign(f'a:{old.id}')))
    assert rows(h)[('CAR1', 'deviation')]['since'] == newer and rows(h)[('CAR1', 'deviation')]['user'] == 'page'
    assert la.map_newer_case('deviation', at(58), dev['from'], st.LIVE_ACK_SINCE_TOL) is False   # тот же случай ±3
    assert la.map_newer_case('gps', at(30), at(40), st.LIVE_ACK_SINCE_TOL) is True
    assert la.map_newer_case('gps', None, at(40), st.LIVE_ACK_SINCE_TOL) is False


def test_review2_broken_record_is_logged_once(tmp_path, caplog, monkeypatch):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    h.store.live_ack_put(DAY, [('CAR1', 'gps', at(1))], 'd', NOW.isoformat())
    monkeypatch.setattr(la, 'map_ack_match', lambda *a, **kw: (_ for _ in ()).throw(KeyError('x')))
    h.tick()
    h.tick()
    assert caplog.text.count('не сверена') == 1


# ============================== ревью 3 (gate) ==============================

def test_review3_refresh_only_keys_written_by_the_bot(tmp_path):
    morning = (NOW - timedelta(hours=2)).isoformat()
    h = Harness(tmp_path, {'CAR1': card(late('c1', 40, 'plan'))}, NOW)
    h.store.live_ack_put(DAY, [('CAR1', 'late:window', None)], 'morning', morning)   # утренняя чужая отметка
    h.tick()
    h.bot.handle(callback(h, h.bot.sign(f'a:{h.rec("late:").id}')))
    assert h.rec('late:').payload['map_keys'] == ['late:plan']
    t = NOW + timedelta(minutes=6)
    h.set(now=t, cards={'CAR1': card(late('c1', 40, 'plan', now=t))})
    h.tick()
    got = rows(h)
    assert got[('CAR1', 'late:plan')]['seen_at'] == t.isoformat()
    assert got[('CAR1', 'late:window')]['seen_at'] == morning                # не ожила: ухудшение до окна — снова мигает


def test_review3_refresh_at_most_every_five_minutes(tmp_path, monkeypatch):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, 'window'))}, NOW)
    h.tick()
    h.bot.handle(callback(h, h.bot.sign(f'a:{h.rec("late:").id}')))
    calls = []
    real = h.store.live_ack_put

    def counting(day, items, user, at, refresh=False):
        calls.append(refresh)
        return real(day, items, user, at, refresh)
    monkeypatch.setattr(h.store, 'live_ack_put', counting)
    for minutes in (1, 2, 3, 4, 5, 6, 7):
        t = NOW + timedelta(minutes=minutes)
        h.set(now=t, cards={'CAR1': card(late('c1', 20, 'window', now=t))})
        h.tick()
    assert calls == [True]                                                   # один раз — на 5-й минуте


def test_review3_map_items_failure_is_logged_and_bad_page_rows_do_not_cancel_escalation(tmp_path, caplog, monkeypatch):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    monkeypatch.setattr(la, 'map_ack_items', lambda r: (_ for _ in ()).throw(ValueError('x')))
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    assert h.rec('alert:').acked_by == 7 and 'не передана карте' in caplog.text
    # битая строка карты при эскалации — «нет отметки», эскалация уходит (а не пропадает в _rejected)
    h2 = Harness(tmp_path, gps_card(), NOW, db='b.db')
    h2.tick()
    monkeypatch.setattr(la, 'map_ack_match', lambda *a, **kw: (_ for _ in ()).throw(TypeError('bad row')))
    h2.set(now=NOW + timedelta(minutes=10))
    h2.tick()
    assert [t for t in h2.api.texts() if t.startswith('❗')] and h2.rec('alert:').escalated_at is not None


def test_keys_match_the_page_on_real_live_output():
    """Настоящий вывод live.car_view: ключ страницы — вид, случай — alerts.since[вид]; ключ бота и отметка карты из
    него — то же (машина, вид, since)."""
    from test_route_live import DEPOT, T0, Track, facts, stop, A, TRUCK, ROAD
    tr = Track().park(DEPOT, 5).drive(A).park(A, 2)
    stops = [stop('S:A', 1, A, 100.0, seq=1)]
    last = T0 + timedelta(minutes=5)
    dev = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}
    now = last + timedelta(minutes=12)
    f = facts(tr.pts, stops, [T0, last], dev, last_contact=last)
    c = {**live.car_view(T0.date(), now, f, [live.PlanTrip((1,), {})], TRUCK, DEPOT, live.Rules(), ROAD, False),
         'car_code': 'CAR1'}
    tg = la.TgRules(sim=True)
    send = next(a for a in la.plan({'CAR1': c}, live.Rules(), tg, now, {}).actions if isinstance(a, la.Send))
    rec = la.Rec(send.key, send.kind, 'active', now.isoformat(), car='CAR1')
    day, items = la.map_ack_items(rec)
    assert day == T0.date().isoformat() and items == [('CAR1', 'no_contact', c['alerts']['since']['no_contact'])]
    page_row = {'car': 'CAR1', 'key': 'no_contact', 'since': c['alerts']['since']['no_contact'], 'user': 'd',
                'at': now.isoformat(), 'seen_at': now.isoformat()}
    assert la.map_ack_match(rec, [page_row], st.LIVE_ACK_SINCE_TOL) == page_row
