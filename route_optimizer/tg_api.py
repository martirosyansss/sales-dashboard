# -*- coding: utf-8 -*-
"""Транспорт Telegram Bot API для бота «Araqich Dispatch» (№91, docs/plans/telegram-bot-plan.md §1).

Только urllib (как прежний send_telegram): метод → POST JSON на https://api.telegram.org/bot<токен>/<метод>, ответ —
result или TelegramError. Токен есть только в адресе запроса: в тексте ошибок и журнале его нет (ни адреса, ни
исключения urllib с адресом — только код HTTP и description Telegram).

Ограничения Telegram (RateLimiter): не чаще CHAT_INTERVAL_S на чат и не больше GROUP_PER_MIN в минуту на группу
(отрицательный chat_id) — для методов, которые пишут в чат (LIMITED); место в очереди резервируется под замком, ждут —
вне замка (два потока бота не держат друг друга). 429 — пауза retry_after для всех чатов; не дольше RETRY_429_MAX_S —
ждём и повторяем один раз, дольше — TelegramError(429): вызывающий повторит позже. 400 с migrate_to_chat_id (группа стала
супергруппой) — в TelegramError.migrate_to. В блоке no_wait() (бот держит свой замок) вызов не ждёт дольше
NO_WAIT_MAX_S и не повторяет 429 — TelegramError(429, retry_after): ждут вне замка (RateLimiter.idle, пауза прохода).
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator

API_URL = 'https://api.telegram.org/bot{token}/{method}'
HTTP_TIMEOUT_S = 10.0
CHAT_INTERVAL_S = 1.0       # ≤ 1 сообщение в секунду на чат
GROUP_PER_MIN = 20          # ≤ 20 сообщений в минуту на группу
RETRY_429_MAX_S = 60.0      # 429: подождать столько и меньше — и повторить один раз; дольше — сбой
DESCRIPTION_MAX = 200
# методы, которые пишут в чат: под ограничение частоты (answerCallbackQuery, getUpdates и чтение — нет)
LIMITED = frozenset({'sendMessage', 'editMessageText', 'editMessageReplyMarkup', 'sendLocation', 'pinChatMessage',
                     'unpinChatMessage', 'createForumTopic'})


class TelegramError(RuntimeError):
    """Сбой вызова Bot API; текст — код и description Telegram, без токена. status — код HTTP (или error_code ответа),
    retry_after — секунды паузы при 429, migrate_to — новый id группы (стала супергруппой)."""

    def __init__(self, text: str, status: int | None = None, retry_after: float | None = None,
                 description: str = '', migrate_to: int | None = None):
        super().__init__(text)
        self.status = status
        self.retry_after = retry_after
        self.description = description
        self.migrate_to = migrate_to


class _Local(threading.local):
    no_wait = False


_local = _Local()
NO_WAIT_MAX_S = 2.0   # под замком бота (no_wait) ждать очередь не дольше; дольше и 429 — сбой с retry_after


@contextmanager
def no_wait() -> Iterator[None]:
    """В этом потоке вызовы не ждут дольше NO_WAIT_MAX_S (очередь, пауза 429) и не повторяют 429 — TelegramError(429,
    retry_after): бот держит свой замок и подождёт вне его (пауза прохода)."""
    before = _local.no_wait
    _local.no_wait = True
    try:
        yield
    finally:
        _local.no_wait = before


class RateLimiter:
    """Очередь отправки по чатам (правило — в описании модуля). clock / sleep — подмена в тестах."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.next_at: dict[str, float] = {}           # чат → не раньше
        self.window: dict[str, deque[float]] = {}     # группа → моменты отправок за последнюю минуту
        self.paused_until = 0.0                       # 429: пауза для всех

    def slot(self, chat: str) -> float:
        """Зарезервировать момент отправки в chat → сколько ждать, с."""
        with self.lock:
            now = self.clock()
            at = max(now, self.next_at.get(chat, 0.0), self.paused_until)
            if chat.startswith('-'):
                sent = self.window.setdefault(chat, deque())
                while sent and sent[0] <= at - 60.0:
                    sent.popleft()
                if len(sent) >= GROUP_PER_MIN:
                    at = max(at, sent[len(sent) - GROUP_PER_MIN] + 60.0)
                sent.append(at)
            self.next_at[chat] = at + CHAT_INTERVAL_S
            return at - now

    def ready_in(self, chat: str) -> float:
        """Сколько ждать до свободного места в chat — без резервирования (подождать заранее, вне чужих замков)."""
        with self.lock:
            now = self.clock()
            at = max(now, self.next_at.get(chat, 0.0), self.paused_until)
            sent = self.window.get(chat) if chat.startswith('-') else None
            if sent:
                recent = [t for t in sent if t > at - 60.0]
                if len(recent) >= GROUP_PER_MIN:
                    at = max(at, recent[len(recent) - GROUP_PER_MIN] + 60.0)
            return at - now

    def idle(self, chat: str) -> None:
        delay = self.ready_in(chat)
        if delay > 0:
            self.sleep(delay)

    def wait(self, chat: str) -> None:
        if _local.no_wait:   # под замком бота: долгого ожидания нет — сбой с retry_after, повтор позже
            ahead = self.ready_in(chat)
            if ahead > NO_WAIT_MAX_S:
                raise TelegramError(f'HTTP 429: очередь {ahead:.0f} с', 429, retry_after=ahead,
                                    description='rate limit (local queue)')
        delay = self.slot(chat)
        if delay > 0:
            self.sleep(delay)

    def hold(self) -> None:
        """Метод вне очереди чатов (getUpdates, getChatMember, answerCallbackQuery): только общая пауза 429."""
        with self.lock:
            delay = self.paused_until - self.clock()
        if delay > 0:
            if _local.no_wait and delay > NO_WAIT_MAX_S:
                raise TelegramError('HTTP 429: пауза', 429, retry_after=delay, description='rate limit (paused)')
            self.sleep(delay)

    def pause(self, seconds: float) -> None:
        with self.lock:
            self.paused_until = max(self.paused_until, self.clock() + seconds)


