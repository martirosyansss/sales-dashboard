# -*- coding: utf-8 -*-
"""Тревоги карты «Մեքենաները առցանց» в Telegram-группу (№76, этап 2, docs/plans/live-map-plan.md).

Фоновый поток раз в INTERVAL_S берёт карточки флота (views._live_cards — тот же расчёт, что у карты, кэш 10 с) и шлёт
сообщение при начале тревоги (скорость, долгая стоянка, нет связи — только APK ≥ 2.2.0, GPS выключен, малый центр) и при
её окончании — для «нет связи» и GPS. Включается только env ROUTES_LIVE_ALERTS=1 и только если заданы токен
(ROUTES_LIVE_TG_TOKEN, иначе TELEGRAM_BOT_TOKEN) и чат (ROUTES_LIVE_TG_CHAT): без них поток не стартует. Слать должен
ровно один процесс — переменная задаётся только на CT115 (deploy/README.md): у ПК та же карта, но терминалы шлют на
CT115, и сообщений ПК не нужно.

Правила (настройки «Маршрутов», Rules):
- виды тревог — alert_kinds (по умолчанию все); тихие часы quiet (по умолчанию 20:00–08:00, Ереван): тревога, замеченная
  в них, не отправляется вовсе (и не отправится утром, если ещё идёт); повтор — не чаще раза в repeat_min минут на
  тревогу одного вида одной машины (следующая в этом окне пропускается, а не откладывается);
- «уже отправлено» — файл рядом с базой «Маршрутов» (route_live_alerts.json; новая схема SQLite ради нескольких строк не
  нужна): ключ тревоги — машина, вид и момент её начала; запись после каждой успешной отправки (перезапуск не
  повторяет), недельные хвосты сами уходят. Тревога, которой не было видно в этот момент и которая кончилась больше
  RECENT_MIN назад, не рассылается задним числом (после простоя сервера и при первом запуске);
- сбой отправки (сеть, Telegram) — в журнал без токена, повтор со всё большей паузой (до BACKOFF_MAX_S); поток не падает.
  Тайм-аут ответа не значит «не доставлено»: сообщение, дошедшее без ответа, при повторе придёт второй раз (редкий дубль
  принят — потерять тревогу хуже). CLIENT_ERRORS_MAX ошибок 4xx подряд (токен отозван, бота убрали из группы) — рассылка
  останавливается до перезапуска или смены настроек тревог, в журнале одна понятная строка.
Сообщения — по-армянски (глоссарий раздела), ссылка на карту Яндекса: https://yandex.ru/maps/?pt=<lon>,<lat>&z=16&l=map.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from . import actuals as ac
from .live import Rules

logger = logging.getLogger(__name__)

ENABLE_ENV = 'ROUTES_LIVE_ALERTS'
TOKEN_ENV = 'ROUTES_LIVE_TG_TOKEN'
TOKEN_FALLBACK_ENV = 'TELEGRAM_BOT_TOKEN'
CHAT_ENV = 'ROUTES_LIVE_TG_CHAT'
INTERVAL_S = 30.0
HTTP_TIMEOUT_S = 10.0
BACKOFF_MAX_S = 600.0
RECENT_MIN = 10.0                 # кончившаяся тревога старше — не рассылается задним числом
STATE_FILE = 'route_live_alerts.json'
STATE_KEEP = timedelta(hours=48)
CLIENT_ERRORS_MAX = 5             # ошибок 4xx подряд (кроме 429) — дальше не шлём, пока не перезапустят или не сменят настройки
END_KINDS = ('no_contact', 'gps')   # окончание сообщается только у них
MAP_URL = 'https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map'

TITLE = {'speed': 'Արագության գերազանցում', 'stop': 'Երկար կանգառ ոչ խանութում', 'no_contact': 'Կապ չկա',
         'gps': 'GPS-ն անջատված է', 'center': 'Փոքր կենտրոնում (մուտքը թույլատրված չէ)'}
TITLE_END = {'no_contact': 'Կապը վերականգնվեց', 'gps': 'GPS-ը կրկին միացված է'}


def config_from_env(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """(токен, чат), если рассылка включена и настроена; иначе None (поток не стартует)."""
    env = os.environ if env is None else env
    if env.get(ENABLE_ENV, '').strip() != '1':
        return None
    token = (env.get(TOKEN_ENV) or env.get(TOKEN_FALLBACK_ENV) or '').strip()
    chat = (env.get(CHAT_ENV) or '').strip()
    return (token, chat) if token and chat else None


class TelegramError(RuntimeError):
    """Сбой отправки; текст — без токена (он в адресе запроса). status — код HTTP, если он был."""

    def __init__(self, text: str, status: int | None = None):
        super().__init__(text)
        self.status = status


def send_telegram(token: str, chat: str, text: str, timeout: float = HTTP_TIMEOUT_S,
                  opener: Callable[..., Any] = urllib.request.urlopen) -> None:
    """sendMessage (простой текст — имена и ссылки без разметки); ошибка — TelegramError."""
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', method='POST',
                                 data=urllib.parse.urlencode({'chat_id': chat, 'text': text,
                                                              'disable_web_page_preview': 'true'}).encode('utf-8'))
    try:
        with opener(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise TelegramError(f'HTTP {e.code}', e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:   # сеть, тайм-аут, не JSON
        raise TelegramError(type(e).__name__) from None
    if not isinstance(body, dict) or not body.get('ok'):
        raise TelegramError(str((body or {}).get('description', 'ok=false'))[:120] if isinstance(body, dict) else 'ответ')


def _moment(raw: Any) -> datetime | None:
    """ISO-момент с часовым поясом или None (битые значения файла состояния отбрасываются)."""
    try:
        t = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return t if t is not None and t.utcoffset() is not None else None


# --- что уже отправлено ---

class AlertState:
    """Файл «уже отправлено»: sent — ключ тревоги → {at, start, end, skipped}; last — «машина|вид» → момент последнего
    начала (окно повтора). Нечитаемый файл — пустое состояние (и предупреждение): задним числом не рассылается (RECENT_MIN)."""

    def __init__(self, path: str):
        self.path = path
        self.sent: dict[str, dict[str, Any]] = {}
        self.last: dict[str, str] = {}
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            self.sent = {k: v for k, v in (raw.get('sent') or {}).items()
                         if isinstance(v, dict) and _moment(v.get('at')) is not None
                         and all(v.get(x) is None or isinstance(v.get(x), str) for x in ('start', 'end'))}
            self.last = {k: v for k, v in (raw.get('last') or {}).items() if _moment(v) is not None}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            logger.warning('[Routes] Тревоги в Telegram: файл «уже отправлено» не прочитан — начинаем заново')

    def save(self, now: datetime) -> None:
        """Атомарно (временный файл + замена); хвосты старше STATE_KEEP убираются. Ошибка записи — в журнал."""
        keep = (now - STATE_KEEP).isoformat()
        self.sent = {k: v for k, v in self.sent.items() if str(v.get('at', '')) >= keep}
        self.last = {k: v for k, v in self.last.items() if v >= keep}
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or '.', prefix='.route_live_alerts-', suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'sent': self.sent, 'last': self.last}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            logger.exception('[Routes] Тревоги в Telegram: «уже отправлено» не записано')


# --- решения и тексты ---

@dataclass(frozen=True)
class Message:
    key: str          # машина|вид|начало тревоги
    phase: str        # start | end
    car: str
    kind: str
    text: str


def in_quiet(rules: Rules, now: datetime) -> bool:
    """Сейчас — тихие часы (по Еревану; граница «с» включена, «до» — нет; через полночь — тоже)."""
    if rules.quiet is None:
        return False
    lo, hi = rules.quiet
    n = now.astimezone(ac.YEREVAN)
    m = n.hour * 60 + n.minute + n.second / 60.0
    return lo <= m < hi if lo < hi else (m >= lo or m < hi)


def _hm(iso: Any) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(ac.YEREVAN).strftime('%H:%M')
    except (TypeError, ValueError):
        return '—'


def _minutes(a: Mapping[str, Any]) -> int | None:
    try:
        return round((datetime.fromisoformat(a['to']) - datetime.fromisoformat(a['from'])).total_seconds() / 60)
    except (KeyError, TypeError, ValueError):
        return None


def build_text(card: Mapping[str, Any], a: Mapping[str, Any], phase: str, rules: Rules) -> str:
    """Сообщение: что случилось, машина, водитель, где (ссылка на карту Яндекса), время — по-армянски."""
    kind = a['kind']
    lines = [TITLE_END[kind] if phase == 'end' else TITLE[kind]]
    if kind == 'gps' and phase == 'start' and a.get('gps') == 'no_permission':
        lines[0] = 'GPS-ի թույլտվությունը չկա'
    plate = [x for x in (card.get('car_code'), card.get('name')) if x]
    lines.append('Մեքենա՝ ' + ' · '.join(plate))
    driver = card.get('driver') or next(iter(card.get('drivers') or ()), None)
    if driver:
        lines.append('Վարորդ՝ ' + str(driver))
    if phase == 'end':
        minutes = _minutes(a)
        word = 'Կապ չկար' if kind == 'no_contact' else 'Անջատված էր'
        if minutes is not None:
            lines.append(f'{word}՝ {minutes} րոպե։')
    elif kind == 'speed':
        lines.append(f'Արագություն՝ {a.get("max_kmh")} կմ/ժ (սահմանը՝ {rules.speed_kmh:g} կմ/ժ)։')
    elif kind == 'stop':
        lines.append(f'Կանգառի տևողությունը՝ {a.get("minutes")} րոպե' + (' (ճաշի ժամին)։' if a.get('lunch') else '։'))
    elif kind == 'no_contact':
        lines.append(f'Կապ չկա՝ {a.get("minutes")} րոպե։')
    elif kind == 'center':
        lines.append('Մեքենան մտել է փոքր կենտրոն, որտեղ նրան թույլատրված չէ։')
    lat, lon, last_known = a.get('lat'), a.get('lon'), False
    if lat is None or lon is None:   # «нет связи» и GPS: места события нет — последняя известная точка
        pos = card.get('position') or {}
        lat, lon, last_known = pos.get('lat'), pos.get('lon'), True
    if lat is not None and lon is not None:
        lines.append(('Վերջին հայտնի դիրքը՝ ' if last_known else 'Որտեղ՝ ') + MAP_URL.format(lat=lat, lon=lon))
    lines.append('Ժամ՝ ' + _hm(a.get('to') if phase == 'end' else a.get('from')))
    return '\n'.join(lines)


def _plan_car(car: str, card: Mapping[str, Any], rules: Rules, now: datetime, state: AlertState, quiet: bool,
              repeat: timedelta, out: list[Message]) -> bool:
    """Решения по тревогам одной машины (plan_messages); True — состояние менялось."""
    changed = False
    for a in card.get('alerts_log') or ():
        kind = a.get('kind')
        if kind not in rules.alert_kinds or not a.get('from'):
            continue
        key = f'{car}|{kind}|{a["from"]}'
        rec = state.sent.get(key)
        if rec is None:
            why = None
            ended = a.get('to')
            if not a.get('active') and (not ended or now - datetime.fromisoformat(ended) > timedelta(minutes=RECENT_MIN)):
                why = 'old'
            elif quiet:
                why = 'quiet'
            else:
                last = state.last.get(f'{car}|{kind}')
                if last is not None and now - datetime.fromisoformat(last) < repeat:
                    why = 'repeat'
            if why is not None:
                state.sent[key] = {'at': now.isoformat(), 'start': None, 'end': None, 'skipped': why}
                changed = True
            else:
                out.append(Message(key, 'start', car, kind, build_text(card, a, 'start', rules)))
        elif kind in END_KINDS and rec.get('start') and not rec.get('end') and not a.get('active') and a.get('to'):
            if quiet:
                rec['end'] = 'quiet'
                changed = True
            else:
                out.append(Message(key, 'end', car, kind, build_text(card, a, 'end', rules)))

    return changed


def plan_messages(cards: Mapping[str, Mapping[str, Any]], rules: Rules, now: datetime,
                  state: AlertState) -> tuple[list[Message], bool]:
    """Что слать сейчас: (сообщения по порядку, менялось ли состояние — пропущенные тревоги отмечаются сразу).
    Тревога без записи — начало: не «старая» (идёт или кончилась не раньше RECENT_MIN назад), не тихие часы, не в окне
    повтора этого вида у этой машины; с записью «начало отправлено», у «нет связи»/GPS кончилась — окончание."""
    out: list[Message] = []
    changed = False
    quiet = in_quiet(rules, now)
    repeat = timedelta(minutes=rules.repeat_min)
    for car, card in cards.items():
        try:
            changed |= _plan_car(car, card, rules, now, state, quiet, repeat, out)
        except Exception:   # битые данные одной машины не должны остановить тревоги остальных
            logger.exception('[Routes] Тревоги в Telegram: машина %s пропущена', car)
    return out, changed


class LiveAlerter:
    """Один проход (tick) = решения plan_messages + отправка; состояние пишется после каждого сообщения. source() — (Rules,
    момент, карточки флота) или None (флота нет); send(text) бросает исключение при сбое."""

    def __init__(self, source: Callable[[], tuple[Rules, datetime, Mapping[str, Mapping[str, Any]]] | None],
                 send: Callable[[str], None], state_path: str):
        self.source = source
        self.send = send
        self.state = AlertState(state_path)
        self.failures = 0
        self.retry_at = 0.0
        self.client_errors = 0                       # подряд ошибок 4xx
        self.halted: tuple[Any, ...] | None = None   # настройки тревог, при которых рассылка остановлена

    def tick(self, monotonic: Callable[[], float] = time.monotonic) -> int:
        """Отправленных сообщений за проход. Пауза после сбоя отправки — до retry_at."""
        if monotonic() < self.retry_at:
            return 0
        got = self.source()
        if got is None:
            return 0
        rules, now, cards = got
        sig = (rules.alert_kinds, rules.quiet, rules.repeat_min)
        if self.halted is not None:
            if self.halted == sig:
                return 0
            self.halted, self.client_errors = None, 0   # настройки тревог сменили — пробуем снова
        messages, changed = plan_messages(cards, rules, now, self.state)
        sent = 0
        for m in messages:
            try:
                self.send(m.text)
            except Exception as e:   # сеть, Telegram: не падаем, повтор позже
                self.failures += 1
                status = getattr(e, 'status', None)
                self.client_errors = self.client_errors + 1 if isinstance(status, int) and 400 <= status < 500 \
                    and status != 429 else 0
                if self.client_errors >= CLIENT_ERRORS_MAX:
                    self.halted = sig
                    logger.error('[Routes] Тревоги в Telegram ОСТАНОВЛЕНЫ: %d ошибок %s подряд (токен или чат неверны, бота '
                                 'убрали из группы?). Исправьте .env и перезапустите сервер или смените настройки тревог',
                                 self.client_errors, e)
                    break
                pause = min(INTERVAL_S * 2 ** self.failures, BACKOFF_MAX_S)
                self.retry_at = monotonic() + pause
                logger.warning('[Routes] Тревоги в Telegram: не отправлено (%s: %s), повтор через %.0f с',
                               type(e).__name__, e, pause)
                break
            self.failures = self.client_errors = 0
            rec = self.state.sent.setdefault(m.key, {'at': now.isoformat(), 'start': None, 'end': None})
            rec['end' if m.phase == 'end' else 'start'] = now.isoformat()
            if m.phase == 'start':
                self.state.last[f'{m.car}|{m.kind}'] = now.isoformat()
            self.state.save(now)
            changed = False
            sent += 1
        if changed:
            self.state.save(now)
        return sent
