# -*- coding: utf-8 -*-
"""Подделка Telegram Bot API для тестов бота (№91): вызов api(метод, **параметры) записывается, отвечает как Telegram
(номера сообщений, правки, темы форума, участники группы, getUpdates); fail[метод] — очередь исключений для следующих
вызовов. Сеть не используется, токена нет."""
from __future__ import annotations

from typing import Any

from route_optimizer.tg_api import TelegramError

BOT_ID = 999
BOT_NAME = 'Araqum_Garni_bot'


def err(status: int, description: str, **kw: Any) -> TelegramError:
    return TelegramError(f'HTTP {status}: {description}', status, description=description, **kw)


class FakeTelegram:
    def __init__(self, chat: str = '-100', forum: bool = False, rights: bool = True):
        self.chat = chat
        self.forum = forum
        self.rights = rights
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, list[Exception | None]] = {}
        self.members: dict[int, str] = {}
        self.messages: dict[tuple[str, int], dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []
        self.threads: set[int] = set()
        self._mid = 100
        self._thread = 500

    def __call__(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        self.calls.append((method, params))
        queue = self.fail.get(method)
        if queue:
            e = queue.pop(0)
            if e is not None:
                raise e
        handler = getattr(self, '_' + method, None)
        return handler(params) if handler else True

    # --- методы ---

    def _getMe(self, p: dict[str, Any]) -> dict[str, Any]:
        return {'id': BOT_ID, 'is_bot': True, 'username': BOT_NAME}

    def _getChat(self, p: dict[str, Any]) -> dict[str, Any]:
        return {'id': int(p['chat_id']), 'type': 'supergroup', 'is_forum': self.forum}

    def _getChatMember(self, p: dict[str, Any]) -> dict[str, Any]:
        if p['user_id'] == BOT_ID:
            return {'status': 'administrator' if self.rights else 'member', 'can_manage_topics': self.rights}
        status = self.members.get(p['user_id'])
        if status is None:
            raise err(400, 'Bad Request: user not found')
        return {'status': status, 'user': {'id': p['user_id']}}

    def _createForumTopic(self, p: dict[str, Any]) -> dict[str, Any]:
        self._thread += 1
        self.threads.add(self._thread)
        return {'message_thread_id': self._thread, 'name': p['name']}

    def _sendMessage(self, p: dict[str, Any]) -> dict[str, Any]:
        thread = p.get('message_thread_id')
        if thread is not None and thread not in self.threads:
            raise err(400, 'Bad Request: message thread not found')
        self._mid += 1
        self.messages[(str(p['chat_id']), self._mid)] = {**p}
        out: dict[str, Any] = {'message_id': self._mid, 'chat': {'id': int(p['chat_id'])}, 'text': p['text']}
        if thread is not None:
            out.update(message_thread_id=thread, is_topic_message=True)
        return out

    def _editMessageText(self, p: dict[str, Any]) -> dict[str, Any]:
        key = (str(p['chat_id']), p['message_id'])
        if key not in self.messages:
            raise err(400, 'Bad Request: message to edit not found')
        msg = self.messages[key]
        if msg['text'] == p['text'] and msg.get('reply_markup') == p.get('reply_markup'):
            raise err(400, 'Bad Request: message is not modified')
        msg['text'] = p['text']
        if 'reply_markup' in p:
            msg['reply_markup'] = p['reply_markup']
        else:
            msg.pop('reply_markup', None)
        return {'message_id': p['message_id']}

    def _sendLocation(self, p: dict[str, Any]) -> dict[str, Any]:
        self._mid += 1
        return {'message_id': self._mid}

    def _getUpdates(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        out, self.updates = self.updates, []
        return out

    # --- выборки ---

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def sent(self, chat: str | None = None) -> list[dict[str, Any]]:
        return [p for p in self.of('sendMessage') if chat is None or str(p['chat_id']) == chat]

    def texts(self, chat: str | None = None) -> list[str]:
        return [p['text'] for p in self.sent(chat)]

    def text(self, chat: str, mid: int) -> str:
        return self.messages[(chat, mid)]['text']

    def buttons(self, chat: str, mid: int) -> list[str]:
        markup = self.messages[(chat, mid)].get('reply_markup') or {}
        return [b['text'] for row in markup.get('inline_keyboard') or () for b in row]

    def last_mid(self) -> int:
        return self._mid

    def reset(self) -> None:
        self.calls.clear()


SECRET = '123456:SECRET-TOKEN'


class Harness:
    """Бот на подделке Telegram и временной базе «Маршрутов»: box — что видит бот (настройки, карточки, факт, «сейчас»,
    план, строки «Վարորդներ»; live=False — «Առաքիչ» не подключён); clock — монотонные часы (пауза после сбоя)."""

    def __init__(self, tmp_path: Any, cards: Any = None, now: Any = None, settings: Any = None,
                 api: FakeTelegram | None = None, legacy: str | None = None, db: str = 'routes.db'):
        from route_optimizer import live, store as st, tg_bot
        self.box: dict[str, Any] = {'settings': {**st.DEFAULT_SETTINGS, **(settings or {})}, 'cards': cards or {},
                                    'fleet': {}, 'now': now, 'plan': None, 'scores': None, 'live': True}
        self.api = api or FakeTelegram()
        self.clock = [1000.0]
        self.store = st.Store(str(tmp_path / db))
        box = self.box
        self.feeds = tg_bot.Feeds(
            settings=lambda: box['settings'],
            live=lambda day: ((live.Rules.from_settings(box['settings']), box['now'], box['cards'], box['fleet'])
                              if box['live'] else None),
            plan=lambda day: box['plan'], scores=lambda a, b: box['scores'], now=lambda: box['now'])
        self.legacy = legacy
        self.bot = self.restart()

    def restart(self) -> Any:
        """Новый процесс: тот же Telegram и та же база."""
        from route_optimizer import tg_bot
        self.bot = tg_bot.TgBot(self.api, '-100', self.store, self.feeds, SECRET, self.legacy,
                                monotonic=lambda: self.clock[0])
        return self.bot

    def set(self, **kw: Any) -> None:
        self.box.update(kw)

    def tick(self) -> int:
        return self.bot.tick()

    def rec(self, prefix: str) -> Any:
        hits = [r for k, r in self.bot.records.items() if k.startswith(prefix)]
        assert len(hits) == 1, (prefix, list(self.bot.records))
        return hits[0]