CHAT_WIDE_400 = ('chat not found', 'chat was deactivated', 'was kicked', 'not a member', 'have no rights to send',
                 'not enough rights to send', 'peer_id_invalid', 'chat_id is empty', 'chat_id_invalid',
                 'chat_write_forbidden', 'chat_restricted')


def chat_wide(e: TelegramError) -> bool:
    """Ошибка всего чата или бота (токен неверен — 401/404, бота убрали или запретили — 403, чата нет, нет права писать):
    повтор других сообщений бессмыслен, такие подряд останавливают рассылку."""
    if e.status in (401, 403, 404):
        return True
    return e.status == 400 and any(x in e.description.lower() for x in CHAT_WIDE_400)


def per_message(e: TelegramError) -> bool:
    """Ошибка одного сообщения (400: тема закрыта, разметка, длина, кнопка, нельзя ответить): это сообщение не уйдёт и
    при повторе — в журнал и дальше; остальные — шлются."""
    return isinstance(e.status, int) and 400 <= e.status < 500 and e.status not in (409, 429) and not chat_wide(e)


def _error(status: int | None, body: Any) -> TelegramError:
    """TelegramError из кода и тела ответа (JSON Telegram или что угодно)."""
    body = body if isinstance(body, dict) else {}
    desc = str(body.get('description') or '')[:DESCRIPTION_MAX]
    params = body.get('parameters') if isinstance(body.get('parameters'), dict) else {}
    ra = params.get('retry_after')
    mig = params.get('migrate_to_chat_id')
    code = status if status is not None else (body.get('error_code') if isinstance(body.get('error_code'), int) else None)
    text = (f'HTTP {code}' if code is not None else 'ok=false') + (f': {desc}' if desc else '')
    return TelegramError(text, code, float(ra) if isinstance(ra, (int, float)) and not isinstance(ra, bool) else None,
                         desc, mig if isinstance(mig, int) and not isinstance(mig, bool) else None)


class BotApi:
    """Вызов метода: api('sendMessage', chat_id=…, text=…) → result. opener — подмена urllib в тестах."""

    def __init__(self, token: str, opener: Callable[..., Any] = urllib.request.urlopen,
                 limiter: RateLimiter | None = None, timeout: float = HTTP_TIMEOUT_S):
        self._token = token
        self.opener = opener
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout

    def __repr__(self) -> str:   # токен — ни в repr, ни в журнал
        return 'BotApi(<token>)'

    def _once(self, method: str, params: dict[str, Any]) -> Any:
        req = urllib.request.Request(API_URL.format(token=self._token, method=method), method='POST',
                                     # ensure_ascii: одиночный суррогат (имя человека) не уронит кодирование тела
                                     data=json.dumps(params, ensure_ascii=True).encode('ascii'),
                                     headers={'Content-Type': 'application/json'})
        timeout = self.timeout + float(params.get('timeout') or 0)   # getUpdates: long poll + запас
        try:
            with self.opener(req, timeout=timeout) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b'{}')
            except (OSError, ValueError, AttributeError):
                body = None
            raise _error(e.code, body) from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            # сеть, тайм-аут, обрыв ответа (RemoteDisconnected, IncompleteRead), не JSON — без адреса (в нём токен)
            raise TelegramError(type(e).__name__) from None
        if not isinstance(body, dict) or not body.get('ok'):
            raise _error(None, body)
        return body.get('result')

    def __call__(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in (1, 2):
            if method in LIMITED and 'chat_id' in params:
                self.limiter.wait(str(params['chat_id']))
            else:   # 429 и у них — повтор не раньше retry_after
                self.limiter.hold()
            try:
                return self._once(method, params)
            except TelegramError as e:
                if e.status != 429:
                    raise
                pause = e.retry_after if e.retry_after is not None else CHAT_INTERVAL_S
                self.limiter.pause(pause)
                if attempt == 2 or pause > RETRY_429_MAX_S or _local.no_wait:
                    raise
        raise AssertionError('unreachable')   # pragma: no cover
