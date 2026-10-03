# -*- coding: utf-8 -*-
"""«Հարցրու AI-ին» на странице «Развоз» (ответ владельца №52): разбор запроса страницы, данные дня для модели
(без координат, одинаковый текст — для кэша), точка кэша, вызов модели и ошибки API — по-русски для перевода
страницей. Модель не вызывается: поддельный клиент записывает запрос.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_ai.py -q
"""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import ai_chat  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401


class FakeClient:
    """client.beta.messages.create(**kw) — запоминает запрос, отдаёт заданный ответ или бросает ошибку."""

    def __init__(self, text='Պատասխան', stop='end_turn', error=None):
        self.calls = []
        self.text, self.stop, self.error = text, stop, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        content = [SimpleNamespace(type='thinking', thinking='')]
        if self.text is not None:
            content.append(SimpleNamespace(type='text', text=self.text))
        return SimpleNamespace(content=content, stop_reason=self.stop, model='claude-opus-5-5', _request_id='req_1',
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=100))


def _turns(n):
    return [{'role': 'user' if i % 2 == 0 else 'assistant', 'text': 'реплика %d' % i} for i in range(n)]


# ============================== запрос страницы ==============================

def test_parse_request_keeps_last_turns_from_a_user_turn():
    q, history, focus = ai_chat.parse_request({'question': '  Ո՞ր մեքենան  ', 'history': _turns(16), 'focus': 'JAC · 475DD61, երթ 2'})
    assert q == 'Ո՞ր մեքենան'
    assert len(history) == ai_chat.MAX_HISTORY and history[0]['role'] == 'user' and history[-1]['role'] == 'assistant'
    assert history[-1]['text'] == 'реплика 15'
    assert focus == 'JAC · 475DD61, երթ 2'


@pytest.mark.parametrize('payload', [
    {},
    {'question': '   '},
    {'question': 'x' * (ai_chat.MAX_QUESTION + 1)},
    {'question': 'q', 'history': 'нет'},
    {'question': 'q', 'history': _turns(3)},                                   # нечётная — без ответа
    {'question': 'q', 'history': [{'role': 'assistant', 'text': 'a'}, {'role': 'user', 'text': 'b'}]},
    {'question': 'q', 'history': [{'role': 'user', 'text': 'a'}, {'role': 'user', 'text': 'b'}]},
    {'question': 'q', 'history': [{'role': 'user', 'text': 1}, {'role': 'assistant', 'text': 'b'}]},
    {'question': 'q', 'history': [{'role': 'user', 'text': 'a'}, {'role': 'assistant', 'text': 'b' * (ai_chat.MAX_TURN_TEXT + 1)}]},
], ids=['empty', 'blank', 'long', 'not-list', 'odd', 'assistant-first', 'same-role', 'not-text', 'long-turn'])
def test_parse_request_rejects_bad_payload(payload):
    with pytest.raises(ai_chat.AiError) as e:
        ai_chat.parse_request(payload)
    assert e.value.status == 400


def test_parse_request_drops_bad_focus_but_answers():
    assert ai_chat.parse_request({'question': 'q', 'focus': 'x' * (ai_chat.MAX_FOCUS + 1)})[2] is None
    assert ai_chat.parse_request({'question': 'q', 'focus': 5})[2] is None


# ============================== данные дня ==============================

BODY = {'day': '2026-10-01', 'depot': {'lat': 40.1, 'lon': 44.6}, 'rev': 3, 'orders_sig': 'abc', 'success': True,
        'plan': {'trucks': [{'car_code': 'CAR1', 'trips': [{'id': 1, 'stops': [
            {'name': 'Խանութ «Ա»', 'lat': 40.2, 'lon': 44.5, 'customer_id': 7, 'eta': '09:25', 'kg': 642,
             'orders': [{'isn': 'X-1', 'doc_num': '0001', 'kg': 642}]}]}]}]},
        'geo_suggestions': {'count': 2, 'day_count': 1, 'items': [{'event_id': 1, 'lat': 40.0, 'lon': 44.0}]}}


def test_day_context_drops_coordinates_and_service_fields():
    text = ai_chat.day_context(BODY)
    assert text.startswith('<day_data date="2026-10-01">') and text.endswith('</day_data>')
    for key in ('"lat"', '"lon"', '"isn"', '"customer_id"', '"rev"', '"orders_sig"', '"depot"', '"success"', '"items"'):
        assert key not in text, key
    assert '"orders_count":1' in text and 'Խանութ «Ա»' in text and '"eta":"09:25"' in text
    assert '"geo_suggestions":{"count":2,"day_count":1}' in text


