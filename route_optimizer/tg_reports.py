# -*- coding: utf-8 -*-
"""Отчёты и ответы на команды Telegram-бота (№91, docs/plans/telegram-bot-plan.md §5–6). Чистые функции — без сети и
базы: данные «Развоза», карточки флота (views._live_cards), записи бота и строки «Վարորդներ» (scorecard.period) →
тексты HTML по-армянски (все данные экранируются).

- план дня (day_plan, plan_text): отправленный водителям план (Draft.for_drivers, №81) — по машине водитель (+ առաքիչ),
  рейсы, точки, тонны и км прогноза сборки, выезд, самое раннее окно приёма её магазинов («մինչև HH:MM»); итог парка.
  Плана нет или он не выпущен (Draft.released) — None;
- итог дня (summary_data, summary_text): по машине плана — доставлено N/M (закрытые водителем, кроме отказа), не
  доставлено (названия), км и литры дня по GPS (карточка), магазины, опаздывавшие за день (записи «не успеет»),
  нарушения по видам (журнал тревог без «փոքր» и объяснённых), балл дня (day_score: те же подоценки, что балл периода,
  без порога MIN_DAYS — у одного дня его нет); итог парка;
- неделя (week_text): места водителей за прошлую неделю (scorecard.period: балл и место — с MIN_DAYS днями), с малым
  числом дней — отдельной строкой; три самых слабых показателя парка (средняя подоценка водителей);
- /where (where_text), /today (today_text), /late (late_text), /help (HELP).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from . import actuals as ac
from . import dispatch as dp
from .live_alerts import esc, hm, late_line

NB = ' '
DELIVERED = ('full', 'partial', 'covered')   # закрыта и товар отдан (refused — закрыта, но не доставлена)
VIOLATION_KINDS = ('speed', 'stop', 'center', 'gps', 'deviation', 'sequence')
VIOLATION_TEXT = {'speed': 'արագություն', 'stop': 'կանգառ', 'center': 'կենտրոն', 'gps': 'GPS', 'deviation': 'շեղում',
                  'sequence': 'հերթականություն'}
METRIC_TEXT = {'on_time': 'Ժամանակին', 'order': 'Հերթականություն', 'speed': 'Արագություն',
               'stops': 'Կանգառ խանութից դուրս', 'liters': 'Վառելիք', 'route': 'Երթուղի'}
STATE_TEXT = {'moving': 'ճանապարհին', 'standing': 'կանգնած է', 'offline': 'կապ չկա', 'alert': 'ահազանգ',
              'closed': 'օրն ավարտված է', 'nodata': 'տվյալ չկա'}
MISSED_SHOWN = 8   # չառաքված магазинов в строке итога — не больше (остальные числом)


def num(x: float | None, d: int = 0) -> str:
    """Число по-армянски: разряды — неразрывный пробел, дробь — запятая; None — «—»."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return '—'
    return f'{x:,.{d}f}'.replace(',', NB).replace('.', ',')


def ddmm(day: str) -> str:
    return f'{day[8:10]}.{day[5:7]}.{day[:4]}'


def _clock(minutes: float) -> str:
    m = int(round(minutes))
    return f'{m // 60:02d}:{m % 60:02d}'


# --- план дня ---

def day_plan(stored: Mapping[str, Any] | None, day: date, drivers: Mapping[str, str], helpers: Mapping[str, str],
             windows: Mapping[int, tuple[float, float]], names: Mapping[str, str | None]) -> dict[str, Any] | None:
    """План дня для отчёта: черновик «Развоза» из базы (store.load_dispatch) → выпущен (released) — то, что видят
    водители (for_drivers), по машине; нет или не выпущен — None. windows — клиент → (не раньше, не позже), минуты."""
    if stored is None:
        return None
    full = dp.Draft.from_json(stored)
    if full.released is None:
        return None
    draft = full.for_drivers()
    pred = (draft.prediction or {}).get('trucks') or {}
    by_truck: dict[str, list[list[int]]] = {}
    for t in draft.trips:
        if t.stops:
            by_truck.setdefault(t.truck, []).append(list(t.stops))
    order = [c for c in draft.trucks if c in by_truck] + sorted(c for c in by_truck if c not in draft.trucks)
    cars = []
    for car in order:
        p = pred.get(car) if isinstance(pred.get(car), Mapping) else {}
        ends = [windows[c][1] for trip in by_truck[car] for c in trip if c in windows and math.isfinite(windows[c][1])]
        cars.append({'car': car, 'name': names.get(car), 'driver': drivers.get(car), 'helper': helpers.get(car),
                     'trips': len(by_truck[car]), 'stops': sum(len(t) for t in by_truck[car]),
                     'kg': p.get('kg') if isinstance(p.get('kg'), (int, float)) else None,
                     'km': p.get('km') if isinstance(p.get('km'), (int, float)) else None,
                     'depart': p.get('depart') if isinstance(p.get('depart'), str) else None,
                     'first_window': _clock(min(ends)) if ends else None})
    return {'day': day.isoformat(), 'released_at': (full.released or {}).get('at'), 'cars': cars}


