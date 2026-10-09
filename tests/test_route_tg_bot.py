# -*- coding: utf-8 -*-
"""Telegram-бот «Araqich Dispatch» (№91; route_optimizer.tg_bot, tg_api, tg_reports): транспорт Bot API (JSON, ошибки
без токена, 429, частота), «Տեսա» (правка, подпись кнопок, доступ), эскалация (ответ в группе + личные копии, 403),
«не успеет» (одно сообщение на машину в день: правка, ответ со звуком при ухудшении, ✅), темы форума и общий чат,
группа → супергруппа, отчёты (план — отправка, правка, закрепление; неделя; итог дня), команды /where /today /late /help,
getUpdates (offset), схема 27.

Telegram не вызывается: Bot API — подделка (tests/tg_fake.py), HTTP — подделка opener.
Запуск:  python -m pytest tests/test_route_tg_bot.py -q
"""
import io
import json
import sqlite3
import sys
import urllib.error
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import tg_api, tg_bot, tg_reports as rp  # noqa: E402
from tg_fake import BOT_ID, BOT_NAME, SECRET, FakeTelegram, Harness, err  # noqa: E402

Y = ac.YEREVAN
NOW = datetime(2026, 10, 6, 11, 0, tzinfo=Y)   # вторник
OWNER = 838786551


def at(minutes_ago, now=NOW):
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')


def alert(kind, start_ago, end_ago=None, now=NOW, **extra):
    return {'kind': kind, 'from': at(start_ago, now), 'to': at(end_ago, now) if end_ago is not None else None,
            'active': end_ago is None, **extra}


def late(target, over, kind='window', now=NOW, name=None):
    return {'kind': 'late', 'from': now.isoformat(), 'to': None, 'active': True, 'target': target, 'late_kind': kind,
            'stop_id': target, 'name': name or f'Խանութ {target}', 'over_min': over,
            'eta': (now + timedelta(minutes=30)).isoformat(), 'limit': (now + timedelta(minutes=30 - over)).isoformat()}


def card(*alerts, car='CAR1', **extra):
    return {'car_code': car, 'name': 'JAC', 'driver': 'Արամ', 'drivers': ['Արամ'],
            'position': {'lat': 40.1812, 'lon': 44.5133, 'speed_kmh': 42}, 'alerts_log': list(alerts), **extra}


def gps_card(start_ago=1, **extra):
    return {'CAR1': card(alert('gps', start_ago, gps='off'), **extra)}


def callback(h, data, uid=7, first='Գոռ', chat=None, mid=None, cq='cq1'):
    chat = chat or {'id': -100, 'type': 'supergroup'}
    return {'update_id': 1, 'callback_query': {'id': cq, 'from': {'id': uid, 'first_name': first}, 'data': data,
                                               'message': {'message_id': mid or 1, 'chat': chat}}}


def command(text, uid=7, chat=None, thread=None):
    chat = chat or {'id': -100, 'type': 'supergroup'}
    msg = {'message_id': 50, 'from': {'id': uid, 'first_name': 'Գոռ'}, 'chat': chat, 'text': text}
    if thread is not None:
        msg.update(message_thread_id=thread, is_topic_message=True)
    return {'update_id': 2, 'message': msg}


# ============================== транспорт ==============================

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def test_bot_api_posts_json_and_errors_never_contain_token():
    calls = []

    def ok(req, timeout):
        calls.append((req.full_url, json.loads(req.data.decode()), req.get_header('Content-type'), timeout))
        return Resp(b'{"ok": true, "result": {"message_id": 5}}')
    clock = Clock()
    api = tg_api.BotApi(SECRET, opener=ok, limiter=tg_api.RateLimiter(clock, clock.sleep))
    assert api('sendMessage', chat_id='-100', text='Բարև', reply_markup={'inline_keyboard': []}, message_thread_id=None) \
        == {'message_id': 5}
    url, body, ctype, timeout = calls[0]
    assert url == f'https://api.telegram.org/bot{SECRET}/sendMessage' and ctype == 'application/json'
    assert body == {'chat_id': '-100', 'text': 'Բարև', 'reply_markup': {'inline_keyboard': []}}   # None — не шлётся
    assert timeout == tg_api.HTTP_TIMEOUT_S and 'SECRET' not in repr(api)
    api('getUpdates', offset=3, timeout=25)
    assert calls[-1][3] == tg_api.HTTP_TIMEOUT_S + 25                                       # long poll + запас

    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(
            b'{"ok": false, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup '
            b'chat", "parameters": {"migrate_to_chat_id": -1009}}'))

    def net_error(req, timeout):
        raise urllib.error.URLError('no route to ' + req.full_url)

    def not_ok(req, timeout):
        return Resp(b'{"ok": false, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}')

    def garbage(req, timeout):
        return Resp(b'<html>')
    errors = []
    for opener in (http_error, net_error, not_ok, garbage):
        with pytest.raises(tg_api.TelegramError) as e:
            tg_api.BotApi(SECRET, opener=opener)('sendMessage', chat_id=1, text='x')
        assert 'SECRET' not in str(e.value) and SECRET not in repr(e.value.args)
        errors.append(e.value)
    assert (errors[0].status, errors[0].migrate_to) == (400, -1009) and 'upgraded' in errors[0].description
    assert errors[2].status == 403 and errors[1].status is None


def test_bot_api_429_waits_retry_after_then_retries_once_and_long_pause_fails():
    answers = [urllib.error.HTTPError('u', 429, 'Too Many', {}, io.BytesIO(
        b'{"ok": false, "error_code": 429, "description": "Too Many Requests: retry after 7", '
        b'"parameters": {"retry_after": 7}}')), Resp(b'{"ok": true, "result": true}')]

    def opener(req, timeout):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    clock = Clock()
    api = tg_api.BotApi(SECRET, opener=opener, limiter=tg_api.RateLimiter(clock, clock.sleep))
    assert api('sendMessage', chat_id='-100', text='x') is True
    assert clock.slept == [7.0]                                         # подождали retry_after и повторили
    long = [urllib.error.HTTPError('u', 429, 'Too Many', {}, io.BytesIO(
        b'{"ok": false, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 600}}'))]

    def opener2(req, timeout):
        raise long[0]
    with pytest.raises(tg_api.TelegramError) as e:
        tg_api.BotApi(SECRET, opener=opener2, limiter=tg_api.RateLimiter(clock, clock.sleep))('sendMessage',
                                                                                                chat_id='-1', text='x')
    assert e.value.status == 429 and e.value.retry_after == 600         # дольше RETRY_429_MAX_S — сбой, повтор позже


def test_rate_limiter_one_per_second_per_chat_and_twenty_per_minute_per_group():
    clock = Clock()
    lim = tg_api.RateLimiter(clock, clock.sleep)
    for _ in range(3):
        lim.wait('42')                                                  # личный чат: раз в секунду
    assert clock.slept == [1.0, 1.0]
    clock, waits = Clock(), []
    lim = tg_api.RateLimiter(clock, clock.sleep)
    for _ in range(21):
        before = clock.t
        lim.wait('-100')
        waits.append(clock.t - before)
    assert clock.t == pytest.approx(60.0)                               # 21-е — через минуту после первого
    assert waits[1:20] == [1.0] * 19
    lim.pause(30)
    before = clock.t
    lim.wait('77')
    assert clock.t - before == pytest.approx(30)                        # 429 — пауза для всех чатов


# ============================== «Տեսա» ==============================