def test_day_context_numbers_trips_within_truck():
    """Рейс называется как на странице — номером у машины («Երթ 2»), а не сквозным id плана."""
    body = {'day': '2026-10-01', 'plan': {'trucks': [{'car_code': 'A', 'trips': [{'id': 1}]},
                                                     {'car_code': 'B', 'trips': [{'id': 2}, {'id': 3}]}]}}
    text = ai_chat.day_context(body)
    assert '{"id":1,"trip_no":1}' in text and '{"id":2,"trip_no":1}' in text and '{"id":3,"trip_no":2}' in text
    assert 'trip_no' in ai_chat.SYSTEM


def test_day_context_same_day_same_text():
    """Порядок ключей не влияет на текст — иначе кэш промахивается на каждом вопросе."""
    shuffled = dict(reversed(list(BODY.items())))
    assert ai_chat.day_context(shuffled) == ai_chat.day_context(BODY)


def test_build_messages_cache_breakpoint_only_on_day_data():
    history = _turns(4)
    msgs = ai_chat.build_messages(BODY, 'Ինչու՞', history, 'JAC, երթ 2')
    assert [m['role'] for m in msgs] == ['user', 'assistant', 'user', 'assistant', 'user']
    first = msgs[0]['content']
    assert first[0]['text'].startswith('<day_data') and first[0]['cache_control'] == {'type': 'ephemeral'}
    assert first[1]['text'] == 'реплика 0'
    marked = [b for m in msgs for b in m['content'] if 'cache_control' in b]
    assert len(marked) == 1
    assert msgs[-1]['content'][-1]['text'] == 'Ինչու՞\n\n[Էջում քարտեզում ընտրված է՝ JAC, երթ 2]'


# ============================== вызов модели ==============================

def test_ask_request_shape_and_text_only_answer(monkeypatch):
    monkeypatch.delenv('ROUTES_AI_MODEL', raising=False)
    monkeypatch.delenv('ROUTES_AI_EFFORT', raising=False)
    fake = FakeClient(text='Առաջին տող\n• պունկտ')
    r = ai_chat.ask(BODY, 'Ո՞ր մեքենան', [], client=fake)
    assert r == {'answer': 'Առաջին տող\n• պունկտ', 'model': 'claude-opus-5-5', 'refused': False, 'truncated': False}
    kw = fake.calls[0]
    assert kw['model'] == 'claude-opus-5-5' and kw['max_tokens'] == ai_chat.MAX_TOKENS
    assert kw['system'] == ai_chat.SYSTEM and 'Armenian' in kw['system']
    assert kw['output_config'] == {'effort': 'low'}
    assert kw['betas'] == [ai_chat.FALLBACK_BETA] and kw['fallbacks'] == 'default'
    assert 'tools' not in kw                                        # модель ничего не может менять


def test_ask_env_overrides(monkeypatch):
    monkeypatch.setenv('ROUTES_AI_MODEL', 'claude-sonnet-5-5')
    monkeypatch.setenv('ROUTES_AI_EFFORT', 'MEDIUM')
    fake = FakeClient()
    ai_chat.ask(BODY, 'q', [], client=fake)
    assert fake.calls[0]['model'] == 'claude-sonnet-5-5' and fake.calls[0]['output_config'] == {'effort': 'medium'}
    monkeypatch.setenv('ROUTES_AI_EFFORT', 'max')                   # не из списка — по умолчанию
    ai_chat.ask(BODY, 'q', [], client=fake)
    assert fake.calls[1]['output_config'] == {'effort': 'low'}


def test_ask_refusal_and_truncation():
    r = ai_chat.ask(BODY, 'q', [], client=FakeClient(text='', stop='refusal'))
    assert r['refused'] is True and r['answer'].startswith('Այս հարցին չեմ կարող')
    r = ai_chat.ask(BODY, 'q', [], client=FakeClient(text='մաս', stop='max_tokens'))
    assert r['truncated'] is True and r['answer'] == 'մաս'
    with pytest.raises(ai_chat.AiError) as e:
        ai_chat.ask(BODY, 'q', [], client=FakeClient(text=None))
    assert e.value.status == 502


def _http_error(cls, status):
    req = httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
    return cls('ошибка', response=httpx.Response(status, request=req), body=None)


