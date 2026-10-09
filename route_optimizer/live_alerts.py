# -*- coding: utf-8 -*-
"""Тревоги карты «Մեքենաները առցանց» для Telegram-бота: что слать, править и поднимать (№76 этап 2 → №91,
docs/plans/telegram-bot-plan.md §3). Чистые функции — без сети и базы: карточки флота (views._live_cards: alerts_log,
position, driver) + правила + записи бота (Rec, таблица tg_message) → действия (plan). Отправку, правки и запись делает
tg_bot.

Включение — env ROUTES_LIVE_ALERTS=1 и заданы токен (ROUTES_LIVE_TG_TOKEN, иначе TELEGRAM_BOT_TOKEN) и чат
(ROUTES_LIVE_TG_CHAT): без них бот не стартует. Слать должен ровно один процесс — переменная задаётся только на CT115
(deploy/README.md): у ПК та же карта, но терминалы шлют на CT115, и сообщений ПК не нужно.

Правила (настройки «Маршрутов»: Rules — виды, тихие часы, повтор; TgRules — уровни, эскалация, отчёты):
- виды тревог — alert_kinds (по умолчанию все, кроме LIVE_KINDS_OPT_IN); тихие часы quiet (по умолчанию 20:00–08:00,
  Ереван): тревога, замеченная в них, не отправляется вовсе (и не отправится утром, если ещё идёт); повтор — не чаще
  раза в repeat_min минут на тревогу одного вида одной машины (следующая в этом окне пропускается, а не откладывается);
  (и в одном проходе: две новые тревоги вида у машины — уходит первая);
- «փոքր շեղում» (minor) и объяснённые диспетчером тревоги (explained) не рассылаются и не отмечаются: малое отклонение,
  которое вырастет в тревогу (то же начало), уйдёт тогда; уже отправленная, а потом объяснённая или ставшая «փոքր», —
  закрывается правкой (End reason), как и запись прошлого дня, ещё «идущая» (тревога пропала из журнала без конца);
- запись тревоги — машина, вид и момент начала (alert_key); решение «не слать» (тихие часы, окно повтора, старая)
  записывается тоже (phase skipped) — перезапуск его не пересматривает. Тревога, которой не было видно в этот момент и
  которая кончилась больше RECENT_MIN назад, не рассылается задним числом (после простоя сервера и при первом запуске);
- уровень (level_of): 🔴 critical — звук, «Տեսա», эскалация; 🟠 warning — звук, «Տեսա»; ⚪ info — без звука и кнопок.
  По виду из настроек tg_levels («не успеет» — по виду строки: окно, возврат, план); «Կապ չկա» — ⚪, пока не отмечено
  «в терминалах есть SIM» (tg_sim_installed: до SIM терминал на связи только на Wi-Fi склада);
- окончание тревоги (у всех видов, кроме «не успеет») — правка того же сообщения: «✅ Վերջացավ · տևեց N րոպե», кнопка
  «Տեսա» убирается; правка беззвучна и идёт и в тихие часы. Сообщения нет (запись перенесена из route_live_alerts.json
  или правка невозможна) — новое ⚪ сообщение об окончании: у перенесённых — только «нет связи» и GPS (как слал прежний
  поток), и не в тихие часы. Тревога, уже кончившаяся к отправке (не старше RECENT_MIN), уходит сразу с ✅ и без «Տեսա»;
- «не успеет» (late, №87) — прогноз, а не событие: одно сообщение на машину в день (late_key), строка на магазин (и на
  возврат машины на склад). Новая строка или ухудшившаяся не меньше чем на max(repeat_min, LATE_STEP_MIN) минут против
  отправленного — правка сообщения + короткий ответ на него со звуком (у нового сообщения — само сообщение); прочие
  изменения прогноза — беззвучная правка не чаще LATE_EDIT_MIN (появилась или ушла строка — сразу). Строка, которая не
  «опаздывает» (или нет свежего GPS) LATE_CLEAR_MIN минут подряд, снимается; снова опаздывает — новый случай (со звуком);
  вернулась раньше — тот же случай. Снялись все строки — ✅. В тихие часы «не успеет» не решается вовсе: прогноз, который
  ещё в силе после них, уйдёт тогда. Окно повтора вида у машины к нему не применяется;
- эскалация (due_escalations): 🔴 с сообщением, не подтверждённая «Տեսա» escalate_min минут и всё ещё идущая (active) —
  один раз; не в тихие часы.
Сообщения — HTML (parse_mode), по-армянски (глоссарий раздела); все данные экранируются. Карта — Яндекс:
https://yandex.ru/maps/?pt=<lon>,<lat>&z=16&l=map.
"""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from . import actuals as ac
from .live import Rules
from .store import DEFAULT_SETTINGS, TG_LEVEL_KINDS