def test_ack_edits_message_with_name_time_and_removes_ack_button(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:CAR1|gps')
    data = h.api.messages[('-100', rec.message_id)]['reply_markup']['inline_keyboard'][0][0]['callback_data']
    h.set(now=NOW + timedelta(minutes=2))
    h.bot.handle(callback(h, data, mid=rec.message_id))
    assert h.api.of('answerCallbackQuery')[-1] == {'callback_query_id': 'cq1', 'text': tg_bot.ACKED}
    text = h.api.text('-100', rec.message_id)
    assert text.endswith('✔ Տեսավ Գոռ · 11:02') and h.api.buttons('-100', rec.message_id) == [la.MAP_TEXT, la.WHERE_TEXT]
    assert (h.rec('alert:CAR1|gps').acked_by, h.rec('alert:CAR1|gps').acked_name) == (7, 'Գոռ')
    # второй раз — «уже», без правки; после перезапуска — то же (записано в базу)
    edits = len(h.api.of('editMessageText'))
    h.restart().handle(callback(h, data, uid=8, first='Աննա', cq='cq2'))
    assert h.api.of('answerCallbackQuery')[-1]['text'] == 'Արդեն նշված է՝ Գոռ' and len(h.api.of('editMessageText')) == edits
    # тревога кончилась — ✅ и строка «Տեսավ» остаются
    h.set(now=NOW + timedelta(minutes=5), cards={'CAR1': card({**gps_card()['CAR1']['alerts_log'][0], 'active': False,
                                                               'to': at(-4)})})
    h.tick()
    text = h.api.text('-100', rec.message_id)
    assert text.startswith('✅ <b>Վերջացավ</b>') and '✔ Տեսավ Գոռ · 11:02' in text


def test_forged_old_or_foreign_callbacks_are_ignored(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:CAR1|gps')
    good = h.bot.sign(f'a:{rec.id}')
    for data in (f'a:{rec.id}|AAAAAAAA', f'a:{rec.id}', 'garbage', None, good.replace(f'a:{rec.id}', f'a:{rec.id + 1}'),
                 tg_bot.TgBot(FakeTelegram(), '-100', h.store, h.feeds, 'other-token').sign(f'a:{rec.id}')):
        h.bot.handle(callback(h, data))
    assert h.api.of('editMessageText') == [] and rec.acked_by is None
    assert all('text' not in p for p in h.api.of('answerCallbackQuery'))      # подделка — пустой ответ
    # подписано, но из чужой группы — молчание; из лички — только участнику группы
    h.bot.handle(callback(h, good, chat={'id': -555, 'type': 'supergroup'}))
    h.bot.handle(callback(h, good, uid=31, chat={'id': 31, 'type': 'private'}))
    assert h.bot.records[rec.key].acked_by is None
    h.api.members[32] = 'member'
    h.bot.handle(callback(h, good, uid=32, first='Վարդան', chat={'id': 32, 'type': 'private'}))
    assert h.bot.records[rec.key].acked_name == 'Վարդան'
    # запись старше сообщения (нет записи) — «Հնացած է»
    h.bot.handle(callback(h, h.bot.sign('a:9999')))
    assert h.api.of('answerCallbackQuery')[-1]['text'] == 'Հնացած է'


def test_where_button_sends_location_and_card_into_the_same_topic(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW, api=FakeTelegram(forum=True))
    h.tick()
    rec = h.rec('alert:CAR1|gps')
    data = h.api.messages[('-100', rec.message_id)]['reply_markup']['inline_keyboard'][0][-1]['callback_data']
    assert data.startswith('w:CAR1|')
    upd = callback(h, data, mid=rec.message_id)
    upd['callback_query']['message'].update(message_thread_id=rec.thread_id, is_topic_message=True)
    h.bot.handle(upd)
    loc = h.api.of('sendLocation')[-1]
    assert (loc['latitude'], loc['longitude'], loc['message_thread_id']) == (40.1812, 44.5133, rec.thread_id)
    assert h.api.sent()[-1]['text'].startswith('📍 <b>CAR1</b> · JAC — Արամ')


# ============================== эскалация ==============================

def test_unacked_critical_escalates_once_reply_with_mention_and_private_copy(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:CAR1|gps')
    h.set(now=NOW + timedelta(minutes=9))
    assert h.tick() == 0                                                  # 9 мин — рано
    h.set(now=NOW + timedelta(minutes=10))
    assert h.tick() == 2
    reply, private = h.api.sent()[-2:]
    assert reply['chat_id'] == '-100' and reply['reply_parameters']['message_id'] == rec.message_id
    assert reply['text'].startswith('❗ <b>10 րոպե առանց պատասխանի</b>') and 'disable_notification' not in reply
    assert f'<a href="tg://user?id={OWNER}">ղեկավար</a>' in reply['text']
    assert private['chat_id'] == str(OWNER) and private['text'].endswith('❗ 10 րոպե առանց պատասխանի')
    assert [b['text'] for b in private['reply_markup']['inline_keyboard'][0]] == [la.ACK_TEXT, la.MAP_TEXT, la.WHERE_TEXT]
    h.set(now=NOW + timedelta(minutes=30))
    assert h.tick() == 0                                                   # один раз
    # «Տեսա» из лички (владелец — участник группы) правит и группу, и свою копию
    h.api.members[OWNER] = 'creator'
    data = private['reply_markup']['inline_keyboard'][0][0]['callback_data']
    h.bot.handle(callback(h, data, uid=OWNER, first='Շահեն', chat={'id': OWNER, 'type': 'private'}))
    assert h.api.text('-100', rec.message_id).endswith('✔ Տեսավ Շահեն · 11:30')
    assert h.api.text(str(OWNER), h.rec('alert:CAR1|gps').payload['copies'][0][1]).endswith('✔ Տեսավ Շահեն · 11:30')


def test_no_escalation_when_acked_ended_warning_quiet_or_off(tmp_path):
    later = NOW + timedelta(minutes=15)
    # подтверждена
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:CAR1|gps')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    h.set(now=later)
    assert h.tick() == 0
    # 🟠 скорость — без эскалации; эскалация выключена (0 мин)
    h2 = Harness(tmp_path, {'CAR1': card(alert('speed', 1, max_kmh=110, lat=1.0, lon=1.0))}, NOW, db='b.db')
    h2.tick()
    h2.set(now=later)
    assert h2.tick() == 0
    h3 = Harness(tmp_path, gps_card(), NOW, settings={'tg_escalate_min': 0}, db='c.db')
    h3.tick()
    h3.set(now=later)
    assert h3.tick() == 0
    # тревога уже не идёт (пропала из журнала) — не поднимаем
    h4 = Harness(tmp_path, gps_card(), NOW, db='d.db')
    h4.tick()
    h4.set(now=later, cards={'CAR1': card()})
    assert h4.tick() == 0
    # тихие часы
    evening = datetime(2026, 10, 6, 19, 55, tzinfo=Y)
    h5 = Harness(tmp_path, {'CAR1': card(alert('gps', 1, now=evening, gps='off'))}, evening, db='e.db')
    h5.tick()
    h5.set(now=evening + timedelta(minutes=15))
    assert h5.tick() == 0


def test_escalation_private_403_is_logged_and_does_not_stop(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW, settings={'tg_escalate_to': [OWNER, 5]})
    h.bot.names[str(OWNER)] = 'Շահեն'
    h.tick()
    h.api.fail['sendMessage'] = [None, err(403, "Forbidden: bot can't initiate conversation with a user"), None]
    h.set(now=NOW + timedelta(minutes=11))
    assert h.tick() == 2                                                   # ответ в группе + копия второму
    reply = h.api.sent('-100')[-1]['text']
    assert '<a href="tg://user?id=838786551">Շահեն</a>' in reply and '<a href="tg://user?id=5">ղեկավար</a>' in reply
    assert '/start' in caplog.text and h.bot.client_errors == 0 and h.bot.halted is None
    assert h.rec('alert:CAR1|gps').escalated_at is not None and len(h.rec('alert:CAR1|gps').payload['copies']) == 1


# ============================== «не успеет» ==============================

def test_late_one_message_per_car_day_edit_reply_on_worsening_and_check_when_cleared(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20))}, NOW)
    assert h.tick() == 1
    msg = h.api.sent()[0]
    rec = h.rec('late:CAR1|2026-10-06')
    assert msg['text'].startswith('🔴 <b>Չի հասցնում ժամանակին (կանխատեսում)</b>') and 'Խանութ c1 — կուշանա պատուհանից 20' \
        in msg['text']
    assert h.bot._thread('late') is None and rec.level == 'critical'
    # прогноз чуть хуже (меньше шага) — тише: правка не раньше LATE_EDIT_MIN, без ответа
    h.set(now=NOW + timedelta(minutes=2), cards={'CAR1': card(late('c1', 25, now=NOW + timedelta(minutes=2)))})
    assert h.tick() == 0 and h.api.of('editMessageText') == []
    h.set(now=NOW + timedelta(minutes=6), cards={'CAR1': card(late('c1', 26, now=NOW + timedelta(minutes=6)))})
    assert h.tick() == 1 and len(h.api.sent()) == 1
    assert '26 րոպեով' in h.api.text('-100', rec.message_id) and '🔄 Թարմացված՝ 11:06' in h.api.text('-100', rec.message_id)
    # ухудшилось на шаг (30 мин = repeat_min) и новая строка (план без окна, 🟠) — правка + ответ со звуком
    t = NOW + timedelta(minutes=8)
    h.set(now=t, cards={'CAR1': card(late('c1', 50, now=t), late('c2', 40, 'plan', now=t))})
    assert h.tick() == 2
    reply = h.api.sent()[-1]
    assert reply['reply_parameters']['message_id'] == rec.message_id and 'ուշացումը մեծացավ' in reply['text']
    assert 'Խանութ c1' in reply['text'] and 'Խանութ c2 — կուշանա պլանից 40' in reply['text']
    # перестали опаздывать: строки снимаются через LATE_CLEAR_MIN, потом ✅
    t = NOW + timedelta(minutes=10)
    h.set(now=t, cards={'CAR1': card()})
    h.tick()
    assert h.rec('late:CAR1|').phase == 'active'
    h.set(now=t + timedelta(minutes=16))
    h.tick()
    assert h.rec('late:CAR1|').phase == 'ended' and h.api.text('-100', rec.message_id).startswith('✅ <b>Վերջացավ</b> · տևեց 26')
    # снова опаздывает — новый случай в том же сообщении, со звуком (ответ)
    t = NOW + timedelta(minutes=40)
    h.set(now=t, cards={'CAR1': card(late('c1', 15, now=t))})
    sent = len(h.api.sent())
    assert h.tick() == 2 and len(h.api.sent()) == sent + 1
    assert not h.api.text('-100', rec.message_id).startswith('✅') and h.rec('late:CAR1|').phase == 'active'
    assert sorted(h.rec('late:CAR1|').payload['seen']) == ['c1', 'c2']