def _crew(c: Mapping[str, Any]) -> str:
    who = esc(c['driver']) if c.get('driver') else 'վարորդ նշված չէ'
    return who + (f' + {esc(c["helper"])}' if c.get('helper') and c.get('helper') != c.get('driver') else '')


def _tonnes(kg: float | None) -> str:
    return '—' if kg is None else num(kg / 1000.0, 1) + NB + 'տ'


def plan_text(plan: Mapping[str, Any]) -> str:
    """Сообщение «план дня» (без строки «обновлено» — её добавляет запись бота)."""
    lines = [f'📋 <b>Օրվա պլան · {ddmm(plan["day"])}</b>']
    total = {'trips': 0, 'stops': 0, 'kg': 0.0, 'km': 0.0}
    kg_known = km_known = True
    for c in plan['cars']:
        head = f'🚚 <b>{esc(c["car"])}</b>' + (f' · {esc(c["name"])}' if c.get('name') else '') + ' — ' + _crew(c)
        parts = [f'{c["trips"]}{NB}երթ', f'{c["stops"]}{NB}կետ', _tonnes(c.get('kg')),
                 num(c.get('km')) + NB + 'կմ']
        if c.get('depart'):
            parts.append('մեկնում՝ ' + esc(c['depart']))
        if c.get('first_window'):
            parts.append('⏰ մինչև ' + esc(c['first_window']))
        lines += [head, '   ' + ' · '.join(parts)]
        total['trips'] += c['trips']
        total['stops'] += c['stops']
        kg_known &= c.get('kg') is not None
        km_known &= c.get('km') is not None
        total['kg'] += c.get('kg') or 0.0
        total['km'] += c.get('km') or 0.0
    lines.append(f'<b>Ընդամենը՝</b> {len(plan["cars"])}{NB}մեքենա · {total["trips"]}{NB}երթ · '
                 f'{total["stops"]}{NB}կետ · {_tonnes(total["kg"] if kg_known else None)} · '
                 f'{num(total["km"] if km_known else None)}{NB}կմ')
    return '\n'.join(lines)


# --- итог дня ---

def day_score(row: Mapping[str, Any] | None) -> float | None:
    """Балл дня водителя: средневзвешенное подоценок строки (scorecard parts) — без порога MIN_DAYS."""
    parts = (row or {}).get('parts') or {}
    weight = sum(p['weight'] for p in parts.values())
    return round(sum(p['score'] * p['weight'] for p in parts.values()) / weight) if weight else None


