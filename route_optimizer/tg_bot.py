# -*- coding: utf-8 -*-
"""Telegram-бот раздела «Маршруты» — «Araqich Dispatch» (№91, docs/plans/telegram-bot-plan.md; ответы владельца §91).

Решения — в чистых модулях (live_alerts: тревоги, tg_reports: отчёты и команды); здесь — исполнение: Bot API (tg_api),
записи tg_message / tg_kv (store, схема 27), два потока процесса (__init__.start_live_alerts):
- проход tick (каждые INTERVAL_S): тревоги карты (live_alerts.plan) → отправка, правка, ответы «не успеет»; эскалация
  (live_alerts.due_escalations): ответ в группе на исходное сообщение со звуком и упоминанием людей escalate_to + личное
  сообщение каждому с теми же кнопками (403 — человек не нажимал /start у бота — в журнал, рассылку не останавливает;
  очередь Telegram, 429, сеть — копия отложена, как и правка «✔ Տեսավ» и закрепление отчёта: _pending, следующим
  проходом);
  отчёты по событию: план дня — как только план сегодня выпущен водителям (Draft.released), изменили — правка того же
  сообщения со строкой «Թարմացված՝ HH:MM», закреплён; неделя — с первым планом недели (только в первый рабочий день
  недели); итог дня — когда все машины плана вернулись на склад (карточка closed), но не позже tg_summary_at, закреплён
  вместо плана (бот запущен позже tg_summary_at больше чем на SUMMARY_STALE_MIN — итога за день нет). Выходные и
  праздники (настройки №64) — без отчётов. План и неделя ждут конца тихих часов (утром), итог — нет;
  тревога, объяснённая диспетчером или ставшая «փոքր», и записи прошлых дней, ещё «идущие», закрываются правкой;
- приём обновлений poll_once (getUpdates, long poll POLL_TIMEOUT_S, offset — в tg_kv): кнопки и команды /where /today
  /late /help. Доступ: в группе — все; в личке — участники группы (getChatMember, кэш ACCESS_TTL_S) и люди эскалации
  (tg_escalate_to); чужим и другим чатам — молчание. Данные кнопок подписаны (HMAC от токена, короткий id записи):
  подделанные — мимо.
Общее состояние (записи) — под self.lock, взятым на одно действие прохода (не на весь проход); очередь Telegram (1/с,
20/мин, пауза 429) ждётся до замка, под замком вызовы не ждут (tg_api.no_wait); долгие расчёты (карточки флота, план,
«Վարորդներ») — до него, их сбой отчётам не даёт пройти, тревогам — не мешает. Замок не честный: нажатие «Տեսա» может
ждать его несколько действий прохода — поэтому бот отвечает на нажатие сразу (по снимку записей), а запись ставит в
pending_acks до ответа: эскалация такой записи не идёт, пока «Տեսա» ждёт замка, и перепроверяет «Տեսա» под замком. Запись — после удачного вызова Telegram (сбой — повтор), сначала в память, потом в
базу (сбой базы — в журнал, без повтора сообщения). Сообщение, которое Telegram отверг само по себе (400: тема закрыта,
разметка, длина, кнопка), — в журнал и запись failed, проход идёт дальше (tg_api.per_message); к остановке рассылки
ведут только ошибки всего чата (tg_api.chat_wide).

«Տեսա» общая с картой «Մեքենաները առցանց» (live_ack, схема 28 — владелец таблицы страница, бот только её API):
нажата в Telegram — после записи в tg_message отметка карты (Store.live_ack_put: тревога — вид и её начало, «не
успеет» — late:window / late:plan идущих строк без начала), страница перестаёт мигать; нажата на карте — проход
(_sync_map_acks, до эскалации; и перепроверка вплотную к отправке эскалации) находит отметку того же случая
(la.map_ack_match: то же начало, у отклонения ±3 мин; «не успеет» — отметка не раньше начала случая), ставит «✔ Տեսավ
<кто на карте>» и снимает кнопку — эскалации нет. Обратно на карту не пишется — петли нет.

Темы: группа — форум и бот — админ с правом «Управление темами» → при старте создаются недостающие темы (TOPICS), их id —
в tg_kv; тревоги «нет связи»/GPS — в 🔴, «не успеет» — в ⏰, прочие — в 🚚, отчёты — в 📊. Не форум или нет прав — всё в
общий чат; тему удалили — сообщение в общий чат, темы создаются заново. Группа стала супергруппой (400
migrate_to_chat_id) — бот пишет в новый id (запоминает в tg_kv и просит в журнале сменить ROUTES_LIVE_TG_CHAT).

Сбои: сеть и Telegram — проход прерывается, повтор со всё большей паузой (до BACKOFF_MAX_S, 429 — не меньше
retry_after); CLIENT_ERRORS_MAX ошибок 4xx подряд в группу (токен отозван, бота убрали) — рассылка остановлена до
перезапуска или смены настроек тревог и бота, в журнале одна строка. Закрепление без прав — в журнал, не сбой.
«Уже отправлено» прежнего потока (route_live_alerts.json рядом с базой) переносится в tg_message при первом запуске
(phase skipped / active без message_id): после выкладки тревоги не повторяются задним числом. Файл не меняется (откат).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping

from . import actuals as ac
from . import dispatch as dp
from . import live_alerts as la
from . import tg_api
from . import tg_reports as rp
from .live import Rules
from .live_alerts import Rec
from .store import LIVE_ACK_SINCE_TOL
from .tg_api import TelegramError, chat_wide, per_message

logger = logging.getLogger(__name__)

POLL_TIMEOUT_S = 25
POLL_BACKOFF_MAX_S = 300.0
KEEP_DAYS = 8                  # записи бота старше — удаляются (неделя отчёта + запас)
ACCESS_TTL_S = 600.0           # участник группы в личке — проверка getChatMember не чаще
SETUP_RETRY_S = 300.0          # getMe / команды / темы не удались — повтор не раньше
CALLBACK_MAX = 64              # байт в callback_data (Telegram)
SIG_BYTES = 6
NAMES_MAX = 300
LEGACY_KEEP = timedelta(hours=48)   # как прежний STATE_KEEP: старше — не переносится
MEMBER = ('creator', 'administrator', 'member')
TOPICS = (('critical', '🔴 Կրիտիկական'), ('late', '⏰ Ուշացումներ'), ('violations', '🚚 Խախտումներ'),
          ('reports', '📊 Օրվա ամփոփում'))
TOPIC_OF = {'no_contact': 'critical', 'gps': 'critical', 'late': 'late', 'plan': 'reports', 'summary': 'reports',
            'week': 'reports'}   # остальные виды — violations
COMMANDS = [{'command': 'where', 'description': 'Որտեղ է մեքենան (օրինակ՝ /where 35OS355)'},
            {'command': 'today', 'description': 'Այսօրվա առաջընթացը'},
            {'command': 'late', 'description': 'Ով է ուշանում'},
            {'command': 'help', 'description': 'Օգնություն'}]
NO_PREVIEW = {'is_disabled': True}
TEXT_MAX = 4096                # символов в сообщении (Telegram)
MAP_ACKED_BY = 0               # acked_by записи, подтверждённой на карте (Telegram id людей — положительные)
MAP_ACKED_NAME = 'քարտեզ'      # имя, если у отметки карты нет пользователя
SUMMARY_STALE_MIN = 60         # итог дня позже tg_summary_at больше чем на столько (бот запущен поздно) — не шлётся
ACKED = 'Գրանցված է'


@dataclass
class Feeds:
    """Данные раздела (собирает __init__.start_live_alerts): настройки, карточки флота дня (Rules карты, момент расчёта,
    карточки, факт терминалов; None — «Առաքիչ» не подключён), план дня для отчёта (tg_reports.day_plan; None — нет или не
    выпущен), строки «Վարորդներ» за период (None — нет данных), «сейчас» (Ереван)."""
    settings: Callable[[], Mapping[str, Any]]
    live: Callable[[date], tuple[Rules, datetime, Mapping[str, Mapping[str, Any]], Mapping[str, Mapping[str, Any]]] | None]
    plan: Callable[[date], dict[str, Any] | None]
    scores: Callable[[date, date], list[dict[str, Any]] | None]
    now: Callable[[], datetime]


def fit(text: str) -> str:
    """Текст в предел Telegram (TEXT_MAX): лишние строки целиком (каждая строка — законченный HTML) и «…»."""
    if len(text) <= TEXT_MAX:
        return text
    cut = text.rfind('\n', 0, TEXT_MAX - 2)
    if cut <= 0:   # одна огромная строка — простым текстом (обрезанный тег или «&am…» сломали бы разбор HTML)
        plain = html.unescape(re.sub(r'<[^>]*>', '', text))
        n = TEXT_MAX - 1
        while len(html.escape(plain[:n], quote=False)) > TEXT_MAX - 1:
            n -= 64
        return html.escape(plain[:n], quote=False) + '…'
    return text[:cut] + '\n…'


def _map_day(rec: Rec) -> str | None:
    """День отметки карты для записи: тревога — дата её начала по Еревану, «не успеет» — день из ключа."""
    if rec.key.startswith('alert:'):
        t = la._moment(rec.key.split('|', 2)[2])
        return t.astimezone(ac.YEREVAN).date().isoformat() if t is not None else None
    if rec.key.startswith('late:'):
        return rec.key.rsplit('|', 1)[1]
    return None


def _person(user: Mapping[str, Any]) -> str:
    """Имя человека для «✔ Տեսավ …» и упоминаний; одиночные суррогаты — «?» (иначе запись не сохранится в UTF-8)."""
    name = ' '.join(str(user[k]) for k in ('first_name', 'last_name') if user.get(k))
    name = (name or (f'@{user["username"]}' if user.get('username') else f'#{user.get("id")}'))[:64]
    return name.encode('utf-8', 'replace').decode('utf-8')


class TgBot:
    def __init__(self, api: Callable[..., Any], chat: str, store: Any, feeds: Feeds, secret: str,
                 legacy_json: str | None = None, monotonic: Callable[[], float] = time.monotonic):
        self.api = api
        self.chat = str(chat)
        self.store = store
        self.feeds = feeds
        self._key = hashlib.sha256(b'araqich-tg|' + secret.encode('utf-8')).digest()
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.names_lock = threading.Lock()
        # «Տեսա» нажата, ответ дан, а запись ещё ждёт замка: эскалация этих записей не идёт (свой маленький замок)
        self.pending_acks: set[int] = set()
        self.pending_lock = threading.Lock()
        self.me: dict[str, Any] | None = None
        self.ready = False
        self.setup_at: float | None = None
        self.forum = False
        self.topics: dict[str, int] = {}
        self.closed_topics: dict[int, float] = {}   # тема → когда Telegram ответил TOPIC_CLOSED (monotonic)
        self.webhook_cleared = False
        self.failures = 0
        self.retry_at = 0.0
        self.client_errors = 0
        self.halted: tuple[Any, ...] | None = None
        self.access: dict[int, tuple[bool, float]] = {}
        self.pin_warned = False
        self.pruned: date | None = None
        moved = store.tg_kv('chat')   # группа стала супергруппой — её новый id
        if isinstance(moved, dict) and str(moved.get('from')) == self.chat and moved.get('to'):
            self.chat = str(moved['to'])
        now = feeds.now()
        self.records: dict[str, Rec] = {r['key']: Rec(**r) for r in
                                        store.tg_messages((now - timedelta(days=KEEP_DAYS)).isoformat())}
        self.next_id = store.tg_next_id()
        names = store.tg_kv('names')
        self.names: dict[str, str] = names if isinstance(names, dict) else {}
        offset = store.tg_kv('offset')
        self.offset: int | None = offset if isinstance(offset, int) else None
        if legacy_json:
            self._import_legacy(legacy_json, now)

    # --- записи ---

    def _new(self, key: str, kind: str, phase: str, now: datetime, **kw: Any) -> Rec:
        rec = Rec(key=key, kind=kind, phase=phase, sent_at=now.isoformat(), id=self.next_id, **kw)
        self.next_id += 1
        return rec

    def _persist(self, rec: Rec) -> None:
        """Запись — сначала в память (сообщение уже ушло: повтора в этом процессе не будет), потом в базу; сбой базы —
        в журнал (после перезапуска возможен один повтор — лучше, чем дубль на каждом проходе)."""
        if rec.id is None:   # номер — всегда свой (база с AUTOINCREMENT дала бы занятый для кнопок номер)
            rec.id, self.next_id = self.next_id, self.next_id + 1
        self.records[rec.key] = rec
        try:
            self.store.tg_save_message(asdict(rec))
        except Exception:
            logger.exception('[Routes] Telegram-бот: запись %s не сохранена в базу', rec.key)

    def _kv(self, key: str, value: Any) -> None:
        try:
            self.store.tg_set_kv(key, value)
        except Exception:
            logger.exception('[Routes] Telegram-бот: ключ %s не сохранён в базу', key)

    def _prune(self, now: datetime) -> None:
        day = now.astimezone(ac.YEREVAN).date()
        if self.pruned == day:
            return
        before = (now - timedelta(days=KEEP_DAYS)).isoformat()
        try:
            self.store.tg_prune(before)
        except Exception:
            logger.exception('[Routes] Telegram-бот: старые записи не удалены из базы')
        self.records = {k: r for k, r in self.records.items() if r.sent_at >= before}
        self.pruned = day

    def _import_legacy(self, path: str, now: datetime) -> None:
        """«Уже отправлено» прежнего потока (route_live_alerts.json) → записи (правило — в описании модуля). Один раз:
        отметка legacy_json в tg_kv (и когда файла нет)."""
        if self.store.tg_kv('legacy_json') is not None:
            return
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            sent = raw.get('sent') if isinstance(raw, dict) else None
        except FileNotFoundError:
            sent = None
        except (OSError, ValueError):
            logger.warning('[Routes] Telegram-бот: %s не прочитан — «уже отправлено» не перенесено', path)
            sent = None
        keep = now - LEGACY_KEEP
        late: dict[str, Rec] = {}
        count = 0
        for k, v in (sent if isinstance(sent, dict) else {}).items():
            at = la._moment(v.get('at')) if isinstance(v, dict) else None
            if at is None or at < keep:
                continue
            parts = k.split('|')
            start = v.get('start') if isinstance(v.get('start'), str) else None
            # «не успеет» — только 4 части (машина|late|день|цель); 3 части с late — не тревога, мимо
            new_key = (la.late_key(parts[0], parts[2]) if len(parts) == 4 and parts[1] == 'late'
                       else la.alert_key(*parts) if len(parts) == 3 and parts[1] in la.TITLE and parts[1] != 'late'
                       else None)
            if new_key is None or new_key in self.records:   # бот уже вёл эту тревогу — его запись главнее
                continue
            if len(parts) == 4 and parts[1] == 'late':
                car, _, day, target = parts
                rec = late.get(la.late_key(car, day)) or self._new(la.late_key(car, day), 'late', 'active', at, car=car,
                                                                   payload={'lines': {}, 'seen': [], 'legacy': True})
                late[rec.key] = rec
                over = v.get('over') if isinstance(v.get('over'), int) else None
                rec.payload['lines'][target] = {'over': over, 'sent': over, 'kind': None, 'text': '',
                                                'calm': v.get('calm_since') if isinstance(v.get('calm_since'), str) else None}
                rec.payload['seen'] = sorted(set(rec.payload['seen']) | {target})
            else:
                car, kind, begin = parts
                phase = 'skipped' if start is None else ('ended' if v.get('end') else 'active')
                rec = self._new(la.alert_key(car, kind, begin), kind, phase, at, car=car,
                                payload={'legacy': True, **({'why': str(v.get('skipped') or 'migrated')}
                                                            if phase == 'skipped' else {})})
                rec.sent_at = start or rec.sent_at
                self._persist(rec)
                count += 1
        for rec in late.values():
            self._persist(rec)
            count += 1
        self._kv('legacy_json', {'path': path, 'at': now.isoformat(), 'records': count})
        if count:
            logger.info('[Routes] Telegram-бот: из %s перенесено записей «уже отправлено»: %d', path, count)

    # --- подпись кнопок ---

    def sign(self, data: str) -> str | None:
        """Данные кнопки + подпись (HMAC от токена); не помещается в 64 байта — None (кнопки не будет)."""
        sig = base64.urlsafe_b64encode(hmac.new(self._key, data.encode('utf-8'), hashlib.sha256).digest()[:SIG_BYTES])
        out = f'{data}|{sig.decode()}'
        return out if len(out.encode('utf-8')) <= CALLBACK_MAX else None

    def verify(self, raw: Any) -> str | None:
        if not isinstance(raw, str) or '|' not in raw:
            return None
        data = raw.rsplit('|', 1)[0]
        try:   # байты: compare_digest на str не с ASCII бросает TypeError; одиночный суррогат — UnicodeEncodeError
            good = self.sign(data)
            return data if good is not None and hmac.compare_digest(good.encode('utf-8'), raw.encode('utf-8')) else None
        except UnicodeEncodeError:
            return None

    # --- Telegram ---

    def _thread(self, kind: str) -> int | None:
        """Тема вида; закрытая (TOPIC_CLOSED) — SETUP_RETRY_S сразу общий чат, без лишнего вызова и строки журнала."""
        thread = self.topics.get(TOPIC_OF.get(kind, 'violations')) if self.forum else None
        closed = self.closed_topics.get(thread) if thread is not None else None
        if closed is not None and self.monotonic() - closed < SETUP_RETRY_S:
            return None
        return thread

    def _migrated(self, new_id: int) -> None:
        logger.warning('[Routes] Telegram-бот: группа стала супергруппой, новый id %s — бот пишет туда; впишите его в '
                       'ROUTES_LIVE_TG_CHAT', new_id)
        self._kv('chat', {'from': self.chat, 'to': str(new_id)})
        self.chat = str(new_id)
        self.ready, self.setup_at, self.forum, self.topics = False, None, False, {}

    def _send(self, chat: str, text: str, *, thread: int | None = None, markup: Mapping[str, Any] | None = None,
              silent: bool = False, reply_to: int | None = None) -> dict[str, Any]:
        """sendMessage HTML без превью ссылок; группа стала супергруппой — в новый id; темы нет — в общий чат."""
        params: dict[str, Any] = {'chat_id': chat, 'text': fit(text), 'parse_mode': 'HTML', 'link_preview_options': NO_PREVIEW,
                                  'message_thread_id': thread, 'reply_markup': markup,
                                  'disable_notification': True if silent else None,
                                  'reply_parameters': ({'message_id': reply_to, 'allow_sending_without_reply': True}
                                                       if reply_to else None)}
        try:
            return self.api('sendMessage', **params)
        except TelegramError as e:
            if e.migrate_to is not None:   # группа стала супергруппой (и запись, отправленная ещё в прежнюю, — туда же)
                if chat == self.chat:
                    self._migrated(e.migrate_to)
                return self.api('sendMessage', **{**params, 'chat_id': str(e.migrate_to), 'message_thread_id': None})
            if thread is not None and e.status == 400 and 'topic_closed' in e.description.lower():
                logger.warning('[Routes] Telegram-бот: тема %s закрыта — сообщения в общий чат (откройте тему)', thread)
                self.closed_topics[thread] = self.monotonic()
                return self.api('sendMessage', **{**params, 'message_thread_id': None})
            if thread is not None and e.status == 400 and 'thread not found' in e.description.lower():
                logger.warning('[Routes] Telegram-бот: тема %s не найдена — сообщение в общий чат, тему создам заново',
                               thread)
                gone = [k for k, v in self.topics.items() if v == thread]   # только удалённая; остальные — как были
                for k in gone:
                    del self.topics[k]
                if gone:
                    self.ready, self.setup_at = False, None
                    self._kv('topics', {'chat': self.chat, 'ids': self.topics})
                return self.api('sendMessage', **{**params, 'message_thread_id': None})
            raise

    def _post(self, rec: Rec, silent: bool, reply_to: int | None = None) -> None:
        """Отправить сообщение записи в группу (тема — по виду); chat / message_id / thread_id — в запись."""
        msg = self._send(self.chat, la.render(rec), thread=self._thread(rec.kind), markup=la.keyboard(rec, self.sign),
                         silent=silent, reply_to=reply_to)
        # ушло — дальше ничего не бросает: странный ответ — запись без номера сообщения (её кнопки — та же запись)
        msg = msg if isinstance(msg, dict) else {}
        mid = msg.get('message_id')
        rec.chat, rec.message_id = self.chat, mid if isinstance(mid, int) and not isinstance(mid, bool) else None
        thread = msg.get('message_thread_id')
        rec.thread_id = thread if msg.get('is_topic_message') and isinstance(thread, int) else None
        self.failures = self.client_errors = 0

    def _edit(self, rec: Rec) -> bool:
        """Правка сообщения записи (и личных копий эскалации) по её состоянию; False — править нечего или нельзя
        (сообщения нет, удалено, старше 48 ч)."""
        if rec.message_id is None or rec.chat is None:
            return False
        text, markup = fit(la.render(rec)), la.keyboard(rec, self.sign)
        try:
            self.api('editMessageText', chat_id=rec.chat, message_id=rec.message_id, text=text, parse_mode='HTML',
                     link_preview_options=NO_PREVIEW, reply_markup=markup)
        except TelegramError as e:
            d = e.description.lower()
            if e.status == 400 and 'not modified' in d:
                return True
            if e.status == 400 and ('not found' in d or "can't be edited" in d or 'message_id_invalid' in d
                                    or e.migrate_to is not None):   # прежняя группа (стала супергруппой) — не править
                logger.info('[Routes] Telegram-бот: сообщение %s не править (%s)', rec.key, e)
                return False
            raise
        self.failures = self.client_errors = 0
        for chat, mid in rec.payload.get('copies') or ():
            try:
                self.api('editMessageText', chat_id=chat, message_id=mid, text=text, parse_mode='HTML',
                         link_preview_options=NO_PREVIEW, reply_markup=markup)
            except TelegramError as e:   # личная копия — не важнее группы
                logger.info('[Routes] Telegram-бот: копия %s у %s не исправлена (%s)', rec.key, chat, e)
        return True

    def _pin(self, rec: Rec, unpin: Rec | None = None) -> None:
        """Закрепить сообщение отчёта без звука (и открепить прежнее). Нет прав — одна строка в журнале, не сбой;
        очередь, 429, сеть — отметка pin в записи (снять закрепление — unpin), повтор следующим проходом (_pending)."""
        target = unpin.key if unpin is not None and unpin.message_id is not None else None
        try:
            if target is not None:
                self.api('unpinChatMessage', chat_id=unpin.chat, message_id=unpin.message_id)   # type: ignore[union-attr]
                target = None   # откреплено — повтор (если закрепить не дала очередь) больше не открепляет
            self.api('pinChatMessage', chat_id=rec.chat, message_id=rec.message_id, disable_notification=True)
            rec.payload.pop('pin', None)
        except TelegramError as e:
            if tg_api.throttled(e):
                rec.payload['pin'] = {'unpin': target}
                return
            rec.payload.pop('pin', None)
            if not self.pin_warned:
                logger.warning('[Routes] Telegram-бот: не закреплено (%s) — нужны права администратора «Закреплять '
                               'сообщения»', e)
                self.pin_warned = True

    def _failed(self, e: TelegramError, sig: tuple[Any, ...]) -> None:
        """Сбой отправки в группу: пауза с ростом (429 — не меньше retry_after) или остановка после CLIENT_ERRORS_MAX
        ошибок всего чата подряд (tg_api.chat_wide: токен, бота убрали, чата нет; ошибка одного сообщения сюда не
        попадает — _rejected). Своя очередь (e.local: под замком ждать нельзя) — не сбой: пауза ровно retry_after, без
        счёта сбоев и роста паузы."""
        if e.local:
            self.retry_at = self.monotonic() + (e.retry_after or la.INTERVAL_S)
            logger.info('[Routes] Telegram-бот: очередь Telegram занята — продолжу через %.0f с', e.retry_after or 0)
            return
        self.failures += 1
        self.client_errors = self.client_errors + 1 if chat_wide(e) else 0
        if self.client_errors >= la.CLIENT_ERRORS_MAX:
            self.halted = sig
            logger.error('[Routes] Тревоги в Telegram ОСТАНОВЛЕНЫ: %d ошибок %s подряд (токен или чат неверны, бота '
                         'убрали из группы?). Исправьте .env и перезапустите сервер или смените настройки тревог',
                         self.client_errors, e)
            return
        pause = min(la.INTERVAL_S * 2 ** self.failures, la.BACKOFF_MAX_S)
        if e.retry_after:
            pause = max(pause, e.retry_after)
        self.retry_at = self.monotonic() + pause
        logger.warning('[Routes] Тревоги в Telegram: не отправлено (%s), повтор через %.0f с', e, pause)

    # --- настройка: команды, темы ---

    def _ensure_me(self) -> dict[str, Any]:
        if self.me is None:
            self.me = self.api('getMe')
        return self.me

    def setup(self) -> None:
        """getMe, setMyCommands, темы форума. Сбой — в журнал, повтор не раньше SETUP_RETRY_S (тревоги идут и без них)."""
        if self.ready or (self.setup_at is not None and self.monotonic() - self.setup_at < SETUP_RETRY_S):
            return
        self.setup_at = self.monotonic()
        try:
            me = self._ensure_me()
            self.api('setMyCommands', commands=COMMANDS)
            self._setup_topics(me)
            self.ready = True
        except TelegramError as e:
            logger.warning('[Routes] Telegram-бот: настройка не удалась (%s) — повтор через %.0f мин', e,
                           SETUP_RETRY_S / 60)
        except Exception:   # битый ответ Telegram, сбой базы — тревоги идут и без тем
            logger.exception('[Routes] Telegram-бот: настройка не удалась — повтор через %.0f мин', SETUP_RETRY_S / 60)

    def _setup_topics(self, me: Mapping[str, Any]) -> None:
        chat = self.api('getChat', chat_id=self.chat) or {}
        self.forum = bool(chat.get('is_forum'))
        saved = self.store.tg_kv('topics')
        ids = saved.get('ids') if isinstance(saved, dict) and str(saved.get('chat')) == self.chat else None
        self.topics = {k: v for k, v in (ids or {}).items() if isinstance(v, int)} if self.forum else {}
        if not self.forum:
            return
        missing = [(k, name) for k, name in TOPICS if k not in self.topics]
        if not missing:
            return
        member = self.api('getChatMember', chat_id=self.chat, user_id=me.get('id')) or {}
        if not (member.get('status') == 'creator'
                or (member.get('status') == 'administrator' and member.get('can_manage_topics'))):
            logger.info('[Routes] Telegram-бот: группа — форум, но у бота нет права «Управление темами» — сообщения в '
                        'общий чат')
            if not self.topics:
                self.forum = False
            return
        for k, name in missing:   # каждая созданная — сразу в базу: сбой на следующей не создаст эту второй раз
            self.topics[k] = int(self.api('createForumTopic', chat_id=self.chat, name=name)['message_thread_id'])
            self._kv('topics', {'chat': self.chat, 'ids': self.topics})

    # --- проход: тревоги, эскалация, отчёты ---

    def tick(self) -> int:
        """Один проход → сколько сообщений отправлено и исправлено. Пауза после сбоя — до retry_at."""
        if self.monotonic() < self.retry_at:
            return 0
        settings = self.feeds.settings()
        rules, tg = Rules.from_settings(settings), la.TgRules.from_settings(settings)
        sig = (rules.alert_kinds, rules.quiet, rules.repeat_min, tg)
        if self.halted is not None:
            if self.halted == sig:
                return 0
            self.halted, self.client_errors = None, 0   # настройки тревог сменили — пробуем снова
        self.setup()
        now = self.feeds.now()
        day = now.astimezone(ac.YEREVAN).date()
        live = self.feeds.live(day)
        cards: Mapping[str, Mapping[str, Any]] = {}
        fleet: Mapping[str, Mapping[str, Any]] = {}
        if live is not None:
            rules, now, cards, fleet = live
        try:   # долгие расчёты — вне замка; сбой данных отчётов (база, план) не мешает тревогам
            inputs = self._report_inputs(day, now, settings, rules, tg, cards)
        except Exception:
            logger.exception('[Routes] Telegram-бот: данные отчётов не прочитаны — тревоги идут без отчётов')
            inputs = None
        done = 0
        quiet = la.in_quiet(rules, now)
        # замок — на одно действие, не на весь проход; очередь Telegram (1/с, 20/мин, 429) ждётся до замка (_idle), под
        # замком вызовы не ждут (tg_api.no_wait: долгое ожидание — сбой с retry_after, пауза прохода)
        with self.lock:
            self._prune(now)
            p = la.plan(cards, rules, tg, now, self.records)   # без «Առաքիչ» — только закрытие прошлых дней
        try:
            for act in p.actions:
                self._idle()
                with self.lock, tg_api.no_wait():
                    done += self._guard(act.key, lambda act=act: self._apply(act, cards, now, quiet), act, now)
            done += self._sync_map_acks()   # «Տեսա» карты — до эскалации: подтверждённое на карте не поднимается
            with self.lock:
                due = la.due_escalations(self.records, p.active, tg, now, quiet) if live is not None else []
            for rec in due:
                self._idle()
                with self.lock, tg_api.no_wait():
                    done += self._guard(rec.key, lambda rec=rec: self._escalate(rec.key, tg, now), None, now)
            with self.lock:   # отложенное из-за очереди Telegram: правка «Տեսա», личные копии, закрепление
                pending = [k for k, r in self.records.items() if r.payload.get('ack_edit')
                           or r.payload.get('copies_pending') or r.payload.get('pin')]
            for key in pending:
                self._idle()
                with self.lock, tg_api.no_wait():
                    done += self._pending(key)
            if inputs is not None:
                self._idle()
                with self.lock, tg_api.no_wait():
                    done += self._reports(inputs, day, now, rules, tg, cards, fleet)
        except TelegramError as e:   # сеть, Telegram, ошибка всего чата: повтор позже (несделанное не записано)
            self._failed(e, sig)
        return done

    def _pending(self, key: str) -> int:
        """Доделать отложенное очередью Telegram (429, своя очередь, сеть): правку «✔ Տեսավ» (ack_edit), личные копии
        эскалации (copies_pending), закрепление отчёта (pin). Ограничение частоты — наверх (пауза прохода, отметки
        остаются); отказ именно этого сообщения — отметка снимается, в журнал."""
        rec = self.records.get(key)
        if rec is None:
            return 0
        try:
            return self._pending_rec(key, rec)
        except TelegramError:
            raise
        except Exception:   # битая запись (текст не строится): отметки снять — иначе каждый проход без отчётов
            logger.exception('[Routes] Telegram-бот: отложенное %s не доделано — отметки сняты', key)
            for flag in ('ack_edit', 'copies_pending', 'pin'):
                rec.payload.pop(flag, None)
            self._persist(rec)
            return 0

    def _pending_rec(self, key: str, rec: Rec) -> int:
        done = 0
        if rec.payload.get('ack_edit'):
            try:
                done += int(self._edit(rec))
            except TelegramError as e:
                if tg_api.throttled(e):
                    raise
                logger.warning('[Routes] Telegram-бот: «Տեսա» %s так и не показана (%s)', key, e)
            rec.payload.pop('ack_edit', None)
            self._persist(rec)
        if rec.payload.get('copies_pending'):
            done += self._send_copies(rec)
        if rec.payload.get('pin'):
            self._pin(rec, unpin=self.records.get(str(rec.payload['pin'].get('unpin') or '')))
            self._persist(rec)
        return done

    def _idle(self) -> None:
        """Подождать место в очереди группы до замка (у подделки Telegram очереди нет)."""
        limiter = getattr(self.api, 'limiter', None)
        if limiter is not None:
            limiter.idle(self.chat)

    def _guard(self, key: str, run: Callable[[], int], act: Any, now: datetime, sig: str | None = None) -> int:
        """Одно действие: Telegram отверг именно это сообщение (tg_api.per_message: тема закрыта, разметка, длина,
        кнопка) — в журнал, запись помечается (_rejected), проход идёт дальше; прочие сбои — наверх (_failed).
        act None — эскалация записи key; act — строка вида отчёта ('plan' | 'week' | 'summary') — отчёт."""
        try:
            return run()
        except TelegramError as e:
            if not per_message(e):
                raise
            logger.warning('[Routes] Telegram-бот: сообщение %s отклонено Telegram (%s) — пропущено', key, e)
            self._rejected(key, act, e, now, sig)
            return 0
        except Exception as e:   # битые данные одного действия не останавливают остальные (и не повторяются)
            logger.exception('[Routes] Telegram-бот: действие %s не выполнено — пропущено', key)
            self._rejected(key, act, TelegramError(type(e).__name__, description=type(e).__name__), now, sig)
            return 0

    def _rejected(self, key: str, act: Any, e: TelegramError, now: datetime, sig: str | None = None) -> None:
        """Отметить отвергнутое, чтобы не повторять его на каждом проходе: новая тревога или отчёт — запись failed;
        окончание — запись кончилась (без правки); «не успеет» — строки записаны (новое сообщение — при ухудшении);
        эскалация — считается сделанной."""
        why = {'why': 'rejected', 'error': e.description[:200]}
        old = self.records.get(key)
        if isinstance(act, la.End) and old is not None:
            self._persist(replace(old, phase='ended', resolved_at=act.at,
                                  payload={**old.payload, 'minutes': act.minutes, **why,
                                           **({'end_reason': act.reason} if act.reason else {})}))
        elif isinstance(act, la.Late):
            base = {**(old.payload if old else {}), 'lines': act.lines, 'seen': list(act.seen), **why}
            self._persist(replace(old, payload=base) if old is not None else
                          self._new(key, 'late', 'failed', now, car=act.car, level=act.level, payload=base))
        elif act is None and old is not None:
            self._persist(replace(old, escalated_at=now.isoformat(), payload={**old.payload, 'escalation': why}))
        elif isinstance(act, (la.Send, la.Skip)):
            self._persist(self._new(key, act.kind, 'failed', now, car=act.car, payload=why))
        elif isinstance(act, str):   # отчёт: нет записи — failed; есть — отметка и подпись (до изменения не повторять)
            extra = {**why, **({'sig': sig} if sig else {})}
            self._persist(self._new(key, act, 'failed', now, payload=extra) if old is None
                          else replace(old, payload={**old.payload, **extra}))

    def _apply(self, act: la.Action, cards: Mapping[str, Mapping[str, Any]], now: datetime, quiet: bool) -> int:
        if isinstance(act, la.Skip):
            self._persist(self._new(act.key, act.kind, 'skipped', now, car=act.car, payload={'why': act.why}))
            return 0
        if isinstance(act, la.Send):
            rec = self._new(act.key, act.kind, 'ended' if act.ended else 'active', now, car=act.car, level=act.level,
                            resolved_at=act.resolved_at,
                            payload={'body': act.body, 'map': act.map,
                                     **({'minutes': act.minutes} if act.ended else {})})
            self._post(rec, silent=act.level == 'info')
            self._persist(rec)
            return 1
        if isinstance(act, la.End):
            old = self.records[act.key]
            rec = replace(old, phase='ended', resolved_at=act.at,
                          payload={**old.payload, 'minutes': act.minutes,
                                   **({'end_reason': act.reason} if act.reason else {})})
            done = int(self._edit(rec))
            # сообщения нет (перенесено из прежнего потока — только «нет связи»/GPS) или правка невозможна — новое ⚪;
            # закрытая не концом тревоги (объяснена, «փոքր», прошлый день) — только правка
            if not done and not quiet and not act.reason and (old.message_id is not None
                                                               or act.kind in la.LEGACY_END_KINDS):
                self._send(self.chat, act.body, thread=self._thread(act.kind), silent=True)
                done = 1
            self._persist(rec)
            return done
        assert isinstance(act, la.Late)
        return self._apply_late(act, cards, now)

    def _apply_late(self, act: la.Late, cards: Mapping[str, Mapping[str, Any]], now: datetime) -> int:
        old = self.records.get(act.key)
        url, _ = la.map_url(cards.get(act.car) or {}, {})
        base = {'lines': act.lines, 'seen': list(act.seen)}
        if old is None:   # первое «не успеет» машины за день
            rec = self._new(act.key, 'late', 'active', now, car=act.car, level=act.level,
                            payload={**base, 'body': act.body, 'map': url, 'edited': now.isoformat()})
            self._post(rec, silent=act.level == 'info')
            self._persist(rec)
            return 1
        rec = replace(old, payload={**old.payload, **base})
        if act.op == 'state':
            self._persist(rec)
            return 0
        if act.op == 'end':
            first = la._moment(old.sent_at)
            rec.phase, rec.resolved_at = 'ended', now.isoformat()
            rec.payload['minutes'] = round((now - first).total_seconds() / 60) if first is not None else None
            done = int(self._edit(rec))
            self._persist(rec)
            return done
        if rec.phase == 'ended':   # снова опаздывает — новый случай в том же сообщении (и отсчёт эскалации — заново)
            rec.phase, rec.resolved_at, rec.acked_by, rec.acked_name, rec.acked_at = 'active', None, None, None, None
            rec.escalated_at, rec.sent_at = None, now.isoformat()
            rec.payload.pop('minutes', None)
            rec.payload.pop('critical_at', None)
        if act.level == 'critical' and old.level != 'critical':   # стало 🔴 (окно, возврат) — эскалация отсюда
            rec.payload['critical_at'] = now.isoformat()
        rec.level = act.level
        rec.payload.update(body=act.body, map=url or old.payload.get('map'), edited=now.isoformat(),
                           updated=now.astimezone(ac.YEREVAN).strftime('%H:%M'))
        done = int(self._edit(rec))
        if act.notify:
            if not done:   # править нечего (перенесено, удалено, отвергнуто) — новое сообщение вместо прежнего
                rec.payload.pop('updated', None)
                rec.payload.pop('critical_at', None)
                # группа видит его впервые — отсчёт эскалации отсюда, прежнее «Տեսա» и эскалация не в счёт
                rec.phase, rec.sent_at, rec.escalated_at = 'active', now.isoformat(), None
                rec.acked_by = rec.acked_name = rec.acked_at = None
                self._post(rec, silent=rec.level == 'info')
                done = 1
            elif rec.level != 'info':   # ухудшилось на шаг — короткий ответ со звуком
                card = cards.get(act.car) or {}
                text = (f'⏰ <b>{la.esc(act.car)}</b>' + (f' · {la.esc(card["driver"])}' if card.get('driver') else '')
                        + ' — ուշացումը մեծացավ\n' + '\n'.join(act.notify))
                try:
                    self._send(rec.chat or self.chat, text, thread=rec.thread_id, reply_to=rec.message_id)
                    done += 1
                except TelegramError as e:   # ответ отвергнут — правка уже сделана: её состояние записать
                    if not per_message(e):
                        raise
                    logger.warning('[Routes] Telegram-бот: ответ «не успеет» %s отклонён (%s)', act.key, e)
        self._persist(rec)
        return done

    def _escalate(self, key: str, tg: la.TgRules, now: datetime) -> int:
        """Эскалация 🔴 (один раз): ответ в группе со звуком и упоминанием + личные копии с кнопками. Запись берётся
        заново под замком: «Տեսա», нажатую между решением и отправкой, эскалация не перекрывает."""
        rec = self.records.get(key)
        if rec is None or rec.escalated_at is not None or rec.phase != 'active' or self._acked(rec):
            return 0
        n = int(tg.escalate_min)
        mention = ', '.join(f'<a href="tg://user?id={uid}">{la.esc(self.names.get(str(uid)) or "ղեկավար")}</a>'
                            for uid in tg.escalate_to)
        what = f'{la.EMOJI["critical"]} {la.esc(la.TITLE.get(rec.kind, rec.kind))} · <b>{la.esc(rec.car or "")}</b>'
        text = f'❗ <b>{n} րոպե առանց պատասխանի</b>\n{what}' + (f'\n{mention}' if mention else '')
        if self._acked(rec):   # перепроверка вплотную к отправке: «Տեսա» могла прийти, пока собирали текст
            return 0
        row = self._map_row(rec)   # и на карте: «Տեսա» диспетчера между синхронизацией и этой отправкой
        if row is not None:
            return self._from_map(rec, row)
        sent = 1
        try:
            self._send(rec.chat or self.chat, text, thread=rec.thread_id, reply_to=rec.message_id)
        except TelegramError as e:   # ответ в группе отвергнут — личные копии всё равно (ради них эскалация)
            if not per_message(e):
                raise
            logger.warning('[Routes] Telegram-бот: эскалация %s в группе отклонена (%s) — только лично', rec.key, e)
            sent = 0
        # ответ в группе ушёл — повторов не будет; личные — списком «кому ещё» (очередь, 429, сеть — следующим проходом)
        rec = replace(rec, escalated_at=now.isoformat(), payload={**rec.payload, 'esc_min': n,
                                                                  'copies_pending': [str(u) for u in tg.escalate_to]})
        self._persist(rec)
        return sent + self._send_copies(rec)

    def _acked(self, rec: Rec) -> bool:
        """«Տեսա» записана или нажата и ждёт записи (pending_acks)."""
        if rec.acked_by is not None:
            return True
        with self.pending_lock:
            return rec.id in self.pending_acks

    def _send_copies(self, rec: Rec) -> int:
        """Личные копии эскалации тем, кому ещё не ушли (payload.copies_pending). 403 — человек не нажимал /start у бота
        (больше не пробуем, в журнал); ограничение частоты и сеть — остаётся в списке, повтор следующим проходом;
        прочие отказы — в журнал. Запись подтвердили — копий больше не шлём."""
        n = rec.payload.get('esc_min', 10)
        sent = 0
        for uid in list(rec.payload.get('copies_pending') or ()):
            if self._acked(rec):
                rec.payload['copies_pending'] = []
                break
            try:
                msg = self._send(uid, la.render(rec) + f'\n❗ {n} րոպե առանց պատասխանի',
                                 markup=la.keyboard(rec, self.sign))
            except TelegramError as e:
                if tg_api.throttled(e):
                    break   # остальные — следующим проходом
                rec.payload['copies_pending'].remove(uid)
                # 403 — бот заблокирован или не начат; 400 «chat not found» — человек ни разу не открывал бота
                if e.status == 403 or (e.status == 400 and 'chat not found' in e.description.lower()):
                    logger.warning('[Routes] Telegram-бот: эскалация %s лично %s не доставлена (%s) — человеку нужно '
                                   'открыть бота и нажать /start', rec.key, uid, e)
                else:
                    logger.warning('[Routes] Telegram-бот: эскалация %s лично %s отклонена (%s)', rec.key, uid, e)
                continue
            msg = msg if isinstance(msg, dict) else {}
            rec.payload['copies_pending'].remove(uid)
            rec.payload['copies'] = [*(rec.payload.get('copies') or []), [uid, msg.get('message_id')]]
            sent += 1
        if not rec.payload.get('copies_pending'):
            rec.payload.pop('copies_pending', None)
        self._persist(rec)
        return sent

    def _report_inputs(self, day: date, now: datetime, settings: Mapping[str, Any], rules: Rules, tg: la.TgRules,
                       cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
        """Данные отчётов — до замка (план из базы, «Վարորդներ» — долгий расчёт): None — отчётов сегодня нет (выключены,
        выходной или праздник, плана нет). week / day — строки «Վարորդներ», только если отчёт пора слать (ключ есть —
        посчитано, значение None — данных нет)."""
        if not (tg.report_plan or tg.report_summary or tg.report_week):
            return None
        if not dp.is_workday(day, settings.get('workdays') or (), dp.holidays_of(settings)):
            return None
        plan = self.feeds.plan(day)
        if plan is None or not plan.get('cars'):
            return None
        ds = day.isoformat()
        out: dict[str, Any] = {'plan': plan, 'quiet': la.in_quiet(rules, now)}
        monday = day - timedelta(days=day.weekday())
        workdays, off = settings.get('workdays') or (), dp.holidays_of(settings)
        first = next(d for d in (monday + timedelta(days=i) for i in range(7)) if d == day or dp.is_workday(d, workdays, off))
        # неделя — только в первый рабочий день недели: бот, запущенный в среду, не шлёт прошлую неделю
        if (tg.report_week and not out['quiet'] and day == first
                and f'week:{monday.isoformat()}' not in self.records):
            prev = monday - timedelta(days=7)
            out['week'] = self.feeds.scores(prev, prev + timedelta(days=6))
        local = now.astimezone(ac.YEREVAN)
        minute = local.hour * 60 + local.minute
        back = all((cards.get(c['car']) or {}).get('closed') for c in plan['cars'])
        if tg.report_summary and f'summary:{ds}' not in self.records and (back or minute >= tg.summary_at):
            if minute >= tg.summary_at + SUMMARY_STALE_MIN:   # первый запуск поздно вечером — итог уже не новость
                out['stale'] = True
            else:
                out['day'] = self.feeds.scores(day, day)
        return out

    def _reports(self, inputs: Mapping[str, Any], day: date, now: datetime, rules: Rules, tg: la.TgRules,
                 cards: Mapping[str, Mapping[str, Any]], fleet: Mapping[str, Mapping[str, Any]]) -> int:
        plan, quiet, ds = inputs['plan'], inputs['quiet'], day.isoformat()
        done = 0
        if tg.report_plan:
            try:   # подпись — как в _plan_report: отвергнутый план не повторяется до его изменения
                sig: str | None = hashlib.sha256(rp.plan_text(plan).encode('utf-8')).hexdigest()[:16]
            except Exception:
                sig = None
            done += self._guard(f'plan:{ds}', lambda: self._plan_report(plan, ds, now, quiet), 'plan', now, sig)
        if 'week' in inputs and (not tg.report_plan or f'plan:{ds}' in self.records):
            monday = day - timedelta(days=day.weekday())
            done += self._guard(f'week:{monday.isoformat()}', lambda: self._week_report(monday, inputs['week'], now),
                                'week', now)
        if inputs.get('stale'):
            self._persist(self._new(f'summary:{ds}', 'summary', 'skipped', now, payload={'why': 'stale'}))
        if 'day' in inputs:
            done += self._guard(f'summary:{ds}', lambda: self._summary_report(plan, ds, now, cards, fleet, inputs['day']),
                                'summary', now)
        return done

    def _report(self, key: str, kind: str, text: str, now: datetime, pin: bool, **payload: Any) -> Rec:
        rec = self._new(key, kind, 'report', now, payload={'body': text, **payload})
        self._post(rec, silent=False)
        self._persist(rec)
        if pin:
            self._pin(rec, unpin=self.records.get(f'plan:{key.split(":", 1)[1]}') if kind == 'summary' else None)
            if rec.payload.get('pin'):   # не закрепилось из-за очереди — повтор следующим проходом
                self._persist(rec)
        return rec

    def _plan_report(self, plan: Mapping[str, Any], ds: str, now: datetime, quiet: bool) -> int:
        text = rp.plan_text(plan)
        sig = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        old = self.records.get(f'plan:{ds}')
        if old is None or old.phase == 'failed':   # ещё не отправлен (или Telegram отверг прежний текст плана)
            if quiet or (old is not None and old.payload.get('sig') == sig):
                return 0
            if old is not None:
                del self.records[old.key]
            try:
                self._report(f'plan:{ds}', 'plan', text, now, True, sig=sig)
            except TelegramError as e:
                if not per_message(e):
                    raise
                logger.warning('[Routes] Telegram-бот: план %s отклонён Telegram (%s) — до изменения плана', ds, e)
                self._persist(self._new(f'plan:{ds}', 'plan', 'failed', now, payload={'sig': sig, 'why': 'rejected'}))
                return 0
            return 1
        if old.payload.get('sig') == sig or f'summary:{ds}' in self.records:
            return 0
        rec = replace(old, payload={**old.payload, 'body': text, 'sig': sig,
                                    'updated': now.astimezone(ac.YEREVAN).strftime('%H:%M')})
        try:
            edited = self._edit(rec)
        except TelegramError as e:
            if not per_message(e):
                raise
            logger.warning('[Routes] Telegram-бот: правка плана %s отклонена (%s) — до следующего изменения плана', ds, e)
            self._persist(replace(old, payload={**old.payload, 'sig': sig}))
            return 0
        if not edited:   # удалили — новое сообщение
            rec.payload.pop('updated')
            try:
                self._post(rec, silent=True)
            except TelegramError as e:
                if not per_message(e):
                    raise
                logger.warning('[Routes] Telegram-бот: план %s заново не отправлен (%s) — до изменения плана', ds, e)
                self._persist(replace(old, payload={**old.payload, 'sig': sig}))
                return 0
            self._pin(rec)
        self._persist(rec)
        return 1

    def _week_report(self, monday: date, rows: list[dict[str, Any]] | None, now: datetime) -> int:
        prev = monday - timedelta(days=7)
        key = f'week:{monday.isoformat()}'
        if rows is None:   # «Վարորդներ» не подключены или расчёт не удался — на этой неделе без рейтинга
            self._persist(self._new(key, 'week', 'skipped', now, payload={'why': 'no-data'}))
            return 0
        self._report(key, 'week', rp.week_text(prev, rows), now, False)
        return 1

    def _summary_report(self, plan: Mapping[str, Any], ds: str, now: datetime, cards: Mapping[str, Mapping[str, Any]],
                        fleet: Mapping[str, Mapping[str, Any]], scores: list[dict[str, Any]] | None) -> int:
        """Итог дня (пора — решено в _report_inputs: все машины плана вернулись или tg_summary_at)."""
        if f'summary:{ds}' in self.records:
            return 0
        late = {r.car: len(r.payload.get('seen') or ()) for r in self.records.values()
                if r.key.startswith('late:') and r.key.endswith(f'|{ds}') and r.car}
        rows = rp.summary_data(plan, cards, fleet, late, scores)
        self._report(f'summary:{ds}', 'summary', rp.summary_text(ds, rows), now, True)
        return 1

    # --- приём обновлений: кнопки и команды ---

    def poll_once(self, timeout: int = POLL_TIMEOUT_S) -> int:
        """Один getUpdates (long poll) и обработка → сколько обновлений. Сбой Telegram — исключение (поток ждёт)."""
        if not self.webhook_cleared:
            self.api('deleteWebhook', drop_pending_updates=False)
            self.webhook_cleared = True
        updates = self.api('getUpdates', offset=self.offset, timeout=timeout,
                           allowed_updates=['message', 'callback_query']) or []
        for u in updates:
            try:
                self.handle(u)
            except TelegramError as e:
                logger.warning('[Routes] Telegram-бот: ответ не отправлен (%s)', e)
            except Exception:   # одно битое обновление не останавливает приём
                logger.exception('[Routes] Telegram-бот: обновление %s не обработано', u.get('update_id'))
            if isinstance(u.get('update_id'), int):
                self.offset = u['update_id'] + 1
        if updates:
            self._kv('offset', self.offset)
        return len(updates)

    def handle(self, u: Mapping[str, Any]) -> None:
        if isinstance(u.get('callback_query'), Mapping):
            self._on_callback(u['callback_query'])
        elif isinstance(u.get('message'), Mapping):
            self._on_message(u['message'])

    def _member(self, uid: Any) -> bool:
        """Участник группы (для лички): getChatMember, кэш ACCESS_TTL_S; ответ «нет» (400/403) — тоже в кэш."""
        if not isinstance(uid, int):
            return False
        hit = self.access.get(uid)
        if hit is not None and self.monotonic() - hit[1] < ACCESS_TTL_S:
            return hit[0]
        try:
            m = self.api('getChatMember', chat_id=self.chat, user_id=uid) or {}
            ok = m.get('status') in MEMBER or (m.get('status') == 'restricted' and m.get('is_member') is True)
        except TelegramError as e:
            if e.status not in (400, 403):
                raise
            ok = False
        self.access[uid] = (ok, self.monotonic())
        return ok

    def _allowed(self, chat: Mapping[str, Any], uid: Any) -> bool:
        """В группе — все; в личке — участники группы и люди эскалации (tg_escalate_to: копия с «Տեսա» приходит им
        лично, даже если в группе их нет); прочие чаты — нет."""
        if str(chat.get('id')) == self.chat:
            return True
        if chat.get('type') != 'private':
            return False
        return uid in la.TgRules.from_settings(self.feeds.settings()).escalate_to or self._member(uid)

    def _remember(self, user: Mapping[str, Any]) -> None:
        uid, name = user.get('id'), _person(user)
        if isinstance(uid, int) and self.names.get(str(uid)) != name:
            with self.names_lock:   # свой замок: имя не ждёт прохода тревог
                self.names[str(uid)] = name
                while len(self.names) > NAMES_MAX:
                    self.names.pop(next(iter(self.names)))
                self._kv('names', self.names)

    def _cards(self) -> Mapping[str, Mapping[str, Any]]:
        now = self.feeds.now()
        live = self.feeds.live(now.astimezone(ac.YEREVAN).date())
        return live[2] if live is not None else {}

    def _on_message(self, m: Mapping[str, Any]) -> None:
        text = m.get('text')
        if not isinstance(text, str) or not text.startswith('/'):
            return
        head, _, arg = text.partition(' ')
        cmd, _, to = head[1:].partition('@')
        if to and to.casefold() != str(self._ensure_me().get('username') or '').casefold():
            return   # команда другому боту
        chat, user = m.get('chat') or {}, m.get('from') or {}
        if not self._allowed(chat, user.get('id')):
            return   # чужим — молчание
        self._remember(user)
        cid = str(chat.get('id'))
        thread = m.get('message_thread_id') if m.get('is_topic_message') else None
        cmd = cmd.casefold()
        if cmd in ('help', 'start'):
            self._send(cid, rp.help_text(la.TgRules.from_settings(self.feeds.settings()).escalate_min), thread=thread)
        elif cmd == 'where':
            self._where(cid, thread, arg.strip())
        elif cmd == 'today':
            self._send(cid, rp.today_text(self._cards(), self.feeds.now()), thread=thread)
        elif cmd == 'late':
            self._send(cid, rp.late_text(self._cards()), thread=thread)

    def _where(self, chat: str, thread: int | None, query: str) -> None:
        cards = self._cards()
        car, hits = rp.find_car(cards, query)
        if car is not None:
            self._where_card(chat, thread, car, cards[car])
            return
        buttons = [{'text': c, 'callback_data': data} for c in hits if (data := self.sign(f'w:{c}'))]
        if not buttons:
            self._send(chat, 'Մեքենա չի գտնվել' + (f'՝ «{la.esc(query)}»' if query else '') + '։', thread=thread)
            return
        rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
        self._send(chat, '🚚 Ո՞ր մեքենան', thread=thread, markup={'inline_keyboard': rows})

    def _where_card(self, chat: str, thread: int | None, car: str, card: Mapping[str, Any]) -> None:
        pos = card.get('position') or {}
        if pos.get('lat') is not None and pos.get('lon') is not None:
            self.api('sendLocation', chat_id=chat, latitude=pos['lat'], longitude=pos['lon'], message_thread_id=thread,
                     disable_notification=True)
        self._send(chat, rp.where_text(car, card), thread=thread)

    def _answer(self, cq: Mapping[str, Any], text: str | None) -> None:
        try:
            self.api('answerCallbackQuery', callback_query_id=cq.get('id'), text=text)
        except TelegramError as e:   # нажатие старше ~15 мин — Telegram его уже не ждёт
            logger.info('[Routes] Telegram-бот: ответ на кнопку не принят (%s)', e)

    def _on_callback(self, cq: Mapping[str, Any]) -> None:
        msg = cq.get('message') or {}
        chat, user = msg.get('chat') or {}, cq.get('from') or {}
        data = self.verify(cq.get('data'))
        if data is None or not self._allowed(chat, user.get('id')):
            self._answer(cq, None)   # подделка, старые данные, чужой — без ответа по сути
            return
        self._remember(user)
        if data.startswith('a:') and data[2:].isdigit():
            rid = int(data[2:])
            # ответ на нажатие — сразу, до замка (проход тревог держит его на время одного сообщения, а Telegram ждёт
            # ответа считанные секунды); исход — по снимку записей, «Տեսա» записывается под замком
            seen = next((r for r in list(self.records.values()) if r.id == rid), None)
            if seen is None or seen.phase not in ('active', 'ended'):   # кнопки — только у отправленных
                self._answer(cq, 'Հնացած է')
            elif seen.acked_by is not None:
                self._answer(cq, f'Արդեն նշված է՝ {seen.acked_name}')
            else:
                with self.pending_lock:   # до ответа: услышав «Գրանցված է», эскалации человек уже не увидит
                    self.pending_acks.add(rid)
                try:
                    self._answer(cq, ACKED)
                    with self.lock, tg_api.no_wait():
                        self._ack(rid, user)
                finally:
                    with self.pending_lock:
                        self.pending_acks.discard(rid)
        elif data.startswith('w:'):
            self._answer(cq, None)
            car = data[2:]
            cards = self._cards()
            thread = msg.get('message_thread_id') if msg.get('is_topic_message') else None
            if car in cards:
                self._where_card(str(chat.get('id')), thread, car, cards[car])
            else:
                self._send(str(chat.get('id')), f'{la.esc(car)}՝ այսօր տվյալ չկա։', thread=thread)
        else:
            self._answer(cq, None)

    def _ack(self, rid: int, user: Mapping[str, Any]) -> str:
        """«Տեսա»: кто и когда — в запись, сообщение правится (строка «✔ Տեսավ …», без кнопки). Правка не удалась —
        нажатие всё равно записано (эскалации не будет); очередь, 429, сеть — отметка ack_edit, правка следующим
        проходом (_pending)."""
        old = next((r for r in self.records.values() if r.id == rid), None)
        if old is None or old.phase not in ('active', 'ended'):   # номера сообщения может не быть — кнопка его записи
            return 'Հնացած է'
        if old.acked_by is not None:
            return f'Արդեն նշված է՝ {old.acked_name}'
        rec = replace(old, acked_by=user.get('id'), acked_name=_person(user), acked_at=self.feeds.now().isoformat(),
                      payload=dict(old.payload))
        try:
            self._edit(rec)
        except TelegramError as e:
            if tg_api.throttled(e):
                rec.payload['ack_edit'] = True
            logger.warning('[Routes] Telegram-бот: «Տեսա» записано, сообщение не исправлено (%s)%s', e,
                           ' — повтор следующим проходом' if tg_api.throttled(e) else '')
        self._persist(rec)
        self._map_ack(rec)
        return ACKED

    # --- общая «Տեսա» с картой «Մեքենաները առցանց» (live_ack, схема 28) ---

    def _map_ack(self, rec: Rec) -> None:
        """«Տեսա» в Telegram → отметка карты (Store.live_ack_put): страница перестаёт мигать и пишет «Տեսավ՝ имя».
        Сбой базы — в журнал (в Telegram «Տեսա» уже записана)."""
        got = la.map_ack_items(rec)
        if got is None:
            return
        day, items = got
        try:
            self.store.live_ack_put(day, items, rec.acked_name, rec.acked_at or self.feeds.now().isoformat())
        except Exception:
            logger.exception('[Routes] Telegram-бот: «Տեսա» %s не передана карте', rec.key)

    def _map_row(self, rec: Rec) -> Mapping[str, Any] | None:
        """Отметка карты, которой страница считает проблему записи подтверждённой (la.map_ack_match); сбой — None."""
        day = _map_day(rec)
        if day is None:
            return None
        try:
            rows = self.store.live_acks(day)
        except Exception:
            logger.exception('[Routes] Telegram-бот: «Տեսա» карты за %s не прочитаны', day)
            return None
        return la.map_ack_match(rec, rows, LIVE_ACK_SINCE_TOL)

    def _from_map(self, rec: Rec, row: Mapping[str, Any]) -> int:
        """«Տեսա» на карте → запись Telegram: кто (acked_by 0 — с карты, имя — пользователь карты) и когда, правка
        сообщения «✔ Տեսավ …» без кнопки (очередь, 429, сеть — ack_edit, следующим проходом). Обратно на карту не
        пишется (отметка уже там) — петли нет."""
        new = replace(rec, acked_by=MAP_ACKED_BY, acked_name=str(row.get('user') or MAP_ACKED_NAME)[:64],
                      acked_at=str(row.get('at') or self.feeds.now().isoformat()),
                      payload={**rec.payload, 'acked_via': 'map'})
        done = 0
        try:
            done = int(self._edit(new))
        except TelegramError as e:
            if tg_api.throttled(e):
                new.payload['ack_edit'] = True
            logger.warning('[Routes] Telegram-бот: «Տեսա» карты %s записана, сообщение не исправлено (%s)', rec.key, e)
        self._persist(new)
        return done

    def _sync_map_acks(self) -> int:
        """Отметки карты → записи Telegram: идущие неподтверждённые тревоги и «не успеет», у которых на карте есть
        «Տեսա» того же случая. Чтение базы — вне замка (по дню один раз), правка — по записи под замком."""
        with self.lock:
            todo = [r.key for r in self.records.values() if r.phase == 'active' and r.acked_by is None
                    and r.key.startswith(('alert:', 'late:'))]
        days: dict[str, list[dict[str, Any]]] = {}
        done = 0
        for key in todo:
            rec = self.records.get(key)
            if rec is None:
                continue
            day = _map_day(rec)
            if day is None:
                continue
            if day not in days:
                try:
                    days[day] = self.store.live_acks(day)
                except Exception:
                    logger.exception('[Routes] Telegram-бот: «Տեսա» карты за %s не прочитаны', day)
                    days[day] = []
            if not days[day]:
                continue
            self._idle()
            with self.lock, tg_api.no_wait():
                rec = self.records.get(key)   # заново под замком: «Տեսա» в Telegram могла прийти раньше
                if rec is None or rec.acked_by is not None or rec.phase != 'active' or self._acked(rec):
                    continue
                row = la.map_ack_match(rec, days[day], LIVE_ACK_SINCE_TOL)
                if row is not None:
                    done += self._from_map(rec, row)
        return done