def test_late_becoming_critical_escalates_ten_minutes_after_that_not_after_first_message(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 40, 'plan'))}, NOW)
    h.tick()
    assert h.rec('late:').level == 'warning'
    t = NOW + timedelta(minutes=30)                                        # 🟠 полчаса, потом строка окна — 🔴
    h.set(now=t, cards={'CAR1': card(late('c1', 40, 'plan', now=t), late('c2', 10, now=t))})
    h.tick()
    assert h.rec('late:').level == 'critical' and h.rec('late:').escalated_at is None
    h.set(now=t + timedelta(minutes=9))
    h.tick()
    assert h.rec('late:').escalated_at is None
    h.set(now=t + timedelta(minutes=10))
    h.tick()
    assert h.rec('late:').escalated_at is not None and h.api.sent('-100')[-1]['text'].startswith('❗')


def test_long_texts_fit_telegram_limit():
    lines = [f'<b>{i}</b> ' + 'Ա' * 60 for i in range(200)]
    text = tg_bot.fit('\n'.join(lines))
    assert len(text) <= tg_bot.TEXT_MAX and text.endswith('\n…') and text.count('<b>') == text.count('</b>')
    assert tg_bot.fit('կարճ') == 'կարճ'
    body = la.late_body(card(), [f'line {i}' for i in range(20)], 'critical', NOW)
    assert 'line 14' in body and 'line 15' not in body and '… և ևս 5 խանութ' in body


def test_late_in_quiet_hours_is_not_decided(tmp_path):
    night = datetime(2026, 10, 6, 21, 0, tzinfo=Y)
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20, now=night))}, night)
    assert h.tick() == 0 and h.bot.records == {}


# ============================== темы ==============================

def test_forum_with_rights_creates_topics_once_and_routes_by_kind(tmp_path):
    api = FakeTelegram(forum=True)
    cards = {'CAR1': card(alert('gps', 1, gps='off'), alert('speed', 1, max_kmh=100, lat=1.0, lon=1.0), late('c1', 20))}
    h = Harness(tmp_path, cards, NOW, api=api)
    h.tick()
    names = [p['name'] for p in api.of('createForumTopic')]
    assert names == ['🔴 Կրիտիկական', '⏰ Ուշացումներ', '🚚 Խախտումներ', '📊 Օրվա ամփոփում']
    topics = h.store.tg_kv('topics')['ids']
    by_text = {p['text'].split('\n')[0]: p.get('message_thread_id') for p in api.sent()}
    assert by_text['🔴 <b>GPS-ն անջատված է</b>'] == topics['critical']
    assert by_text['🟠 <b>Արագության գերազանցում</b>'] == topics['violations']
    assert by_text['🔴 <b>Չի հասցնում ժամանակին (կանխատեսում)</b>'] == topics['late']
    h.restart().tick()
    assert len(api.of('createForumTopic')) == 4                            # после перезапуска — из tg_kv
    assert [c['commands'] for c in api.of('setMyCommands')][0][0] == tg_bot.COMMANDS[0]


def test_not_forum_or_no_rights_everything_in_main_chat(tmp_path):
    for api in (FakeTelegram(forum=False), FakeTelegram(forum=True, rights=False)):
        h = Harness(tmp_path, gps_card(), NOW, api=api, db=f'{id(api)}.db')
        h.tick()
        assert api.of('createForumTopic') == [] and 'message_thread_id' not in api.sent()[0]


def test_deleted_topic_falls_back_to_main_chat_and_topics_are_recreated(tmp_path):
    api = FakeTelegram(forum=True)
    h = Harness(tmp_path, gps_card(), NOW, api=api)
    h.tick()
    before = dict(h.bot.topics)
    api.threads.discard(before['violations'])                              # тему «Խախտումներ» удалили в группе
    h.set(cards={'CAR1': card(alert('gps', 1, gps='off'), alert('center', 1, lat=1.0, lon=1.0))})
    assert h.tick() == 1
    assert 'message_thread_id' not in api.sent()[-1] and 'Փոքր կենտրոնում' in api.sent()[-1]['text']
    # ревью: сброшена только удалённая тема, прочие — как были (в базе — тоже)
    assert h.bot.topics == {k: v for k, v in before.items() if k != 'violations'}
    assert h.store.tg_kv('topics')['ids'] == h.bot.topics
    h.clock[0] += tg_bot.SETUP_RETRY_S + 1
    h.tick()
    assert [p['name'] for p in api.of('createForumTopic')][4:] == ['🚚 Խախտումներ']
    assert h.bot.topics['critical'] == before['critical'] and h.bot.topics['violations'] != before['violations']


def test_topics_are_saved_one_by_one_when_creation_fails_midway(tmp_path):
    api = FakeTelegram(forum=True)
    api.fail['createForumTopic'] = [None, None, err(500, 'Internal Server Error')]
    h = Harness(tmp_path, {}, NOW, api=api)
    h.tick()
    assert list(h.store.tg_kv('topics')['ids']) == ['critical', 'late']        # созданные две — в базе
    h.restart()
    h.clock[0] += tg_bot.SETUP_RETRY_S + 1
    h.tick()
    assert [p['name'] for p in api.of('createForumTopic')] == [n for _, n in tg_bot.TOPICS][:3] + \
        [n for _, n in tg_bot.TOPICS][2:]                                    # первые две второй раз не создаются


def test_group_upgraded_to_supergroup_writes_to_new_id(tmp_path, caplog):
    api = FakeTelegram()
    h = Harness(tmp_path, gps_card(), NOW, api=api)
    api.fail['sendMessage'] = [err(400, 'Bad Request: group chat was upgraded to a supergroup chat', migrate_to=-1009)]
    assert h.tick() == 1
    assert api.sent()[-1]['chat_id'] == '-1009' and h.rec('alert:CAR1|gps').chat == '-1009'
    assert 'ROUTES_LIVE_TG_CHAT' in caplog.text
    assert h.restart().chat == '-1009'                                    # запомнено


# ============================== отчёты ==============================

STORED_PLAN = {
    'trucks': ['CAR1', 'CAR2'],
    'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [501, 502]}, {'id': 2, 'truck': 'CAR1', 'stops': [503]},
              {'id': 3, 'truck': 'CAR2', 'stops': [504]}],
    'released': {'at': '2026-10-05T18:10:00+04:00', 'by': 'logist'},
    'prediction': {'trucks': {'CAR1': {'km': 86.4, 'kg': 2450, 'depart': '09:30'},
                              'CAR2': {'km': 20.0, 'kg': 600, 'depart': '10:00'}}},
}


def plan_of(stored=None, day=date(2026, 10, 6)):
    return rp.day_plan(stored or STORED_PLAN, day, {'CAR1': 'Արամ', 'CAR2': 'Վարդան'}, {'CAR1': 'Գոռ'},
                       {502: (-float('inf'), 600.0), 503: (540.0, 720.0), 504: (600.0, float('inf'))},
                       {'CAR1': 'JAC', 'CAR2': None})


def test_day_plan_from_released_draft_and_text():
    assert rp.day_plan({**STORED_PLAN, 'released': None}, date(2026, 10, 6), {}, {}, {}, {}) is None
    assert rp.day_plan(None, date(2026, 10, 6), {}, {}, {}, {}) is None
    plan = plan_of()
    assert plan['cars'][0] == {'car': 'CAR1', 'name': 'JAC', 'driver': 'Արամ', 'helper': 'Գոռ', 'trips': 2, 'stops': 3,
                               'kg': 2450, 'km': 86.4, 'depart': '09:30', 'first_window': '10:00'}
    assert plan['cars'][1]['first_window'] is None
    text = rp.plan_text(plan)
    assert text.splitlines() == [
        '📋 <b>Օրվա պլան · 06.10.2026</b>',
        '🚚 <b>CAR1</b> · JAC — Արամ + Գոռ', '   2 երթ · 3 կետ · 2,5 տ · 86 կմ · մեկնում՝ 09:30 · ⏰ մինչև 10:00',
        '🚚 <b>CAR2</b> — Վարդան', '   1 երթ · 1 կետ · 0,6 տ · 20 կմ · մեկնում՝ 10:00',
        '<b>Ընդամենը՝</b> 2 մեքենա · 3 երթ · 4 կետ · 3,0 տ · 106 կմ']
    # водителям отправлен снимок (№81): в отчёте — он, а не правки черновика после отправки
    sent = {**STORED_PLAN, 'trips': STORED_PLAN['trips'] + [{'id': 4, 'truck': 'CAR2', 'stops': [505]}],
            'sent': {'at': '2026-10-05T18:10:00+04:00', 'by': 'logist', 'plan': STORED_PLAN}}
    assert plan_of(sent)['cars'][1]['stops'] == 1