logger = logging.getLogger(__name__)

ENABLE_ENV = 'ROUTES_LIVE_ALERTS'
TOKEN_ENV = 'ROUTES_LIVE_TG_TOKEN'
TOKEN_FALLBACK_ENV = 'TELEGRAM_BOT_TOKEN'
CHAT_ENV = 'ROUTES_LIVE_TG_CHAT'
INTERVAL_S = 30.0
BACKOFF_MAX_S = 600.0
RECENT_MIN = 10.0                 # кончившаяся тревога старше — не рассылается задним числом
STATE_FILE = 'route_live_alerts.json'   # «уже отправлено» прежнего потока (до №91) — переносит tg_bot
CLIENT_ERRORS_MAX = 5             # ошибок 4xx подряд (кроме 429) — дальше не шлём, пока не перезапустят или не сменят настройки
LEGACY_END_KINDS = ('no_contact', 'gps')   # прежний поток сообщал окончание только у них
MAP_URL = 'https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map'
LATE_STEP_MIN = 15.0              # «не успеет»: со звуком — при ухудшении прогноза на столько (и не меньше repeat_min)
LATE_CLEAR_MIN = 15.0             # …строка без опоздания столько минут подряд — снимается (новый случай)
LATE_EDIT_MIN = 5.0               # беззвучная правка «не успеет» без новых строк — не чаще
LATE_LINES_SHOWN = 15             # строк «не успеет» в сообщении — не больше (остальные числом: предел Telegram 4096)

LEVELS = ('critical', 'warning', 'info')   # по убыванию важности
EMOJI = {'critical': '🔴', 'warning': '🟠', 'info': '⚪'}
TITLE = {'speed': 'Արագության գերազանցում', 'stop': 'Երկար կանգառ ոչ խանութում', 'no_contact': 'Կապ չկա',
         'gps': 'GPS-ն անջատված է', 'center': 'Փոքր կենտրոնում (մուտքը թույլատրված չէ)',
         'late': 'Չի հասցնում ժամանակին (կանխատեսում)', 'deviation': 'Շեղում երթուղուց',
         'sequence': 'Խանութներ բաց են թողնված (հերթականություն)'}
EXIT_TEXT = {'closed': 'հավելվածը փակվել է', 'shutdown': 'հեռախոսն անջատվել է'}   # live.offline_reason (APK 2.2.5)
TITLE_END = {'no_contact': 'Կապը վերականգնվեց', 'gps': 'GPS-ը կրկին միացված է'}
END_TEXT = {'explained': 'Բացատրված է', 'minor': 'Փոքր շեղում՝ ահազանգ չէ', 'day': 'Օրն ավարտվեց'}
ACK_TEXT = '✔ Տեսա'
MAP_TEXT = '🗺 Քարտեզ'
WHERE_TEXT = '📍 Որտեղ է'