def summary_data(plan: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]],
                 fleet: Mapping[str, Mapping[str, Any]], late_seen: Mapping[str, int],
                 score_rows: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Строки итога по машинам плана (правило — в описании модуля); late_seen — машина → магазинов «не успеет» за
    день; score_rows — строки «Վարորդներ» за день (водитель — по имени, без учёта регистра)."""
    scores = {' '.join(str(r.get('name') or '').split()).casefold(): r for r in score_rows or ()
              if r.get('role') == 'driver'}
    out = []
    for c in plan['cars']:
        car = c['car']
        card = cards.get(car) or {}
        stops = [s for s in (fleet.get(car) or {}).get('stops') or () if isinstance(s, Mapping)]
        missed = [str(s.get('name') or s.get('stop_id')) for s in stops if s.get('status') not in DELIVERED]
        kinds: dict[str, int] = {}
        for a in card.get('alerts_log') or ():
            if a.get('kind') in VIOLATION_KINDS and not a.get('minor') and not a.get('explained'):
                kinds[a['kind']] = kinds.get(a['kind'], 0) + 1
        driver = card.get('driver') or c.get('driver')
        row = scores.get(' '.join(str(driver or '').split()).casefold()) if driver else None
        out.append({'car': car, 'driver': driver, 'done': sum(1 for s in stops if s.get('status') in DELIVERED),
                    'total': len(stops), 'missed': missed, 'km': card.get('km'), 'liters': card.get('fuel_l'),
                    'late': late_seen.get(car, 0), 'violations': kinds, 'score': day_score(row),
                    'back': bool(card.get('closed'))})
    return out


def summary_text(day: str, rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [f'🏁 <b>Օրվա ամփոփում · {ddmm(day)}</b>']
    for r in rows:
        mark = '✅' if r['total'] and r['done'] == r['total'] else '⚠️'
        lines.append(f'🚚 <b>{esc(r["car"])}</b>' + (f' · {esc(r["driver"])}' if r.get('driver') else '')
                     + f' — {mark} {r["done"]}/{r["total"]}' + ('' if r['back'] else ' · դեռ չի վերադարձել'))
        if r['missed']:
            shown = ', '.join(esc(x) for x in r['missed'][:MISSED_SHOWN])
            more = len(r['missed']) - MISSED_SHOWN
            lines.append('   Չառաքված՝ ' + shown + (f' և ևս {more}' if more > 0 else ''))
        v = ', '.join(f'{VIOLATION_TEXT[k]} {n}' for k, n in r['violations'].items())
        parts = [num(r.get('km'), 1) + NB + 'կմ', num(r.get('liters'), 1) + NB + 'լ', f'ուշացում՝ {r["late"]}']
        if v:
            parts.append('խախտումներ՝ ' + v)
        if r.get('score') is not None:
            parts.append(f'միավոր՝ {r["score"]}')
        lines.append('   ' + ' · '.join(parts))
    done, total = sum(r['done'] for r in rows), sum(r['total'] for r in rows)
    km = sum(r['km'] for r in rows if isinstance(r.get('km'), (int, float)))
    liters = sum(r['liters'] for r in rows if isinstance(r.get('liters'), (int, float)))
    lines.append(f'<b>Պարկ՝</b> {len(rows)}{NB}մեքենա · {done}/{total}{NB}կետ · {num(km, 1)}{NB}կմ · '
                 f'{num(liters, 1)}{NB}լ · ուշացում՝ {sum(r["late"] for r in rows)} · '
                 f'խախտումներ՝ {sum(sum(r["violations"].values()) for r in rows)}')
    return '\n'.join(lines)


# --- неделя ---

def week_text(monday: date, rows: Sequence[Mapping[str, Any]]) -> str:
    """Рейтинг водителей за неделю monday…monday+6 (строки scorecard.period)."""
    drivers = [r for r in rows if r.get('role') == 'driver']
    ranked = sorted((r for r in drivers if r.get('score') is not None), key=lambda r: (r['rank'], str(r['name'])))
    lines = [f'🏆 <b>Շաբաթվա վարկանիշ · {monday:%d.%m}–{monday + timedelta(days=6):%d.%m}</b>']
    medal = {1: '🥇', 2: '🥈', 3: '🥉'}
    for r in ranked:
        lines.append(f'{medal.get(r["rank"], str(r["rank"]) + ".")} {esc(r["name"])} — {num(r["score"])} '
                     f'({r["days"]}{NB}օր, {r["stops"]}{NB}կետ)')
    few = [r for r in drivers if r.get('score') is None and r.get('days')]
    if few:
        lines.append('Քիչ տվյալ՝ ' + ', '.join(esc(r['name']) for r in few))
    if not ranked and not few:
        lines.append('Անցյալ շաբաթվա տվյալներ չկան։')
    sums: dict[str, list[float]] = {}
    for r in drivers:
        for k, p in (r.get('parts') or {}).items():
            sums.setdefault(k, []).append(p['score'])
    weak = sorted(((sum(v) / len(v), k) for k, v in sums.items() if k in METRIC_TEXT))[:3]
    if weak:
        lines.append('⚠️ Թույլ կողմեր՝ ' + ' · '.join(f'{METRIC_TEXT[k]} {num(s)}/100' for s, k in weak))
    return '\n'.join(lines)


# --- команды ---

HELP = ('🤖 <b>Araqich Dispatch</b>\n'
        '/where [համար] — որտեղ է մեքենան (առանց համարի՝ մեքենաների ցուցակ)\n'
        '/today — այսօրվա առաջընթացը\n'
        '/late — ով է ուշանում\n'
        '/help — այս օգնությունը\n\n'
        '🔴 կրիտիկական (ձայն, «✔ Տեսա», 10 րոպեից պատասխան չկա՝ ղեկավարին) · 🟠 ուշադրություն · ⚪ տեղեկություն '
        '(առանց ձայնի)։ «✔ Տեսա»՝ տեսել եմ, զբաղվում եմ։')


def _ago(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return '—'
    m = int(seconds // 60)
    return 'հենց հիմա' if m < 1 else (f'{m}{NB}րոպե առաջ' if m < 120 else f'{m // 60}{NB}ժամ առաջ')


def find_car(cards: Mapping[str, Mapping[str, Any]], query: str) -> tuple[str | None, list[str]]:
    """Машина по запросу /where: номер без пробелов и регистра — сразу; иначе единственная, у кого запрос в номере,
    названии или имени водителя. → (машина или None, подходящие — для кнопок)."""
    q = ''.join(query.split()).casefold()
    if not q:
        return None, sorted(cards)
    for car in cards:
        if ''.join(car.split()).casefold() == q:
            return car, [car]
    hits = sorted(car for car, c in cards.items()
                  if any(q in ''.join(str(x or '').split()).casefold() for x in (car, c.get('name'), c.get('driver'))))
    return (hits[0] if len(hits) == 1 else None), hits


def where_text(car: str, c: Mapping[str, Any]) -> str:
    """Карточка /where: где (точка — отдельным sendLocation), состояние и скорость, последняя связь, следующий магазин и
    ETA, груз и точки."""
    lines = [f'📍 <b>{esc(car)}</b>' + (f' · {esc(c["name"])}' if c.get('name') else '')
             + (f' — {esc(c["driver"])}' if c.get('driver') else '')]
    pos = c.get('position') or {}
    state = STATE_TEXT.get(c.get('state'), '—')
    speed = pos.get('speed_kmh')
    lines.append('Վիճակ՝ ' + state + (f', {speed}{NB}կմ/ժ' if isinstance(speed, (int, float)) and speed > 0 else ''))
    lines.append(f'Վերջին կապը՝ {_ago(c.get("contact_age_s"))}' + (f' ({hm(c["last_contact"])})' if c.get('last_contact') else ''))
    if not pos:
        lines.append('Դիրքը հայտնի չէ։')
    nxt = c.get('next')
    if isinstance(nxt, Mapping):
        if nxt.get('here'):
            lines.append(f'Հիմա խանութում՝ {esc(nxt.get("name") or nxt.get("stop_id"))}')
        else:
            eta = f' — ≈ {hm(nxt["eta"])}' if nxt.get('eta') else (f' — պլանով {hm(nxt["planned_eta"])}'
                                                                    if nxt.get('planned_eta') else '')
            delay = nxt.get('delay_min')
            late = f' ({"+" if delay > 0 else ""}{delay}{NB}րոպե պլանից)' if isinstance(delay, int) and delay else ''
            lines.append(f'Հաջորդ խանութը՝ {esc(nxt.get("name") or nxt.get("stop_id"))}{eta}{late}')
    load = c.get('load') or {}
    stores = c.get('stores') or {}
    lines.append(f'Բեռը՝ {num(load.get("remaining_kg"))}{NB}կգ · կետեր՝ {stores.get("done", 0)}/{stores.get("total", 0)}')
    if c.get('return_eta'):
        lines.append('Վերադարձ պահեստ՝ ≈ ' + hm(c['return_eta']))
    return '\n'.join(lines)


def today_text(cards: Mapping[str, Mapping[str, Any]], now: datetime) -> str:
    """/today: машина — точки N/M, опаздывающих магазинов, связь; итог парка."""
    lines = [f'📊 <b>Այսօր · {now.astimezone(ac.YEREVAN):%d.%m %H:%M}</b>']
    done = total = 0
    for car in sorted(cards):
        c = cards[car]
        if not c.get('planned') and not (c.get('stores') or {}).get('total') and not c.get('position'):
            continue
        st = c.get('stores') or {}
        done += st.get('done', 0)
        total += st.get('total', 0)
        late = len(c.get('late') or ())
        net = '📵 ' + STATE_TEXT['offline'] if c.get('state') == 'offline' else (
            '🏁' if c.get('closed') else '📶 ' + _ago(c.get('contact_age_s')))
        lines.append(f'🚚 <b>{esc(car)}</b>' + (f' {esc(c["driver"])}' if c.get('driver') else '')
                     + f' — {st.get("done", 0)}/{st.get("total", 0)}' + (f' · ⏰{NB}{late}' if late else '') + f' · {net}')
    if len(lines) == 1:
        lines.append('Այսօր մեքենաների տվյալներ դեռ չկան։')
    else:
        lines.append(f'<b>Ընդամենը՝</b> {done}/{total}{NB}կետ')
    return '\n'.join(lines)


def late_text(cards: Mapping[str, Mapping[str, Any]]) -> str:
    """/late: машины с прогнозом «не успеет» — строки по магазинам и возврату."""
    lines = ['⏰ <b>Ով է ուշանում</b>']
    for car in sorted(cards):
        rows = [x for x in cards[car].get('late') or () if isinstance(x, Mapping)]
        if rows:
            c = cards[car]
            lines.append(f'🚚 <b>{esc(car)}</b>' + (f' · {esc(c["driver"])}' if c.get('driver') else ''))
            lines += ['   ' + late_line(x) for x in rows]
    if len(lines) == 1:
        lines.append('Այս պահին ուշացող չկա ✅')
    return '\n'.join(lines)