def test_plan_report_sent_on_release_pinned_and_edited_in_place_on_change(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    assert h.tick() == 0                                                   # плана нет — ничего
    h.set(plan=plan_of())
    assert h.tick() == 1
    msg = h.api.sent()[-1]
    rec = h.rec('plan:2026-10-06')
    assert msg['text'].startswith('📋 <b>Օրվա պլան · 06.10.2026</b>') and 'reply_markup' not in msg
    assert h.api.of('pinChatMessage')[-1] == {'chat_id': '-100', 'message_id': rec.message_id, 'disable_notification': True}
    assert h.tick() == 0                                                   # тот же план — без правок
    changed = {**STORED_PLAN, 'trips': STORED_PLAN['trips'] + [{'id': 4, 'truck': 'CAR2', 'stops': [505]}]}
    h.set(plan=plan_of(changed), now=NOW + timedelta(minutes=12))
    assert h.tick() == 1 and len(h.api.sent()) == 1
    text = h.api.text('-100', rec.message_id)
    assert '2 երթ · 2 կետ' in text and text.endswith('🔄 Թարմացված՝ 11:12')


def test_reports_wait_for_quiet_hours_end_and_skip_holidays(tmp_path):
    early = datetime(2026, 10, 6, 7, 30, tzinfo=Y)
    h = Harness(tmp_path, {}, early)
    h.set(plan=plan_of())
    assert h.tick() == 0
    h.set(now=datetime(2026, 10, 6, 8, 0, tzinfo=Y))
    assert h.tick() == 1
    sunday = datetime(2026, 10, 11, 11, 0, tzinfo=Y)                        # workdays 1–6
    h2 = Harness(tmp_path, {}, sunday, db='b.db')
    h2.set(plan=plan_of(day=sunday.date()))
    assert h2.tick() == 0
    h3 = Harness(tmp_path, {}, NOW, settings={'holidays': ['2026-10-06']}, db='c.db')
    h3.set(plan=plan_of())
    assert h3.tick() == 0
    h4 = Harness(tmp_path, {}, NOW, settings={'tg_report_plan': False, 'tg_report_summary': False,
                                              'tg_report_week': False}, db='d.db')
    h4.set(plan=plan_of())
    assert h4.tick() == 0 and h4.api.sent() == []


SCORE_ROWS = [
    {'role': 'driver', 'id': 1, 'name': 'Արամ', 'days': 5, 'stops': 90, 'score': 91.0, 'rank': 1,
     'parts': {'on_time': {'score': 90.0, 'weight': 30}, 'speed': {'score': 60.0, 'weight': 15},
               'stops': {'score': 100.0, 'weight': 15}}},
    {'role': 'driver', 'id': 2, 'name': 'Վարդան', 'days': 4, 'stops': 70, 'score': 75.5, 'rank': 2,
     'parts': {'on_time': {'score': 50.0, 'weight': 30}, 'speed': {'score': 80.0, 'weight': 15},
               'route': {'score': 40.0, 'weight': 15}}},
    {'role': 'driver', 'id': 3, 'name': 'Նոր', 'days': 1, 'stops': 8, 'score': None, 'rank': None, 'parts': {}},
    {'role': 'helper', 'id': 4, 'name': 'Գոռ', 'days': 5, 'stops': 90, 'score': 88.0, 'rank': 1, 'parts': {}},
]


def test_week_ranking_goes_with_first_plan_of_the_week(tmp_path):
    monday = datetime(2026, 10, 5, 9, 0, tzinfo=Y)
    h = Harness(tmp_path, {}, monday)
    h.set(plan=plan_of(day=monday.date()), scores=SCORE_ROWS)
    assert h.tick() == 2
    week = h.api.texts()[-1]
    assert week.splitlines()[:4] == ['🏆 <b>Շաբաթվա վարկանիշ · 28.09–04.10</b>', '🥇 Արամ — 91 (5 օր, 90 կետ)',
                                     '🥈 Վարդան — 76 (4 օր, 70 կետ)', 'Քիչ տվյալ՝ Նոր']
    assert week.splitlines()[4] == '⚠️ Թույլ կողմեր՝ Երթուղի 40/100 · Ժամանակին 70/100 · Արագություն 70/100'
    # вторник той же недели — рейтинга больше нет; «Վարորդներ» не подключены — без рейтинга, без повторных попыток
    h.set(now=NOW, plan=plan_of())
    h.tick()
    assert sum(1 for t in h.api.texts() if t.startswith('🏆')) == 1
    h2 = Harness(tmp_path, {}, monday, db='b.db')
    h2.set(plan=plan_of(day=monday.date()), scores=None)
    assert h2.tick() == 1 and h2.rec('week:').phase == 'skipped'


def test_summary_when_all_planned_trucks_returned_or_at_limit_pinned_instead_of_plan(tmp_path):
    cars = {'CAR1': card(alert('speed', 120, 119, max_kmh=100, lat=1.0, lon=1.0),
                         alert('stop', 100, 80, minutes=20, lat=1.0, lon=1.0),
                         alert('deviation', 60, 50, km=2.0, minor=True, lat=1.0, lon=1.0),
                         planned=True, closed=False, km=86.44, fuel_l=21.3),
            'CAR2': card(car='CAR2', planned=True, closed=True, km=20.0, fuel_l=5.0, driver='Վարդան')}
    fleet = {'CAR1': {'stops': [{'stop_id': 'a', 'name': 'Ա', 'status': 'full'}, {'stop_id': 'b', 'name': 'Բ<', 'status': 'refused'},
                                {'stop_id': 'c', 'name': 'Գ', 'status': 'pending'}]},
             'CAR2': {'stops': [{'stop_id': 'd', 'name': 'Դ', 'status': 'partial'}]}}
    h = Harness(tmp_path, cars, NOW, settings={'live_alert_kinds': []})
    h.set(plan=plan_of(), fleet=fleet, scores=SCORE_ROWS)
    h.tick()
    assert not any(t.startswith('🏁') for t in h.api.texts())                # CAR1 ещё не вернулась
    # 19:30 — итог, даже если не все вернулись
    h.set(now=datetime(2026, 10, 6, 19, 30, tzinfo=Y))
    h.tick()
    text = h.api.texts()[-1]
    assert text.splitlines() == [
        '🏁 <b>Օրվա ամփոփում · 06.10.2026</b>',
        '🚚 <b>CAR1</b> · Արամ — ⚠️ 1/3 · դեռ չի վերադարձել', '   Չառաքված՝ Բ&lt;, Գ',
        '   86,4 կմ · 21,3 լ · ուշացում՝ 0 · խախտումներ՝ արագություն 1, կանգառ 1 · միավոր՝ 85',
        '🚚 <b>CAR2</b> · Վարդան — ✅ 1/1', '   20,0 կմ · 5,0 լ · ուշացում՝ 0 · միավոր՝ 55',
        '<b>Պարկ՝</b> 2 մեքենա · 2/4 կետ · 106,4 կմ · 26,3 լ · ուշացում՝ 0 · խախտումներ՝ 2']
    plan_mid, summary_mid = h.rec('plan:').message_id, h.rec('summary:').message_id
    assert h.api.of('unpinChatMessage')[-1]['message_id'] == plan_mid
    assert h.api.of('pinChatMessage')[-1]['message_id'] == summary_mid
    h.tick()
    assert sum(1 for t in h.api.texts() if t.startswith('🏁')) == 1          # один раз за день
    # все машины плана вернулись — итог сразу, не дожидаясь 19:30
    back = {k: {**v, 'closed': True} for k, v in cars.items()}
    h2 = Harness(tmp_path, back, datetime(2026, 10, 6, 16, 0, tzinfo=Y), settings={'live_alert_kinds': []}, db='b.db')
    h2.set(plan=plan_of(), fleet=fleet)
    h2.tick()
    assert h2.api.texts()[-1].startswith('🏁') and 'միավոր' not in h2.api.texts()[-1]


def test_summary_counts_late_stores_of_the_day(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20), late('c2', 40, 'plan'), planned=True, closed=True),
                           'CAR2': card(car='CAR2', planned=True, closed=True)}, NOW)
    h.set(plan=plan_of(), fleet={})
    h.tick()
    summary = [t for t in h.api.texts() if t.startswith('🏁')][0]
    assert 'ուշացում՝ 2' in summary


def test_pin_without_rights_is_logged_once_and_not_a_failure(tmp_path, caplog):
    h = Harness(tmp_path, {}, NOW)
    h.api.fail['pinChatMessage'] = [err(400, 'Bad Request: not enough rights to manage pinned messages in the chat')] * 3
    h.set(plan=plan_of())
    assert h.tick() == 1 and h.bot.failures == 0 and caplog.text.count('не закреплено') == 1


# ============================== команды и обновления ==============================

def test_commands_in_group_where_today_late_help(tmp_path):
    cards = {'CAR1': card(late('c1', 20), planned=True, state='moving', contact_age_s=180, last_contact=at(3),
                          stores={'done': 12, 'total': 20}, load={'remaining_kg': 1240},
                          next={'stop_id': 's', 'name': 'Սուպեր', 'here': False, 'eta': at(-20), 'delay_min': 12},
                          late=[late('c1', 20)]),
             'CAR2': card(car='CAR2', planned=True, state='offline', driver='Վարդան', stores={'done': 3, 'total': 9})}
    h = Harness(tmp_path, cards, NOW, settings={'live_alert_kinds': []})
    h.api.updates = [command('/where car1'), command('/where'), command('/today'), command('/late'),
                     command(f'/help@{BOT_NAME}'), command('/where@OtherBot CAR1'), command('hello')]
    assert h.bot.poll_once(0) == 7
    loc = h.api.of('sendLocation')
    assert len(loc) == 1 and (loc[0]['latitude'], loc[0]['longitude']) == (40.1812, 44.5133)
    where, pick, today, late_t, help_t = h.api.texts()
    assert where.splitlines() == ['📍 <b>CAR1</b> · JAC — Արամ', 'Վիճակ՝ ճանապարհին, 42 կմ/ժ',
                                  'Վերջին կապը՝ 3 րոպե առաջ (10:57)', 'Հաջորդ խանութը՝ Սուպեր — ≈ 11:20 (+12 րոպե պլանից)',
                                  'Բեռը՝ 1 240 կգ · կետեր՝ 12/20']
    assert pick == '🚚 Ո՞ր մեքենան'
    assert [b['text'] for b in h.api.sent()[1]['reply_markup']['inline_keyboard'][0]] == ['CAR1', 'CAR2']
    assert '🚚 <b>CAR1</b> Արամ — 12/20 · ⏰ 1 · 📶 3 րոպե առաջ' in today and '📵 կապ չկա' in today
    assert '<b>Ընդամենը՝</b> 15/29 կետ' in today
    assert 'Խանութ c1 — կուշանա պատուհանից 20' in late_t and help_t == rp.help_text(10)
    assert h.store.tg_kv('offset') == 3 and h.api.of('getUpdates')[0] == {'timeout': 0, 'allowed_updates':
                                                                          ['message', 'callback_query']}
    h.bot.poll_once(0)
    assert h.api.of('getUpdates')[1]['offset'] == 3 and len(h.api.of('deleteWebhook')) == 1
    assert h.restart().offset == 3                                          # offset переживает перезапуск