def config_from_env(env: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """(токен, чат), если бот включён и настроен; иначе None (бот не стартует)."""
    env = os.environ if env is None else env
    if env.get(ENABLE_ENV, '').strip() != '1':
        return None
    token = (env.get(TOKEN_ENV) or env.get(TOKEN_FALLBACK_ENV) or '').strip()
    chat = (env.get(CHAT_ENV) or '').strip()
    return (token, chat) if token and chat else None


def _moment(raw: Any) -> datetime | None:
    """ISO-момент с часовым поясом или None (битые значения записей отбрасываются)."""
    try:
        t = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return t if t is not None and t.utcoffset() is not None else None


def _hhmm(raw: Any) -> float | None:
    if not isinstance(raw, str) or len(raw) != 5 or raw[2] != ':' or not (raw[:2] + raw[3:]).isdigit():
        return None
    return float(int(raw[:2]) * 60 + int(raw[3:]))


@dataclass(frozen=True)
class TgRules:
    """Настройки бота (store.DEFAULT_SETTINGS tg_*): уровни по виду, SIM в терминалах, эскалация (минуты, кому), отчёты и
    предел итога дня (минуты от полуночи). Неизменяемый и хешируемый — входит в подпись остановки рассылки."""
    levels: tuple[tuple[str, str], ...] = tuple(DEFAULT_SETTINGS['tg_levels'].items())
    sim: bool = False
    escalate_min: float = 10.0
    escalate_to: tuple[int, ...] = tuple(DEFAULT_SETTINGS['tg_escalate_to'])
    report_plan: bool = True
    report_summary: bool = True
    report_week: bool = True
    summary_at: float = 19 * 60 + 30.0

    @classmethod
    def from_settings(cls, s: Mapping[str, Any]) -> TgRules:
        levels = s.get('tg_levels') if isinstance(s.get('tg_levels'), Mapping) else {}
        base = DEFAULT_SETTINGS['tg_levels']
        wait = s.get('tg_escalate_min', DEFAULT_SETTINGS['tg_escalate_min'])
        to = s.get('tg_escalate_to', DEFAULT_SETTINGS['tg_escalate_to'])
        at = _hhmm(s.get('tg_summary_at'))
        return cls(tuple((k, levels.get(k) if levels.get(k) in LEVELS else base[k]) for k in TG_LEVEL_KINDS),
                   s.get('tg_sim_installed') is True,
                   float(wait) if isinstance(wait, (int, float)) and not isinstance(wait, bool) else 10.0,
                   tuple(x for x in to if isinstance(x, int) and not isinstance(x, bool)) if isinstance(to, list) else (),
                   s.get('tg_report_plan', True) is not False, s.get('tg_report_summary', True) is not False,
                   s.get('tg_report_week', True) is not False,
                   at if at is not None else 19 * 60 + 30.0)

    def level(self, key: str) -> str:
        return dict(self.levels).get(key, 'warning')


def level_of(kind: str, a: Mapping[str, Any], tg: TgRules) -> str:
    """Уровень тревоги: по виду из настроек; «не успеет» — по виду строки; «нет связи» без SIM — ⚪."""
    if kind == 'no_contact' and not tg.sim:
        return 'info'
    if kind == 'late':
        return tg.level({'window': 'late_window', 'return': 'late_return'}.get(a.get('late_kind'), 'late_plan'))
    return tg.level(kind)


def top_level(levels: Sequence[str]) -> str | None:
    """Самый важный из уровней (None — пусто)."""
    return min(levels, key=LEVELS.index) if levels else None


def in_quiet(rules: Rules, now: datetime) -> bool:
    """Сейчас — тихие часы (по Еревану; граница «с» включена, «до» — нет; через полночь — тоже)."""
    if rules.quiet is None:
        return False
    lo, hi = rules.quiet
    n = now.astimezone(ac.YEREVAN)
    m = n.hour * 60 + n.minute + n.second / 60.0
    return lo <= m < hi if lo < hi else (m >= lo or m < hi)


# --- записи бота ---

@dataclass
class Rec:
    """Запись бота (строка tg_message, store.TG_MESSAGE_FIELDS). phase: active — сообщение тревоги идёт (или «не успеет»
    со строками), ended — кончилась (✅), skipped — решено не слать (payload.why), report — отчёт."""
    key: str
    kind: str
    phase: str
    sent_at: str
    car: str | None = None
    level: str | None = None
    id: int | None = None
    chat: str | None = None
    message_id: int | None = None
    thread_id: int | None = None
    acked_by: int | None = None
    acked_name: str | None = None
    acked_at: str | None = None
    resolved_at: str | None = None
    escalated_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def alert_key(car: str, kind: str, start: str) -> str:
    return f'alert:{car}|{kind}|{start}'


def late_key(car: str, day: str) -> str:
    return f'late:{car}|{day}'


# --- тексты (HTML) ---

def esc(x: Any) -> str:
    return html.escape(str(x), quote=False)


def hm(iso: Any) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(ac.YEREVAN).strftime('%H:%M')
    except (TypeError, ValueError):
        return '—'


def _minutes(a: Mapping[str, Any]) -> int | None:
    try:
        return round((datetime.fromisoformat(a['to']) - datetime.fromisoformat(a['from'])).total_seconds() / 60)
    except (KeyError, TypeError, ValueError):
        return None


def who(card: Mapping[str, Any]) -> list[str]:
    """Строки «машина» и «водитель (+ առաքիչ)»."""
    plate = []
    if card.get('car_code'):
        plate.append(f'<b>{esc(card["car_code"])}</b>')
    if card.get('name'):
        plate.append(esc(card['name']))
    lines = ['Մեքենա՝ ' + ' · '.join(plate)]
    driver = card.get('driver') or next(iter(card.get('drivers') or ()), None)
    if driver:
        helper = card.get('helper')
        lines.append('Վարորդ՝ ' + esc(driver) + (f' + {esc(helper)}' if helper and helper != driver else ''))
    return lines


def map_url(card: Mapping[str, Any], a: Mapping[str, Any]) -> tuple[str | None, bool]:
    """(ссылка на карту места тревоги, это последняя известная точка машины — у «нет связи» и GPS места события нет)."""
    lat, lon = a.get('lat'), a.get('lon')
    if lat is not None and lon is not None:
        return MAP_URL.format(lat=lat, lon=lon), False
    pos = card.get('position') or {}
    if pos.get('lat') is not None and pos.get('lon') is not None:
        return MAP_URL.format(lat=pos['lat'], lon=pos['lon']), True
    return None, False


def detail(card: Mapping[str, Any], a: Mapping[str, Any], rules: Rules) -> list[str]:
    """Что случилось — строки по виду тревоги (как прежние сообщения, №76)."""
    kind = a['kind']
    if kind == 'speed':
        return [f'Արագություն՝ {esc(a.get("max_kmh"))} կմ/ժ (սահմանը՝ {rules.speed_kmh:g} կմ/ժ)։']
    if kind == 'stop':
        return [f'Կանգառի տևողությունը՝ {esc(a.get("minutes"))} րոպե' + (' (ճաշի ժամին)։' if a.get('lunch') else '։')]
    if kind == 'no_contact':
        out = [f'Կապ չկա՝ {esc(a.get("minutes"))} րոպե։']
        # причина — из последнего состояния терминала (не из offline_reason: при другой активной тревоге state='alert')
        device = card.get('device')
        reason = device.get('exit') if isinstance(device, Mapping) else None
        if reason in EXIT_TEXT:
            out.append(EXIT_TEXT[reason].capitalize() + '։')
        return out
    if kind == 'center':
        return ['Մեքենան մտել է փոքր կենտրոն, որտեղ նրան թույլատրված չէ։']
    if kind == 'deviation':   # идёт — «уже N км»; кончилось к отправке — «отклонилась на N км (с — до)»
        km = esc(a.get('km')).replace('.', ',')
        out = [f'Մեքենան պլանային երթուղուց {rules.deviation_m:g} մ-ից ավելի հեռու է՝ արդեն {km} կմ։'
               if a.get('active') else
               f'Մեքենան շեղվել էր պլանային երթուղուց ({rules.deviation_m:g} մ-ից ավելի)՝ {km} կմ, '
               f'{hm(a.get("from"))}–{hm(a.get("to"))}։']
        if isinstance(a.get('excess_km'), (int, float)):   # перепробег участка (live.detour_legs)
            out.append('Ավելորդ վազք հատվածում՝ ≈ ' + f'{a["excess_km"]:g}'.replace('.', ',') + ' կմ։')
        return out
    if kind == 'sequence':   # пропущенные магазины (номер по плану и название) и магазин, обслуженный раньше них
        def name(x: Mapping[str, Any]) -> str:
            return ' '.join(esc(p) for p in (f'№{x["no"]}' if x.get('no') else None, x.get('name') or x.get('stop_id'))
                            if p)
        skipped = [x for x in a.get('skipped') or () if isinstance(x, Mapping)]
        out = ['Բաց թողնված՝ ' + ', '.join(name(x) for x in skipped) + '։']
        if isinstance(a.get('jump'), Mapping):
            out.append('Նախքան դրանք սպասարկվել է՝ ' + name(a['jump']) + '։')
        return out
    return []


def alert_body(card: Mapping[str, Any], a: Mapping[str, Any], rules: Rules, level: str) -> str:
    """Тело сообщения тревоги: уровень и что случилось, машина, водитель, подробности, где, когда началась. У ⚪ кнопок
    нет — ссылка на карту в тексте."""
    kind = a['kind']
    title = 'GPS-ի թույլտվությունը չկա' if kind == 'gps' and a.get('gps') == 'no_permission' else TITLE[kind]
    lines = [f'{EMOJI[level]} <b>{esc(title)}</b>', *who(card), *detail(card, a, rules)]
    url, last_known = map_url(card, a)
    if url and level == 'info':
        lines.append(('Վերջին հայտնի դիրքը՝ ' if last_known else 'Որտեղ՝ ') + f'<a href="{esc(url)}">քարտեզում</a>')
    lines.append('Ժամ՝ ' + hm(a.get('from')))
    return '\n'.join(lines)


def end_body(card: Mapping[str, Any], a: Mapping[str, Any]) -> str:
    """Отдельное ⚪ сообщение об окончании (правки нет): что кончилось, машина, сколько длилось, когда."""
    kind = a['kind']
    lines = [f'{EMOJI["info"]} <b>{esc(TITLE_END.get(kind) or TITLE[kind])}</b>', *who(card)]
    minutes = _minutes(a)
    if minutes is not None:
        lines.append(f'Տևեց՝ {minutes} րոպե։')
    lines.append('Ժամ՝ ' + hm(a.get('to')))
    return '\n'.join(lines)


def late_line(a: Mapping[str, Any]) -> str:
    """Строка «не успеет» (№87): магазин — на сколько позже окна / плана, прогноз прибытия и предел; машина — возврат."""
    if a.get('late_kind') == 'return':
        return (f'Չի հասցնում վերադառնալ պահեստ՝ +{esc(a.get("over_min"))} րոպե (վերադարձ ≈ {hm(a.get("eta"))}, '
                f'աշխատանքային օրը՝ մինչև {hm(a.get("limit"))})։')
    what = ('պատուհանից', 'պատուհանը՝ մինչև') if a.get('late_kind') == 'window' else ('պլանից', 'պլանով՝')
    return (f'{esc(a.get("name") or a.get("stop_id"))} — կուշանա {what[0]} {esc(a.get("over_min"))} րոպեով '
            f'(ժամանում ≈ {hm(a.get("eta"))}, {what[1]} {hm(a.get("limit"))})։')


def late_body(card: Mapping[str, Any], texts: Sequence[str], level: str, now: datetime) -> str:
    more = len(texts) - LATE_LINES_SHOWN
    lines = [f'{EMOJI[level]} <b>{esc(TITLE["late"])}</b>', *who(card), *texts[:LATE_LINES_SHOWN]]
    if more > 0:
        lines.append(f'… և ևս {more} խանութ')
    url, _ = map_url(card, {})
    if url and level == 'info':
        lines.append(f'Որտեղ է հիմա՝ <a href="{esc(url)}">քարտեզում</a>')
    lines.append('Ժամ՝ ' + now.astimezone(ac.YEREVAN).strftime('%H:%M'))
    return '\n'.join(lines)


def render(rec: Rec) -> str:
    """Текст сообщения записи сейчас: ✅ (кончилась), тело, «обновлено», кто видел."""
    lines = []
    if rec.phase == 'ended':
        m = rec.payload.get('minutes')
        lines.append('✅ <b>' + END_TEXT.get(rec.payload.get('end_reason'), 'Վերջացավ') + '</b>'
                     + (f' · տևեց {m} րոպե' if isinstance(m, int) else ''))
    lines.append(str(rec.payload.get('body') or ''))
    if rec.payload.get('updated'):
        lines.append('🔄 Թարմացված՝ ' + esc(rec.payload['updated']))
    if rec.acked_name:
        lines.append(f'✔ Տեսավ {esc(rec.acked_name)} · {hm(rec.acked_at)}')
    return '\n'.join(lines)


def keyboard(rec: Rec, sign: Callable[[str], str | None]) -> dict[str, Any] | None:
    """Кнопки 🔴/🟠: «Տեսա» (пока идёт и не подтверждена), карта (ссылка), «где машина» (callback). ⚪ — без кнопок.
    sign — подписанные данные кнопки (None — не помещаются в 64 байта: кнопки нет)."""
    if rec.level not in ('critical', 'warning'):
        return None
    row: list[dict[str, str]] = []
    if rec.phase == 'active' and rec.acked_by is None and rec.id is not None:
        data = sign(f'a:{rec.id}')
        if data:
            row.append({'text': ACK_TEXT, 'callback_data': data})
    if rec.payload.get('map'):
        row.append({'text': MAP_TEXT, 'url': str(rec.payload['map'])})
    if rec.car:
        data = sign(f'w:{rec.car}')
        if data:
            row.append({'text': WHERE_TEXT, 'callback_data': data})
    return {'inline_keyboard': [row] if row else []}


# --- решения ---

@dataclass(frozen=True)
class Skip:
    """Тревогу не слать (why: quiet | repeat | old) — только запись."""
    key: str
    car: str
    kind: str
    why: str


@dataclass(frozen=True)
class Send:
    """Новое сообщение тревоги; ended — уже кончилась к отправке (✅ сразу, без «Տեսա»), minutes — сколько длилась."""
    key: str
    car: str
    kind: str
    level: str
    body: str
    map: str | None
    ended: bool = False
    minutes: int | None = None
    resolved_at: str | None = None


@dataclass(frozen=True)
class End:
    """Тревога кончилась: правка её сообщения (✅); body — отдельное ⚪ сообщение, если правки нет. reason — закрыта не
    концом тревоги (без отдельного сообщения): explained — объяснена диспетчером, minor — оказалась «փոքր շեղում»,
    day — день прошёл, а запись ещё «идёт» (тревога пропала из журнала без конца: 20:00, день закрыт)."""
    key: str
    car: str
    kind: str
    minutes: int | None
    at: str
    body: str
    reason: str | None = None


@dataclass(frozen=True)
class Late:
    """«Не успеет» машины за день. op: send — новое сообщение; edit — правка (notify — строки для ответа со звуком,
    пусто — беззвучно); end — ✅; state — только запись (отсчёт «не опаздывает»). lines — строки записи после решения:
    цель → {over, sent, kind, text, calm}; seen — все цели, опаздывавшие за день (итог дня)."""
    key: str
    car: str
    op: str
    level: str | None
    body: str | None
    lines: dict[str, dict[str, Any]]
    seen: tuple[str, ...]
    notify: tuple[str, ...] = ()


Action = Skip | Send | End | Late


@dataclass(frozen=True)
class Plan:
    actions: list[Action]
    active: frozenset[str]   # ключи записей, чья тревога идёт сейчас (эскалация)


def _last_starts(records: Mapping[str, Rec]) -> dict[str, datetime]:
    """«машина|вид» → момент последнего отправленного начала (окно повтора)."""
    out: dict[str, datetime] = {}
    for r in records.values():
        if r.key.startswith('alert:') and r.phase in ('active', 'ended') and r.car:
            t = _moment(r.sent_at)
            k = f'{r.car}|{r.kind}'
            if t is not None and (k not in out or t > out[k]):
                out[k] = t
    return out


def _plan_late(car: str, card: Mapping[str, Any], rules: Rules, tg: TgRules, now: datetime, rec: Rec | None
               ) -> Late | None:
    """«Не успеет» одной машины (правило — в описании модуля); None — ничего не менялось."""
    day = now.astimezone(ac.YEREVAN).date().isoformat()
    key = late_key(car, day)
    old: dict[str, dict[str, Any]] = dict((rec.payload.get('lines') or {}) if rec else {})
    step = max(rules.repeat_min, LATE_STEP_MIN)
    lines: dict[str, dict[str, Any]] = {}
    notify: list[str] = []
    for a in card.get('alerts_log') or ():
        if a.get('kind') != 'late' or not a.get('active') or not isinstance(a.get('over_min'), int):
            continue
        target = str(a.get('target'))
        prev = old.get(target) or {}
        sent = prev.get('sent') if isinstance(prev.get('sent'), int) else None
        line = {'over': a['over_min'], 'sent': sent, 'kind': a.get('late_kind'), 'text': late_line(a), 'calm': None}
        if sent is None or a['over_min'] >= sent + step:   # новая строка (или новый случай) либо ухудшилась на шаг
            line['sent'] = a['over_min']
            notify.append(line['text'])
        lines[target] = line
    for target, prev in old.items():
        if target in lines:
            continue
        calm = _moment(prev.get('calm'))
        if calm is None:   # перестала опаздывать (или нет свежего GPS) — отсчёт LATE_CLEAR_MIN
            lines[target] = {**prev, 'calm': now.isoformat()}
        elif now - calm < timedelta(minutes=LATE_CLEAR_MIN):
            lines[target] = prev
    seen = tuple(sorted(set((rec.payload.get('seen') or []) if rec else []) | set(lines)))
    live_lines = [x for x in lines.values() if not x.get('calm')]
    level = top_level([level_of('late', {'late_kind': x.get('kind')}, tg) for x in live_lines]) or (rec.level if rec else None)
    body = late_body(card, [x['text'] for x in live_lines], level, now) if live_lines and level else None
    if rec is None:
        return Late(key, car, 'send', level, body, lines, seen) if notify else None
    if notify:
        return Late(key, car, 'edit', level, body, lines, seen, tuple(notify))
    if not lines:
        return Late(key, car, 'end', rec.level, None, lines, seen) if rec.phase == 'active' else None
    if lines == old:
        return None
    structural = set(lines) != set(old)   # строка появилась или снята; «не опаздывает пока» — как прочие (не чаще)
    edited = _moment(rec.payload.get('edited')) or _moment(rec.sent_at)
    due = edited is None or now - edited >= timedelta(minutes=LATE_EDIT_MIN)
    if body is not None and body != rec.payload.get('body') and rec.phase == 'active' and (structural or due):
        return Late(key, car, 'edit', level, body, lines, seen)
    return Late(key, car, 'state', rec.level, None, lines, seen)


def _plan_car(car: str, card: Mapping[str, Any], rules: Rules, tg: TgRules, now: datetime,
              records: Mapping[str, Rec], quiet: bool, last: dict[str, datetime], out: list[Action],
              active: set[str]) -> None:
    """Решения по тревогам одной машины (plan)."""
    if 'late' in rules.alert_kinds and not quiet:
        day = now.astimezone(ac.YEREVAN).date().isoformat()
        rec = records.get(late_key(car, day))
        got = _plan_late(car, card, rules, tg, now, rec)
        if got is not None:
            out.append(got)
        lines = got.lines if got is not None else ((rec.payload.get('lines') or {}) if rec else {})
        if any(not x.get('calm') for x in lines.values()):
            active.add(late_key(car, day))
    repeat = timedelta(minutes=rules.repeat_min)
    for a in card.get('alerts_log') or ():
        kind = a.get('kind')
        if kind == 'late' or not a.get('from'):
            continue
        key = alert_key(car, kind, a['from'])
        if a.get('minor') or a.get('explained'):   # не рассылаются; уже отправленная — закрыть (без «Տեսա» и эскалации)
            rec = records.get(key)
            if rec is not None and rec.phase == 'active':
                out.append(End(key, car, kind, _minutes(a), a.get('to') or now.isoformat(), '',
                               'explained' if a.get('explained') else 'minor'))
            continue
        if kind not in rules.alert_kinds:
            continue
        if a.get('active'):
            active.add(key)
        rec = records.get(key)
        if rec is None:
            ended = a.get('to')
            why = None
            if not a.get('active') and (not ended or now - datetime.fromisoformat(ended) > timedelta(minutes=RECENT_MIN)):
                why = 'old'
            elif quiet:
                why = 'quiet'
            elif (t := last.get(f'{car}|{kind}')) is not None and now - t < repeat:
                why = 'repeat'
            if why is not None:
                out.append(Skip(key, car, kind, why))
                continue
            level = level_of(kind, a, tg)
            url, _ = map_url(card, a)
            done = not a.get('active') and bool(ended)
            out.append(Send(key, car, kind, level, alert_body(card, a, rules, level), url, done,
                            _minutes(a) if done else None, ended if done else None))
            last[f'{car}|{kind}'] = now   # вторая тревога того же вида той же машины в этом же проходе — в окне повтора
        elif rec.phase == 'active' and not a.get('active') and a.get('to'):
            out.append(End(key, car, kind, _minutes(a), a['to'], end_body(card, a)))


def plan(cards: Mapping[str, Mapping[str, Any]], rules: Rules, tg: TgRules, now: datetime,
         records: Mapping[str, Rec]) -> Plan:
    """Что делать сейчас (правила — в описании модуля): действия по порядку машин и тревог и ключи идущих тревог.
    Тревога без записи — начало (или Skip); с записью «идёт», а тревога кончилась — End. Битые данные одной машины не
    останавливают остальные (в журнал)."""
    out: list[Action] = []
    active: set[str] = set()
    quiet = in_quiet(rules, now)
    last = _last_starts(records)
    today = now.astimezone(ac.YEREVAN).date()
    for r in records.values():   # «идёт» с прошлого дня (пропала из журнала без конца) — закрыть: без кнопки и эскалации
        t = _moment(r.sent_at)
        if (r.phase == 'active' and r.key.startswith(('alert:', 'late:')) and t is not None
                and t.astimezone(ac.YEREVAN).date() < today):
            out.append(End(r.key, r.car or '', r.kind, None, now.isoformat(), '', 'day'))
    for car, card in cards.items():
        mine: list[Action] = []
        try:
            _plan_car(car, card, rules, tg, now, records, quiet, last, mine, active)
        except Exception:   # битые данные одной машины не должны остановить тревоги остальных
            logger.exception('[Routes] Тревоги в Telegram: машина %s пропущена', car)
            continue
        out.extend(mine)
    return Plan(out, frozenset(active))


# --- общая «Տեսա» с картой «Մեքենաները առցանց» (live_ack, схема 28; страница — routes_live.js problemsOf / serverAck) ---

def _late_page_key(lines: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Ключ проблемы «не успеет» на странице: к окну или к возврату на склад — late:window, только к плану — late:plan
    (routes_live.js alarmSev); строк нет — None."""
    live = [x for x in lines.values() if not x.get('calm')]
    if not live:
        return None
    return 'late:window' if any(x.get('kind') in ('window', 'return') for x in live) else 'late:plan'


def map_ack_items(rec: Rec) -> tuple[str, list[tuple[str, str, str | None]]] | None:
    """«Տեսա» в Telegram → отметка карты: (день, [(машина, ключ, since)]) для Store.live_ack_put; None — нечего отмечать.
    Тревога: ключ — вид, since — её начало (from), день — дата начала по Еревану. «Не успеет» (одно сообщение на машину
    в день): ключ по идущим строкам (к окну/возврату — late:window, к плану — late:plan), since — null, день — из ключа."""
    if not rec.car:
        return None
    if rec.key.startswith('alert:'):
        start = rec.key.split('|', 2)[2]
        t = _moment(start)
        return (t.astimezone(ac.YEREVAN).date().isoformat(), [(rec.car, rec.kind, start)]) if t is not None else None
    if rec.key.startswith('late:'):
        lines = rec.payload.get('lines') or {}
        live = [x for x in lines.values() if not x.get('calm')]
        keys = sorted({'late:window' if x.get('kind') in ('window', 'return') else 'late:plan' for x in live})
        return (rec.key.rsplit('|', 1)[1], [(rec.car, k, None) for k in keys]) if keys else None
    return None


def map_ack_match(rec: Rec, rows: Sequence[Mapping[str, Any]], tol: timedelta) -> Mapping[str, Any] | None:
    """Отметка карты (строка live_acks дня), которой страница считает проблему записи подтверждённой: та же машина и
    вид, тот же случай — since == начало тревоги (у отклонения — в пределах tol, как sameCase страницы). «Не успеет»:
    ключ строки — нужный (late:window; к плану — и late:window, как serverRow страницы), отметка не раньше начала
    случая записи (sent_at): прежний случай того же дня не в счёт. Нет — None."""
    if not rec.car:
        return None
    if rec.key.startswith('alert:'):
        start = _moment(rec.key.split('|', 2)[2])
        for row in rows:
            if row.get('car') != rec.car or row.get('key') != rec.kind:
                continue
            since = _moment(row.get('since'))
            if since is not None and start is not None and (
                    since == start or (rec.kind == 'deviation' and abs(since - start) <= tol)):
                return row
        return None
    if rec.key.startswith('late:'):
        need = _late_page_key(rec.payload.get('lines') or {})
        began = _moment(rec.sent_at)
        if need is None or began is None:
            return None
        accept = {need, 'late:window'}
        for row in rows:
            at = _moment(row.get('seen_at')) or _moment(row.get('at'))
            if (row.get('car') == rec.car and row.get('key') in accept and at is not None
                    and at >= began):
                return row
    return None


def due_escalations(records: Mapping[str, Rec], active: frozenset[str], tg: TgRules, now: datetime,
                    quiet: bool) -> list[Rec]:
    """🔴 записи, которым пора эскалация: сообщение есть, «Տեսա» никто не нажал escalate_min минут, тревога идёт
    (active), эскалации ещё не было; эскалация выключена (0 минут) или тихие часы — ничего."""
    if tg.escalate_min <= 0 or quiet:
        return []
    wait = timedelta(minutes=tg.escalate_min)
    out = []
    for r in records.values():
        t = _moment(r.payload.get('critical_at')) or _moment(r.sent_at)   # «не успеет» стало 🔴 позже — отсчёт оттуда
        if (r.level == 'critical' and r.phase == 'active' and r.message_id is not None and r.acked_by is None
                and r.escalated_at is None and r.key in active and t is not None and now - t >= wait):
            out.append(r)
    return sorted(out, key=lambda r: r.sent_at)