@pytest.mark.parametrize('error, text', [
    (lambda: _http_error(anthropic.AuthenticationError, 401), 'AI недоступен: ключ ANTHROPIC_API_KEY не принят'),
    (lambda: _http_error(anthropic.PermissionDeniedError, 403), 'AI недоступен: ключ ANTHROPIC_API_KEY не принят'),
    (lambda: _http_error(anthropic.NotFoundError, 404), 'AI недоступен: модель не найдена — проверьте ROUTES_AI_MODEL'),
    (lambda: _http_error(anthropic.RateLimitError, 429), 'AI сейчас перегружен — повторите через минуту'),
    (lambda: _http_error(anthropic.BadRequestError, 400), 'AI не принял запрос — начните новый разговор'),
    (lambda: _http_error(anthropic.InternalServerError, 500), 'AI временно недоступен — повторите позже'),
    (lambda: anthropic.APIConnectionError(request=httpx.Request('POST', 'https://api.anthropic.com')), 'Нет связи с AI — повторите позже'),
    (lambda: anthropic.APITimeoutError(request=httpx.Request('POST', 'https://api.anthropic.com')), 'Нет связи с AI — повторите позже'),
], ids=['auth', 'permission', 'model', 'rate', 'bad', 'server', 'connection', 'timeout'])
def test_ask_maps_api_errors(error, text):
    with pytest.raises(ai_chat.AiError) as e:
        ai_chat.ask(BODY, 'q', [], client=FakeClient(error=error()))
    assert str(e.value) == text


def test_available_follows_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')
    assert ai_chat.available() is True
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    assert ai_chat.available() is False


def test_ask_without_key_is_unavailable(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', '  ')
    assert ai_chat.available() is False
    with pytest.raises(ai_chat.AiError) as e:
        ai_chat.ask(BODY, 'q', [])
    assert e.value.status == 503 and 'ANTHROPIC_API_KEY' in str(e.value)


# ============================== маршрут ==============================

def test_api_dispatch_ask_answers_from_the_same_day(client, monkeypatch):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    assert client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).status_code == 200
    fake = FakeClient(text='CAR1-ը վերադառնում է ամենաուշը։')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(ai_chat, '_get_client', lambda: fake)
    r = client.post('/api/routes/dispatch/ask', json={'date': '2026-10-01', 'question': 'Ո՞ր մեքենան',
                                                      'history': _turns(2), 'focus': 'CAR1, երթ 1'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'success': True, 'answer': 'CAR1-ը վերադառնում է ամենաուշը։', 'model': 'claude-opus-5-5',
                            'refused': False, 'truncated': False}
    msgs = fake.calls[0]['messages']
    day = msgs[0]['content'][0]['text']
    assert day.startswith('<day_data date="2026-10-01">')
    assert 'Клиент 102' in day and '"plan"' in day and '"explain"' in day      # то же, что видит страница
    assert '"lat"' not in day and '"isn"' not in day
    assert [m['role'] for m in msgs] == ['user', 'assistant', 'user']
    assert msgs[-1]['content'][-1]['text'].endswith('[Էջում քարտեզում ընտրված է՝ CAR1, երթ 1]')


def test_api_dispatch_ask_errors(client, monkeypatch):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    assert client.post('/api/routes/dispatch/ask', json={'date': '2026-10-01'}).status_code == 400
    assert client.post('/api/routes/dispatch/ask', json={'question': 'q'}).status_code == 400   # нет даты
    monkeypatch.setenv('ANTHROPIC_API_KEY', '')
    r = client.post('/api/routes/dispatch/ask', json={'date': '2026-10-01', 'question': 'q'})
    assert r.status_code == 503 and r.get_json()['error'] == 'AI недоступен: не задан ANTHROPIC_API_KEY в .env сервера'
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(ai_chat, '_get_client', lambda: FakeClient(error=_http_error(anthropic.RateLimitError, 429)))
    r = client.post('/api/routes/dispatch/ask', json={'date': '2026-10-01', 'question': 'q'})
    assert r.status_code == 503 and r.get_json()['error'] == 'AI сейчас перегружен — повторите через минуту'


# ============================== страница ==============================

def test_every_ai_error_is_translated_on_the_page():
    """Страница армянская (№31): каждый текст AiError есть в словаре SERVER_HY routes_dispatch.js."""
    src = (ROOT / 'route_optimizer' / 'ai_chat.py').read_text(encoding='utf-8')
    texts = {t.replace('%d', str(ai_chat.MAX_QUESTION)) for t in re.findall(r"AiError\('([^']+)'", src)}
    assert len(texts) >= 9
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    missing = [t for t in texts if "'" + t + "'" not in js]
    assert missing == []


def test_page_ai_answer_rendered_as_text_only():
    """Ответ AI и вопрос выводятся без HTML: в блоке «Հարցրու AI-ին» innerHTML — только постоянные строки."""
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    block = js[js.index('// ---------- «Հարցրու AI-ին»'):js.index('// ---------- Старт ----------')]
    sets = re.findall(r'innerHTML\s*=\s*(.+)$', block, re.M)
    assert sets and all(re.fullmatch(r"'[^'+]*';", s.strip()) for s in sets), sets