def test_where_unknown_car_and_late_nobody(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    h.bot.handle(command('/where XYZ'))
    h.bot.handle(command('/late'))
    h.bot.handle(command('/today'))
    assert h.api.texts() == ['Մեքենա չի գտնվել՝ «XYZ»։', '⏰ <b>Ով է ուշանում</b>\nԱյս պահին ուշացող չկա ✅',
                             '📊 <b>Այսօր · 06.10 11:00</b>\nԱյսօր մեքենաների տվյալներ դեռ չկան։']


def test_private_chat_only_for_group_members_cached_and_silence_for_strangers(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    stranger = {'id': 31, 'type': 'private'}
    h.bot.handle(command('/help', uid=31, chat=stranger))
    h.bot.handle(command('/help', uid=33, chat={'id': -777, 'type': 'group'}))   # чужая группа
    assert h.api.sent() == []                                                      # молчание
    h.api.members[32] = 'member'
    member = {'id': 32, 'type': 'private'}
    h.bot.handle(command('/help', uid=32, chat=member))
    h.bot.handle(command('/today', uid=32, chat=member))
    assert [p['chat_id'] for p in h.api.sent()] == ['32', '32']
    assert len([p for p in h.api.of('getChatMember') if p['user_id'] == 32]) == 1    # кэш
    h.api.members[32] = 'left'                                                     # вышел из группы
    h.clock[0] += tg_bot.ACCESS_TTL_S + 1
    h.bot.handle(command('/help', uid=32, chat=member))
    assert len(h.api.sent()) == 2


def test_command_in_topic_is_answered_in_that_topic(tmp_path):
    api = FakeTelegram(forum=True)
    h = Harness(tmp_path, {}, NOW, api=api)
    h.tick()
    thread = h.bot.topics['reports']
    h.bot.handle(command('/help', thread=thread))
    assert api.sent()[-1]['message_thread_id'] == thread


def test_poll_survives_bad_update_and_reply_failure(tmp_path, caplog):
    h = Harness(tmp_path, {}, NOW)
    h.api.updates = [{'update_id': 10, 'message': {'text': '/help', 'chat': 'not a dict'}},
                     command('/help'), {'update_id': 12}]
    h.api.updates[1]['update_id'] = 11
    h.api.fail['sendMessage'] = [err(400, 'Bad Request: chat not found')]
    assert h.bot.poll_once(0) == 3 and h.bot.offset == 13
    assert 'ответ не отправлен' in caplog.text or 'не обработано' in caplog.text


# ============================== база ==============================

def test_schema_27_migrates_from_26_and_keeps_data(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    s.save_truck_driver('CAR1', '2026-10-02', 'Արամ', 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE tg_message')
        conn.execute('DROP TABLE tg_kv')
        conn.execute("UPDATE meta SET value = '26' WHERE key = 'schema_version'")
        conn.commit()
    assert st.Store(path).truck_drivers('2026-10-02')[0] == {'CAR1': 'Արամ'}
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {'tg_message', 'tg_message_sent', 'tg_kv'} <= names and st.SCHEMA_VERSION >= 27   # 28 — «Տեսա» карты (live_ack)
    assert st._MIGRATIONS[26] == (st._TG_MESSAGE_TABLE, st._TG_MESSAGE_INDEX, st._TG_KV_TABLE)


def test_store_tg_methods_round_trip(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    assert s.tg_next_id() == 1 and s.tg_kv('x') is None
    rec = {'id': 5, 'key': 'alert:CAR1|gps|t', 'kind': 'gps', 'car': 'CAR1', 'level': 'critical', 'chat': '-100',
           'message_id': 77, 'thread_id': None, 'phase': 'active', 'sent_at': NOW.isoformat(), 'acked_by': None,
           'acked_name': None, 'acked_at': None, 'resolved_at': None, 'escalated_at': None, 'payload': {'body': 'Ա'}}
    s.tg_save_message(rec)
    s.tg_save_message({**rec, 'phase': 'ended', 'payload': {'body': 'Բ'}})
    assert s.tg_messages('') == [{**rec, 'phase': 'ended', 'payload': {'body': 'Բ'}}] and s.tg_next_id() == 6
    assert s.tg_messages((NOW + timedelta(seconds=1)).isoformat()) == []
    s.tg_set_kv('offset', 9)
    s.tg_set_kv('offset', 10)
    assert s.tg_kv('offset') == 10
    assert s.tg_prune((NOW + timedelta(seconds=1)).isoformat()) == 1 and s.tg_messages('') == []
    with pytest.raises(st.StoreError):
        s.tg_save_message({**rec, 'key': 'other', 'level': 'red'})        # CHECK уровня


# ============================== ревью 1 ==============================

def test_review_high1_rejected_message_is_logged_marked_and_pass_continues(tmp_path, caplog):
    """400 одного сообщения (тема закрыта, разметка, длина, кнопка) — в журнал, запись failed, остальные уходят в том же
    проходе, паузы и счёта до остановки нет; повтора нет. Ошибка всего чата (чат не найден) — считается."""
    cards = {'CAR1': card(alert('gps', 1, gps='off'), alert('center', 1, lat=1.0, lon=1.0))}
    h = Harness(tmp_path, cards, NOW)
    h.api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    assert h.tick() == 1 and h.bot.retry_at == 0.0 and h.bot.client_errors == 0 and h.bot.failures == 0
    assert h.rec('alert:CAR1|gps').phase == 'failed' and 'TOPIC_CLOSED' in h.rec('alert:CAR1|gps').payload['error']
    assert h.rec('alert:CAR1|center').phase == 'active' and 'отклонено Telegram' in caplog.text
    assert h.tick() == 0 and len(h.api.sent()) == 2 and len(h.api.messages) == 1   # отвергнутое не повторяется
    # много отвергнутых подряд — не остановка
    many = {f'C{i}': card(alert('gps', 1, gps='off'), car=f'C{i}') for i in range(la.CLIENT_ERRORS_MAX + 2)}
    h2 = Harness(tmp_path, many, NOW, db='b.db')
    h2.api.fail['sendMessage'] = [err(400, "Bad Request: can't parse entities")] * (la.CLIENT_ERRORS_MAX + 2)
    h2.tick()
    assert h2.bot.halted is None and all(r.phase == 'failed' for r in h2.bot.records.values())
    # чат не найден — ошибка всего чата: пауза и счёт
    h3 = Harness(tmp_path, gps_card(), NOW, db='c.db')
    h3.api.fail['sendMessage'] = [err(400, 'Bad Request: chat not found')]
    h3.tick()
    assert h3.bot.client_errors == 1 and h3.bot.retry_at > 0 and h3.bot.records == {}
    assert tg_api.chat_wide(err(403, 'Forbidden: bot was kicked')) and tg_api.chat_wide(err(401, 'Unauthorized'))
    assert tg_api.per_message(err(400, 'Bad Request: message is too long')) and not tg_api.per_message(err(429, 'x'))
    assert not tg_api.per_message(err(400, 'Bad Request: chat not found'))


def test_review_high1_rejected_late_escalation_and_report_do_not_loop(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20))}, NOW)
    h.api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    h.tick()
    rec = h.rec('late:')
    assert rec.phase == 'failed' and 'c1' in rec.payload['lines']
    assert h.tick() == 0 and len(h.api.sent()) == 1 and h.api.messages == {}   # тот же прогноз — без повторов
    t = NOW + timedelta(minutes=2)
    h.set(now=t, cards={'CAR1': card(late('c1', 60, now=t))})              # хуже на шаг — новое сообщение
    assert h.tick() == 1 and h.rec('late:').phase == 'active' and h.rec('late:').message_id
    # эскалация отвергнута — считается сделанной
    h2 = Harness(tmp_path, gps_card(), NOW, db='b.db')
    h2.tick()
    h2.set(now=NOW + timedelta(minutes=11))
    h2.api.fail['sendMessage'] = [err(400, 'Bad Request: message to be replied not found')]
    assert h2.tick() == 1 and h2.rec('alert:').escalated_at is not None   # в группе отвергнуто — лично всё равно
    assert h2.api.sent(str(OWNER)) and h2.tick() == 0
    # план отвергнут — до его изменения не повторяем
    h3 = Harness(tmp_path, {}, NOW, db='c.db')
    h3.set(plan=plan_of())
    h3.api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    h3.tick()
    h3.tick()
    assert h3.rec('plan:').phase == 'failed' and len(h3.api.of('sendMessage')) == 1
    changed = {**STORED_PLAN, 'trips': STORED_PLAN['trips'] + [{'id': 4, 'truck': 'CAR2', 'stops': [505]}]}
    h3.set(plan=plan_of(changed))
    assert h3.tick() == 1 and h3.rec('plan:').phase == 'report'


def test_review_medium3_db_failure_keeps_record_in_memory_and_is_logged(tmp_path, caplog, monkeypatch):
    h = Harness(tmp_path, gps_card(), NOW)

    def broken(rec):
        raise st.StoreError('disk I/O error')
    monkeypatch.setattr(h.store, 'tg_save_message', broken)
    assert h.tick() == 1 and 'не сохранена в базу' in caplog.text
    assert h.tick() == 0 and len(h.api.sent()) == 1                       # в памяти — не повторяется каждые 30 с
    import http.client

    def dropped(req, timeout):
        raise http.client.RemoteDisconnected('Remote end closed connection without response')
    with pytest.raises(tg_api.TelegramError) as e:
        tg_api.BotApi(SECRET, opener=dropped)('getMe')
    assert str(e.value) == 'RemoteDisconnected'


def test_review_medium4_ack_wins_over_escalation_and_scores_are_computed_outside_the_lock(tmp_path):
    import threading
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    sent = len(h.api.sent())
    assert h.bot._escalate(rec.key, la.TgRules(), NOW + timedelta(minutes=20)) == 0 and len(h.api.sent()) == sent
    # долгий расчёт «Վարորդներ» — без замка (кнопки в это время отвечают)
    free = []

    def scores(a, b):
        t = threading.Thread(target=lambda: free.append(h.bot.lock.acquire(timeout=1) and (h.bot.lock.release() or True)))
        t.start()
        t.join()
        return SCORE_ROWS
    monday = datetime(2026, 10, 5, 9, 0, tzinfo=Y)
    h2 = Harness(tmp_path, {}, monday, db='b.db')
    h2.feeds.scores = scores   # под замком прохода другой поток его бы не получил (False через 1 с)
    h2.set(plan=plan_of(day=monday.date()))
    assert h2.tick() == 2 and free == [True]


def test_review_medium5_explained_minor_and_past_day_records_are_closed(tmp_path):
    dev = alert('deviation', 5, km=2.0, lat=1.0, lon=1.0)
    kinds = {'live_alert_kinds': list(st.LIVE_ALERT_KINDS),
             'tg_levels': {**st.DEFAULT_SETTINGS['tg_levels'], 'deviation': 'critical'}}
    h = Harness(tmp_path, {'CAR1': card(dev)}, NOW, settings=kinds)
    h.tick()
    mid = h.rec('alert:CAR1|deviation').message_id
    h.set(cards={'CAR1': card({**dev, 'active': False, 'explained': {'reason': 'road'}})},
          now=NOW + timedelta(minutes=15))
    assert h.tick() == 1 and h.api.text('-100', mid).startswith('✅ <b>Բացատրված է</b>')
    assert h.api.buttons('-100', mid) == [la.MAP_TEXT, la.WHERE_TEXT] and len(h.api.sent()) == 1   # без эскалации
    h2 = Harness(tmp_path, {'CAR1': card(dev)}, NOW, settings=kinds, db='b.db')
    h2.tick()
    h2.set(cards={'CAR1': card({**dev, 'active': False, 'minor': True})})
    h2.tick()
    assert h2.rec('alert:').phase == 'ended' and h2.rec('alert:').payload['end_reason'] == 'minor'
    # «нет связи» пропала из журнала без конца (20:00) — запись «идёт»; на следующий день закрывается правкой
    h3 = Harness(tmp_path, gps_card(), NOW, db='c.db')
    h3.tick()
    mid3 = h3.rec('alert:').message_id
    h3.set(cards={})
    h3.tick()
    assert h3.rec('alert:').phase == 'active'
    h3.set(now=datetime(2026, 10, 7, 8, 30, tzinfo=Y))
    assert h3.tick() == 1 and h3.api.text('-100', mid3).startswith('✅ <b>Օրն ավարտվեց</b>') and len(h3.api.sent()) == 1
    assert h3.tick() == 0


def test_review_low1_legacy_three_part_late_key_is_skipped_and_summary_counts_real_late(tmp_path):
    legacy = tmp_path / la.STATE_FILE
    legacy.write_text(json.dumps({'sent': {
        'CAR1|late|2026-10-06': {'at': at(10), 'start': at(10), 'end': None},
        'CAR1|late|2026-10-06|c1': {'at': at(10), 'start': at(10), 'end': None, 'over': 20},
        'CAR1|late|2026-10-06|c2': {'at': at(10), 'start': at(10), 'end': None, 'over': 30}}}), encoding='utf-8')
    h = Harness(tmp_path, {'CAR1': card(planned=True, closed=True), 'CAR2': card(car='CAR2', planned=True, closed=True)},
                NOW, legacy=str(legacy), settings={'live_alert_kinds': []})
    assert list(h.bot.records) == ['late:CAR1|2026-10-06']                    # без мусорной alert:CAR1|late|…
    h.set(plan=plan_of(), fleet={})
    h.tick()
    summary = [t for t in h.api.texts() if t.startswith('🏁')][0]
    assert 'ուշացում՝ 2' in summary.split('\n')[2]


def test_review_low2_non_ascii_callback_data_is_ignored_not_raised(tmp_path):
    h = Harness(tmp_path, {'ԱԲ12': card(car='ԱԲ12')}, NOW, settings={'live_alert_kinds': []})
    assert h.bot.verify('w:ԱԲ12|Ա') is None and h.bot.verify(h.bot.sign('w:ԱԲ12')) == 'w:ԱԲ12'
    h.bot.handle(callback(h, 'w:ԱԲ12|ԱԲԳԴ'))
    assert 'text' not in h.api.of('answerCallbackQuery')[-1] and h.api.of('sendLocation') == []
    h.bot.handle(callback(h, h.bot.sign('w:ԱԲ12')))
    assert len(h.api.of('sendLocation')) == 1


def test_review_low3_429_on_unlimited_methods_waits_retry_after():
    answers = [urllib.error.HTTPError('u', 429, 'Too Many', {}, io.BytesIO(
        b'{"ok": false, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 5}}')),
        Resp(b'{"ok": true, "result": []}')]

    def opener(req, timeout):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    clock = Clock()
    api = tg_api.BotApi(SECRET, opener=opener, limiter=tg_api.RateLimiter(clock, clock.sleep))
    assert api('getUpdates', timeout=0) == [] and clock.slept == [5.0]


def test_review_low4_fit_without_newlines_is_hard_cut():
    text = tg_bot.fit('<b>' + 'Ա' * 5000 + '</b>')
    assert len(text) <= tg_bot.TEXT_MAX and '<b>' not in text and text.endswith('…')


def test_review_low5_escalation_recipient_outside_group_can_ack_in_private(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW, settings={'tg_escalate_to': [555]})
    h.tick()
    h.set(now=NOW + timedelta(minutes=10))
    h.tick()
    private = h.api.sent('555')[-1]
    h.bot.handle(callback(h, private['reply_markup']['inline_keyboard'][0][0]['callback_data'], uid=555, first='Տեր',
                          chat={'id': 555, 'type': 'private'}))
    assert h.rec('alert:').acked_name == 'Տեր' and h.api.of('getChatMember') == [p for p in h.api.of('getChatMember')
                                                                                  if p['user_id'] != 555]


def test_review_low6_settings_js_falls_back_to_default_level():
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert '(cur[code] || TG_DEFAULT[code]) === v' in js
    assert all(f"{k}: '{v}'" in js for k, v in st.DEFAULT_SETTINGS['tg_levels'].items())


def test_review_low7_help_takes_escalation_minutes_from_settings(tmp_path):
    h = Harness(tmp_path, {}, NOW, settings={'tg_escalate_min': 25})
    h.bot.handle(command('/help'))
    assert '25 րոպեից պատասխան չկա' in h.api.texts()[-1] and '10 րոպե' not in h.api.texts()[-1]
    assert 'ղեկավարին' not in rp.help_text(0)


def test_review_low8_first_start_sends_no_stale_week_or_summary(tmp_path):
    wednesday = datetime(2026, 10, 7, 9, 0, tzinfo=Y)
    h = Harness(tmp_path, {}, wednesday)
    h.set(plan=plan_of(day=wednesday.date()), scores=SCORE_ROWS)
    h.tick()
    assert [t[:1] for t in h.api.texts()] == ['📋']           # план — да, прошлая неделя — нет
    late_evening = datetime(2026, 10, 7, 21, 0, tzinfo=Y)                     # запуск в 21:00: итог дня устарел
    h2 = Harness(tmp_path, {}, late_evening, db='b.db', settings={'live_quiet_from': '23:00', 'live_quiet_to': '07:00'})
    h2.set(plan=plan_of(day=late_evening.date()))
    h2.tick()
    assert not any(t.startswith('🏁') for t in h2.api.texts()) and h2.rec('summary:').payload == {'why': 'stale'}
    # праздник в понедельник — неделя со вторым (первым рабочим) днём
    tuesday = datetime(2026, 10, 6, 9, 0, tzinfo=Y)
    h3 = Harness(tmp_path, {}, tuesday, db='c.db', settings={'holidays': ['2026-10-05']})
    h3.set(plan=plan_of(), scores=SCORE_ROWS)
    assert h3.tick() == 2 and h3.api.texts()[-1].startswith('🏆')


def test_review_low9_repeat_window_applies_within_one_pass(tmp_path):
    cards = {'CAR1': card(alert('speed', 5, 4, max_kmh=100, lat=1.0, lon=1.0), alert('speed', 2, max_kmh=110, lat=1.0, lon=1.0))}
    h = Harness(tmp_path, cards, NOW)
    assert h.tick() == 1 and len(h.api.messages) == 1
    assert sorted(r.payload.get('why') or r.phase for r in h.bot.records.values()) == ['ended', 'repeat']


# ============================== ревью 2 ==============================

def test_review2_high_report_data_failure_does_not_block_alerts(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW)

    def broken(day):
        raise st.StoreError('database is locked')
    h.feeds.plan = broken
    assert h.tick() == 1 and 'данные отчётов не прочитаны' in caplog.text and h.bot.failures == 0


def test_review2_medium_ack_is_answered_without_waiting_for_the_pass_lock(tmp_path):
    import threading
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.lock.acquire()                       # проход тревог занят отправкой
    worker = threading.Thread(target=h.bot.handle, args=(callback(h, h.bot.sign(f'a:{rec.id}')),))
    try:
        worker.start()
        for _ in range(100):
            if h.api.of('answerCallbackQuery'):
                break
            threading.Event().wait(0.01)
        assert h.api.of('answerCallbackQuery')[-1]['text'] == tg_bot.ACKED and rec.acked_by is None
    finally:
        h.bot.lock.release()
    worker.join(2)
    assert h.rec('alert:').acked_name == 'Գոռ'


def test_review2_low_rejected_late_reply_keeps_edit_state_and_repost_restarts_escalation(tmp_path):
    h = Harness(tmp_path, {'CAR1': card(late('c1', 20))}, NOW)
    h.tick()
    t = NOW + timedelta(minutes=3)
    h.set(now=t, cards={'CAR1': card(late('c1', 60, now=t))})
    h.api.fail['sendMessage'] = [err(400, 'Bad Request: message to be replied not found')]
    h.tick()
    rec = h.rec('late:')
    assert rec.phase == 'active' and '60 րոպեով' in rec.payload['body'] and rec.payload['lines']['c1']['sent'] == 60
    # отвергнутое «не успеет» потом всё же ушло — эскалация через 10 мин от этого момента, не от первой попытки
    h2 = Harness(tmp_path, {'CAR1': card(late('c1', 20))}, NOW, db='b.db')
    h2.api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    h2.tick()
    t = NOW + timedelta(minutes=30)
    h2.set(now=t, cards={'CAR1': card(late('c1', 60, now=t))})
    h2.tick()
    assert h2.rec('late:').sent_at == t.isoformat() and h2.rec('late:').escalated_at is None
    h2.set(now=t + timedelta(minutes=10))
    h2.tick()
    assert h2.rec('late:').escalated_at is not None


def test_review2_low_deleted_plan_repost_rejected_is_not_retried_every_pass(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    h.set(plan=plan_of())
    h.tick()
    h.api.messages.clear()                                                 # план удалили в группе
    changed = {**STORED_PLAN, 'trips': STORED_PLAN['trips'] + [{'id': 4, 'truck': 'CAR2', 'stops': [505]}]}
    h.set(plan=plan_of(changed))
    h.api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    h.tick()
    calls = len(h.api.calls)
    h.tick()
    assert len(h.api.calls) == calls and h.bot.failures == 0


def test_review2_low_wrong_chat_errors_are_chat_wide_and_fit_keeps_entities():
    for d in ('Bad Request: PEER_ID_INVALID', 'Bad Request: chat_id is empty', 'Bad Request: CHAT_WRITE_FORBIDDEN'):
        assert tg_api.chat_wide(err(400, d)) and not tg_api.per_message(err(400, d))
    text = tg_bot.fit('Ա &amp; Բ ' * 800)
    assert len(text) <= tg_bot.TEXT_MAX and '&amp;amp;' not in text and '&amp;' in text
    assert not text[:-1].rstrip().endswith(('&', '&a', '&am', '&amp'))


def test_review2_low_lone_surrogate_callback_is_ignored(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    assert h.bot.verify('w:\ud800|abc') is None
    h.bot.handle(callback(h, 'a:\ud800|x'))
    assert 'text' not in h.api.of('answerCallbackQuery')[-1]


# ============================== ревью 3 ==============================

def test_review3_closed_topic_falls_back_to_main_chat_and_keeps_topic_ids(tmp_path):
    api = FakeTelegram(forum=True)
    h = Harness(tmp_path, gps_card(), NOW, api=api)
    api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    assert h.tick() == 1 and h.rec('alert:').phase == 'active' and 'message_thread_id' not in api.sent()[-1]
    assert len(h.bot.topics) == 4


def test_review3_answers_from_snapshot_without_lock_for_every_outcome(tmp_path):
    import threading
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    h.bot.lock.acquire()
    try:
        for data in (h.bot.sign(f'a:{rec.id}'), h.bot.sign('a:9999')):
            t = threading.Thread(target=h.bot.handle, args=(callback(h, data, uid=8),))
            t.start()
            t.join(2)
            assert not t.is_alive()                                            # ответ — без замка прохода
    finally:
        h.bot.lock.release()
    assert [p['text'] for p in h.api.of('answerCallbackQuery')][-2:] == ['Արդեն նշված է՝ Գոռ', 'Հնացած է']


def test_review3_surrogate_in_name_and_broken_action_do_not_stop_the_pass(tmp_path, caplog, monkeypatch):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}'), first='Ա\ud800'))
    assert h.rec('alert:').acked_by == 7
    body = json.loads(json.dumps({'t': 'Ա\ud800'}, ensure_ascii=True))     # тело запроса кодируется (ASCII)
    assert body['t'] == 'Ա\ud800'
    cards = {'CAR1': card(alert('center', 1, lat=1.0, lon=1.0)), 'CAR2': card(alert('center', 1, lat=1.0, lon=1.0), car='CAR2')}
    h2 = Harness(tmp_path, cards, NOW, db='b.db')
    real = la.keyboard
    monkeypatch.setattr(la, 'keyboard', lambda rec, sign: (_ for _ in ()).throw(KeyError('x')) if rec.car == 'CAR1'
                        else real(rec, sign))
    assert h2.tick() == 1 and h2.rec('alert:CAR1').phase == 'failed' and 'не выполнено' in caplog.text
    assert h2.tick() == 0


def test_review3_setup_error_of_any_kind_does_not_abort_the_pass(tmp_path, caplog):
    api = FakeTelegram(forum=True)
    api.fail['createForumTopic'] = [KeyError('message_thread_id')]
    h = Harness(tmp_path, gps_card(), NOW, api=api)
    assert h.tick() == 1 and 'настройка не удалась' in caplog.text


# ============================== ревью 4 ==============================

def test_review4_pending_ack_blocks_escalation_until_written(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.pending_acks.add(rec.id)                     # «Գրանցված է» уже сказано, запись ждёт замка
    h.set(now=NOW + timedelta(minutes=11))
    assert h.tick() == 0 and h.rec('alert:').escalated_at is None
    h.bot.pending_acks.clear()
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    assert h.bot.pending_acks == set() and h.rec('alert:').acked_by == 7
    assert h.tick() == 0                                # подтверждена — эскалации нет


def test_review4_queue_waits_happen_outside_the_bot_lock_and_429_does_not_sleep_under_it(tmp_path):
    import threading
    clock = Clock()
    holder = {}

    def sleep(s):
        bot = holder.get('bot')
        if bot is not None:   # кто бы ни ждал очередь, замок бота свободен
            free = []
            t = threading.Thread(target=lambda: free.append(bot.lock.acquire(timeout=0.5) and (bot.lock.release() or True)))
            t.start()
            t.join()
            holder.setdefault('free', []).append(free == [True])
        clock.sleep(s)
    sent = []

    def opener(req, timeout):
        body = json.loads(req.data.decode())
        sent.append(body)
        mid = len(sent)
        return Resp(json.dumps({'ok': True, 'result': {'message_id': mid, 'id': 1, 'username': 'b', 'type': 'group'}
                                if 'getMe' not in req.full_url else {'id': 999, 'username': 'b'}}).encode())
    api = tg_api.BotApi(SECRET, opener=opener, limiter=tg_api.RateLimiter(clock, sleep))
    cards = {f'C{i}': card(alert('center', 1, lat=1.0, lon=1.0), car=f'C{i}') for i in range(3)}
    h = Harness(tmp_path, cards, NOW, api=api)
    holder['bot'] = h.bot
    assert h.tick() == 3 and holder['free'] and all(holder['free'])
    # под no_wait: 429 — сразу сбой с retry_after (без сна), долгая очередь — тоже
    lim = tg_api.RateLimiter(clock, clock.sleep)
    lim.pause(30)
    before = list(clock.slept)
    with tg_api.no_wait(), pytest.raises(tg_api.TelegramError) as e:
        lim.wait('-100')
    assert e.value.status == 429 and e.value.retry_after == pytest.approx(30) and clock.slept == before
    lim.idle('-100')
    assert lim.slot('-100') == 0                       # idle не резервирует место


def test_review4_lone_surrogate_name_is_saved(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}'), first='Ա\ud800'))
    assert h.rec('alert:').acked_name == 'Ա?'
    assert h.restart().records[rec.key].acked_name == 'Ա?'      # в базе (UTF-8)


def test_review4_report_exception_with_existing_record_is_not_retried_and_odd_send_reply_keeps_record(tmp_path,
                                                                                                    caplog, monkeypatch):
    h = Harness(tmp_path, {}, NOW)
    h.set(plan=plan_of())
    h.tick()
    changed = {**STORED_PLAN, 'trips': STORED_PLAN['trips'] + [{'id': 4, 'truck': 'CAR2', 'stops': [505]}]}
    h.set(plan=plan_of(changed))
    monkeypatch.setattr(h.bot, '_edit', lambda rec: (_ for _ in ()).throw(ValueError('boom')))
    h.tick()
    n = caplog.text.count('не выполнено')
    h.tick()
    assert n == 1 and caplog.text.count('не выполнено') == 1        # подпись записана — без повтора и traceback
    # Telegram ответил странно (не объект) — сообщение ушло, запись та же, кнопка её — «Գրանցված է»
    api = FakeTelegram()
    api._sendMessage = lambda p: True
    h2 = Harness(tmp_path, gps_card(), NOW, api=api, db='b.db')
    assert h2.tick() == 1
    rec = h2.rec('alert:')
    assert rec.phase == 'active' and rec.message_id is None
    h2.bot.handle(callback(h2, h2.bot.sign(f'a:{rec.id}')))
    assert api.of('answerCallbackQuery')[-1]['text'] == tg_bot.ACKED and h2.rec('alert:').acked_by == 7


def test_review4_closed_topic_is_remembered(tmp_path, caplog):
    api = FakeTelegram(forum=True)
    cards = {'CAR1': card(alert('center', 1, lat=1.0, lon=1.0)), 'CAR2': card(alert('center', 1, lat=1.0, lon=1.0), car='CAR2')}
    h = Harness(tmp_path, cards, NOW, api=api)
    api.fail['sendMessage'] = [err(400, 'Bad Request: TOPIC_CLOSED')]
    assert h.tick() == 2
    assert len(api.of('sendMessage')) == 3 and caplog.text.count('закрыта') == 1   # вторая — сразу в общий чат
    h.clock[0] += tg_bot.SETUP_RETRY_S + 1
    h.set(cards={**cards, 'CAR3': card(alert('center', 1, lat=1.0, lon=1.0), car='CAR3')})
    h.tick()
    assert api.sent()[-1].get('message_thread_id') == h.bot.topics['violations']   # потом — снова в тему


# ============================== ревью 5 ==============================

def local429(seconds=5.0):
    return tg_api.TelegramError('HTTP 429: очередь', 429, retry_after=seconds, description='rate limit (local queue)',
                                local=True)


def test_review5_ack_edit_hit_by_queue_is_redone_next_pass(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    h.api.fail['editMessageText'] = [local429()]
    h.bot.handle(callback(h, h.bot.sign(f'a:{rec.id}')))
    assert h.rec('alert:').acked_by == 7 and h.rec('alert:').payload.get('ack_edit') is True
    assert '✔ Տեսավ' not in h.api.text('-100', rec.message_id)
    h.tick()
    assert h.api.text('-100', rec.message_id).endswith('✔ Տեսավ Գոռ · 11:00') and 'ack_edit' not in h.rec('alert:').payload
    assert la.ACK_TEXT not in h.api.buttons('-100', rec.message_id)


def test_review5_private_copy_hit_by_queue_is_resent_and_only_403_means_no_start(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW, settings={'tg_escalate_to': [OWNER, 5]})
    h.tick()
    h.set(now=NOW + timedelta(minutes=10))
    # группа; владелец — очередь (и при доделке в том же проходе — снова очередь)
    h.api.fail['sendMessage'] = [None, local429(), local429()]
    h.tick()
    rec = h.rec('alert:')
    assert rec.payload['copies_pending'] == [str(OWNER), '5'] and '/start' not in caplog.text
    assert h.bot.failures == 0
    h.tick()
    rec = h.rec('alert:')
    assert 'copies_pending' not in rec.payload and [c[0] for c in rec.payload['copies']] == [str(OWNER), '5']
    assert len([t for t in h.api.texts('-100') if t.startswith('❗')]) == 1  # в группе — один раз


def test_review5_local_queue_error_is_not_a_failure(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW)
    h.api.fail['sendMessage'] = [local429(7)]
    assert h.tick() == 0 and h.bot.failures == 0 and h.bot.client_errors == 0
    assert h.bot.retry_at == pytest.approx(h.clock[0] + 7) and 'не отправлено' not in caplog.text
    h.clock[0] += 8
    assert h.tick() == 1


def test_review5_ack_right_before_send_stops_escalation(tmp_path):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')

    class Names(dict):
        def get(self, k, default=None):     # пока собирается текст эскалации, человек нажимает «Տեսա»
            h.bot.pending_acks.add(rec.id)
            return super().get(k, default)
    h.bot.names = Names()
    h.set(now=NOW + timedelta(minutes=11))
    assert h.tick() == 0 and not [t for t in h.api.texts() if t.startswith('❗')]
    assert h.rec('alert:').escalated_at is None


def test_review5_odd_reply_to_private_copy_does_not_crash(tmp_path):
    api = FakeTelegram()
    real = api._sendMessage
    api._sendMessage = lambda p: True if str(p['chat_id']) == str(OWNER) else real(p)
    h = Harness(tmp_path, gps_card(), NOW, api=api)
    h.tick()
    h.set(now=NOW + timedelta(minutes=10))
    assert h.tick() == 2 and h.rec('alert:').payload['copies'] == [[str(OWNER), None]]


def test_review5_pin_hit_by_queue_is_retried(tmp_path):
    h = Harness(tmp_path, {}, NOW)
    h.set(plan=plan_of())
    h.api.fail['pinChatMessage'] = [local429()]
    h.tick()
    rec = h.rec('plan:')
    assert rec.payload.get('pin') == {'unpin': None} and h.bot.pin_warned is False
    h.tick()
    assert 'pin' not in h.rec('plan:').payload and h.api.of('pinChatMessage')[-1]['message_id'] == rec.message_id
    assert len(h.api.of('pinChatMessage')) == 2


# ============================== ревью 6 ==============================

def test_review6_unpin_is_not_repeated_when_only_the_pin_was_throttled(tmp_path, caplog):
    cars = {'CAR1': card(planned=True, closed=True), 'CAR2': card(car='CAR2', planned=True, closed=True)}
    h = Harness(tmp_path, cars, NOW, settings={'live_alert_kinds': []})
    h.set(plan=plan_of(), fleet={})
    h.api.fail['pinChatMessage'] = [None, local429()]          # план закреплён; итог — открепили план, закрепить — очередь
    h.tick()
    summary = h.rec('summary:')
    assert summary.payload['pin'] == {'unpin': None} and len(h.api.of('unpinChatMessage')) == 1
    h.api.fail['unpinChatMessage'] = [err(400, 'Bad Request: message to unpin not found')]
    h.tick()
    assert len(h.api.of('unpinChatMessage')) == 1 and 'pin' not in h.rec('summary:').payload
    assert h.api.of('pinChatMessage')[-1]['message_id'] == summary.message_id and 'не закреплено' not in caplog.text


def test_review6_broken_pending_record_does_not_block_reports(tmp_path, caplog, monkeypatch):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    rec = h.rec('alert:')
    rec.payload['copies_pending'] = [str(OWNER)]
    real = la.render
    monkeypatch.setattr(la, 'render', lambda r: (_ for _ in ()).throw(KeyError('body')) if r.key == rec.key else real(r))
    h.set(plan=plan_of())
    h.tick()
    assert 'отложенное' in caplog.text and 'copies_pending' not in h.rec('alert:').payload
    assert any(t.startswith('📋') for t in h.api.texts())               # отчёт дня ушёл в том же проходе


def test_review6_dm_chat_not_found_gets_the_start_hint(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW)
    h.tick()
    h.set(now=NOW + timedelta(minutes=10))
    h.api.fail['sendMessage'] = [None, err(400, 'Bad Request: chat not found')]
    h.tick()
    assert '/start' in caplog.text and h.bot.client_errors == 0 and 'copies_pending' not in h.rec('alert:').payload


def test_settings_page_has_telegram_block():
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert all(f"key: '{k}'" in js for k in ('tg_levels', 'tg_sim_installed', 'tg_escalate_min', 'tg_escalate_to',
                                              'tg_report_plan', 'tg_report_summary', 'tg_summary_at', 'tg_report_week'))
    assert all(f"['{k}'," in js for k in st.TG_LEVEL_KINDS) and 'Տերմինալներում կա բջջային ինտերնետ (SIM)' in js
    assert "routes_settings.js') }}?v=42" in (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')


def test_token_is_never_logged(tmp_path, caplog):
    h = Harness(tmp_path, gps_card(), NOW)
    h.api.fail['getMe'] = [tg_api.TelegramError('URLError')]
    h.api.fail['sendMessage'] = [err(401, 'Unauthorized')]
    h.tick()
    assert caplog.text and SECRET not in caplog.text and 'SECRET' not in caplog.text
