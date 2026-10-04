# -*- coding: utf-8 -*-
"""Проверочный набор для чата «Հարցրու AI-ին» на странице «Развоз» (ответы владельца №52, №55): вопросы по настоящим
дням, правильные ответы выводятся из тех же цифр дня, что видит страница; ответы модели проверяются по фактам.

Запуск из корня проекта:  python tests/routes_dispatch_ai_eval.py 2026-10-02 2026-10-04 [--out отчёт.json]
Стоимость — около $0.3 на день (≈20 вопросов, Claude Sonnet 5.5, данные дня из кэша). Вызывает настоящий API
(ANTHROPIC_API_KEY из .env) и читает ERP (только SELECT). База настроек не трогается: скрипт работает на временной
копии route_optimizer.db (и courier.db), «что если» считает в памяти. Имя без test_ — pytest его не собирает.
Прогонять после каждой правки инструкции модели (ai_chat.SYSTEM) или данных дня (ai_chat.day_context).

Что проверяется у каждого ответа:
  факты — номер машины, время (ЧЧ:ММ), числа (целые: отдельным числом, не частью времени или большего числа;
          км и литры — с допуском округления), название магазина;
  поведение — «перенеси/поменяй» не выполняется, а называется кнопка «Փոփոխել»; к «администратору» не отправляет;
          «что если по машинам» — вызвана пересборка в памяти и сказано, как применить («Վերակազմել»);
  форма — без названий полей JSON и английских слов из данных, без «ֆ» вместо драма, без разметки markdown;
          вопрос по-русски — ответ по-русски.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Во всех ответах — запрещено (регистр не важен)
FORBIDDEN = [
    (r'\b(poor|over_time|explain|others|no_room|no_window|no_center|no_vehicle|trip_no|stops_count|load_pct|eta|'
     r'unassigned|baseline|planner|car_code|capacity_kg)\b', 'название поля или английское слово'),
    (r'\d\s?ֆ(?![ա-ֆ])', '«ֆ» вместо драма'),
    (r'ադմինիստրատոր', 'отправляет к администратору'),
    (r'\*\*|^\s*#', 'разметка markdown'),
]


def norm(text: str) -> str:
    """Пробелы-разделители разрядов («3 872», «1 130 859») убрать, неразрывные — в обычные; число после времени
    («17:30 120») не приклеивается."""
    text = text.replace(chr(0xa0), ' ').replace(chr(0x202f), ' ')
    return re.sub(r'(?<![\d:.,])\d{1,3}(?: \d{3})+(?!\d)', lambda m: m.group(0).replace(' ', ''), text)


def has_time(answer: str, hhmm: str) -> bool:
    h, m = hhmm.split(':')
    return re.search(r'(?<!\d)0?%d:%s(?!\d)' % (int(h), m), answer) is not None


def has_count(answer: str, n: int, unit: str) -> bool:
    """Счёт рядом со своей единицей: «3 երթ», «17 կետ», «41%» — а не любое «3» в ответе («5 երթ, որից 3-ը»)."""
    return re.search(r'(?<![\d:.,])%d(?![\d:]|[.,]\d)[^\d\n]{0,14}?(?:%s)' % (n, unit), answer, re.I) is not None


def has_number(answer: str, n: int | float) -> bool:
    return re.search(r'(?<![\d:.,])%s(?![\d:]|[.,]\d)' % re.escape(str(n)), answer) is not None


def has_close(answer: str, values: list) -> bool:
    """Число с дробью («484,3») рядом с ожидаемым (км, литры, кг: допуск — округление до целого)."""
    found = [float(x.replace(',', '.')) for x in re.findall(r'(?<![\d:])\d+(?:[.,]\d+)?(?![\d:])', answer)]
    return any(abs(f - v) < 1.0 for f in found for v in values)


def clock(s: str) -> int:
    m = re.match(r'(\d{1,2}):(\d{2})(?: \(\+(\d+)\))?', s or '')
    return int(m.group(3) or 0) * 1440 + int(m.group(1)) * 60 + int(m.group(2)) if m else -1


def around(x: float) -> list[int]:
    return sorted({math.floor(x), math.ceil(x), round(x)})


def word(name: str) -> str:
    """Самое длинное слово названия магазина — его ищем в ответе (кавычки и «ՍՊԸ/ԱՁ» не важны)."""
    words = [w for w in re.findall(r'[\wԱ-֏]+', name or '') if len(w) >= 4 and w not in ('ՍՊԸ',)]
    return max(words, key=len) if words else (name or '')


def questions(body: dict) -> list[dict]:
    """Вопросы дня с ожидаемыми фактами; вопрос пропускается, если в дне нет нужного (нет рейсов с 2 рейсами и т. п.)."""
    plan = body.get('plan')
    if not plan or not plan.get('trucks'):
        return []
    names = {t['car_code']: t.get('name') or t['car_code'] for t in body['trucks']}
    label = lambda code: '%s · %s' % (names.get(code, code), code)
    pt = plan['trucks']
    sm = plan['summary']
    out: list[dict] = []

    def q(qid, text, must=(), numbers=(), times=(), previews=False, lang='hy', must_not=(), counts=(), kg=None):
        out.append({'id': qid, 'q': text, 'must': [list(g) for g in must], 'numbers': [list(g) for g in numbers],
                    'times': list(times), 'previews': previews, 'lang': lang, 'must_not': list(must_not),
                    'counts': [list(c) for c in counts], 'kg': kg})

    last = max(pt, key=lambda t: clock(t['return']))
    q('last_return', 'Ո՞ր մեքենան է ամենաուշը վերադառնում պահեստ, և ժամը քանիսի՞ն։', must=[[last['car_code']]],
      times=[last['return'].split(' ')[0]])
    q('trips_total', 'Ընդհանուր քանի՞ երթ կա այս օրը։', counts=[(sm['trips'], 'երթ')])
    q('stops_total', 'Քանի՞ խանութ է մտել երթերի մեջ։', counts=[(sm['stops'], 'խանութ|կետ|կանգառ')])
    q('km_total', 'Ընդհանուր քանի՞ կիլոմետր են անցնելու մեքենաները։', numbers=[around(sm['km'])])
    if sm.get('liters'):
        q('liters_total', 'Որքա՞ն դիզել կծախսվի ընդհանուր։', numbers=[around(sm['liters'])])
    cap = {x['car_code']: x.get('capacity_kg') or 0 for x in body['trucks']}
    trip_free = sorted(((cap.get(t['car_code'], 0) - tr['kg'], t['car_code']) for t in pt for tr in t['trips']), reverse=True)
    if len(trip_free) == 1 or trip_free[0][0] - trip_free[1][0] >= 50:      # спорные почти равные — не спрашиваем
        q('most_free', 'Ո՞ր երթում է ամենաշատ ազատ տեղը (բեռնատարողությունից հանած երթի բեռը)։', must=[[trip_free[0][1]]])
    big = max(pt, key=lambda t: (len(t['trips']), t['stops']))
    t1 = big['trips'][0]
    q('trip_stops', 'Քանի՞ կետ ունի %s-ի երթ 1-ը։' % label(big['car_code']), counts=[(len(t1['stops']), 'կետ|խանութ|կանգառ')])
    q('depart', 'Ժամը քանիսի՞ն է առաջին անգամ մեկնում %s-ը։' % label(big['car_code']), times=[t1['depart']])
    q('truck_kg', 'Քանի՞ կգ է տանում %s-ը ընդհանուր։' % label(big['car_code']), kg=big['kg'])
    if t1.get('load_pct') is not None:
        q('load_pct', 'Քանի՞ տոկոսով է լցված %s-ի երթ 1-ը։' % label(big['car_code']), counts=[(t1['load_pct'], '%|տոկոս')])
    lt = big['trips'][-1]
    q('last_stop', 'Ո՞րն է %s-ի վերջին երթի վերջին կանգառը։' % label(big['car_code']), must=[[word(lt['stops'][-1]['name'])]])
    if len(t1['stops']) >= 3 and t1['stops'][2].get('eta'):
        s3 = t1['stops'][2]
        q('stop_eta', 'Ժամը քանիսի՞ն է %s-ը հասնում «%s» խանութ։' % (label(big['car_code']), s3['name']), times=[s3['eta']])
    if body['orders'].get('no_coords'):
        q('no_coords', 'Քանի՞ խանութի տեղն է բացակայում քարտեզում։', counts=[(body['orders']['no_coords'], 'խանութ')])
    base = plan.get('baseline')
    if base and base.get('km') is not None and base['km'] - sm['km'] >= 1:
        q('vs_managers', 'Քանի՞ կմ-ով է այս պլանը կարճ, քան եթե տանեին ըստ մենեջերների։', numbers=[around(base['km'] - sm['km'])])
    busy = {t['car_code'] for t in pt}
    idle = [t['car_code'] for t in body['trucks'] if t.get('selected') and t['car_code'] not in busy]
    if 1 <= len(idle) <= 3:
        q('idle', 'Ո՞ր նշված մեքենաները այսօր երթ չունեն։', must=[[c] for c in idle])
    poor = [(t['car_code'], i + 1) for t in pt for i, tr in enumerate(t['trips']) if tr.get('poor')]
    if len(poor) == 1:
        q('poor', 'Ո՞ր երթի ապրանքն է նվազագույնից պակաս։', must=[[poor[0][0]]])
    q('move_refuse', 'Տեղափոխիր «%s» խանութը մեկ այլ մեքենայի։' % lt['stops'][0]['name'],
      must=[['Փոփոխել']], must_not=[r'տեղափոխեցի', r'տեղափոխված է', r'արդեն տեղափոխ'])
    q('who_settings', 'Ո՞վ կարող է փոխել մեքենայի բեռնատարողությունը և որտեղ։', must=[['կարգավորում']])
    if len(pt) >= 2:
        q('whatif_remove', 'Ի՞նչ կլինի, եթե %s-ը այսօր չաշխատի։' % label(pt[-1]['car_code']), must=[['Վերակազմել']], previews=True)
    spare = [t['car_code'] for t in body['trucks'] if t.get('ready') and not t.get('selected')]
    if spare:
        q('whatif_add', 'Ի՞նչ կլինի, եթե ավելացնենք նաև %s-ը։' % label(spare[0]), must=[['Վերակազմել']], previews=True)
    q('russian', 'Какая машина возвращается последней и во сколько?', must=[[last['car_code']]],
      times=[last['return'].split(' ')[0]], lang='ru')
    return out


def grade(item: dict, result: dict | None, error: str | None) -> list[str]:
    if error:
        return ['ошибка: ' + error]
    answer = norm(result['answer'])
    low = answer.lower()
    bad = []
    for group in item['must']:
        if not any(str(x).lower() in low for x in group):
            bad.append('нет: ' + ' / '.join(map(str, group)))
    for group in item['numbers']:
        exact = len(group) == 1          # счёт (рейсы, точки, проценты) — ровно; around() — с допуском округления
        if not any(has_number(answer, n) for n in group) and (exact or not has_close(answer, group)):
            bad.append('нет числа: ' + ' / '.join(map(str, group)))
    for n, unit in item.get('counts', []):
        if not has_count(answer, n, unit):
            bad.append('нет счёта: %s %s' % (n, unit))
    if item.get('kg') is not None:
        kg = item['kg']                      # «3872 կգ» или «3,9 տ»
        tonnes = re.findall(r'(?<![\d:])(\d+(?:[.,]\d+)?)\s*տ(?:ոննա)?(?![ա-ֆ])', answer)
        if not has_close(answer, around(kg)) and not any(abs(float(x.replace(',', '.')) * 1000 - kg) <= 60 for x in tonnes):
            bad.append('нет веса: %s кг' % kg)
    for t in item['times']:
        if not has_time(answer, t):
            bad.append('нет времени: ' + t)
    if item['previews'] and not result.get('previews'):
        bad.append('не пересчитал «что если»')
    for rx, why in FORBIDDEN:
        if re.search(rx, answer, re.I | re.M):
            bad.append(why)
    for rx in item['must_not']:
        if re.search(rx, answer, re.I):
            bad.append('запрещено: ' + rx)
    if item['lang'] == 'ru' and not re.search(r'[а-яё]', low):
        bad.append('ответ не по-русски')
    if item['lang'] == 'hy' and not re.search(r'[ա-ֆ]', low):
        bad.append('ответ не по-армянски')
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('days', nargs='+', help='дни ГГГГ-ММ-ДД')
    ap.add_argument('--db', default=str(ROOT / 'route_optimizer.db'), help='база настроек (копируется во временную)')
    ap.add_argument('--out', help='JSON-отчёт с ответами')
    ap.add_argument('--env', default=str(ROOT / '.env'), help='.env дашборда (ключ API, ERP)')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='ai_eval_'))
    if not Path(args.db).is_file():
        sys.exit('нет базы настроек: %s' % args.db)
    # обе базы — только временные копии (courier.db нет — пустая временная): настоящие файлы не открываются
    for src, env in ((Path(args.db), 'ROUTES_DB_PATH'), (Path(args.db).with_name('courier.db'), 'COURIER_DB_PATH')):
        dst = tmp / src.name
        if src.is_file():
            a, b = sqlite3.connect('file:%s?mode=ro' % src.as_posix(), uri=True), sqlite3.connect(str(dst))
            a.backup(b)
            a.close()
            b.close()
        os.environ[env] = str(dst)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    load_dotenv(args.env)
    import app_v2
    from route_optimizer import ai_chat, views

    report, total, passed = [], 0, 0
    started = time.time()
    for raw in args.days:
        day = date.fromisoformat(raw)
        with app_v2.app.test_request_context('/api/routes/dispatch'):
            state = views._state()
            dd = views._load_day(state, views._bundle(state), day)
            body = views._dispatch_body(dd)
            items = questions(body)
            if not items:
                print('%s: нет рейсов — пропуск' % raw)
                continue
            print('\n=== %s: %d вопросов' % (raw, len(items)))
            for item in items:
                t0 = time.time()
                result, error = None, None
                try:
                    result = ai_chat.ask(body, item['q'], [], None, simulate=lambda codes: views._ai_preview(dd, codes))
                except ai_chat.AiError as e:
                    error = str(e)
                bad = grade(item, result, error)
                total += 1
                passed += not bad
                print('%s %-14s %4.1fs %s' % ('OK  ' if not bad else 'FAIL', item['id'], time.time() - t0,
                                             '' if not bad else '; '.join(bad)))
                if bad and result:
                    print('     ' + result['answer'].replace('\n', '\n     '))
                report.append({'day': raw, **item, 'answer': result and result['answer'],
                               'previews': result and result.get('previews'), 'error': error, 'problems': bad})
    print('\nИтого: %d из %d (%.0f%%) за %.0f с' % (passed, total, 100 * passed / max(total, 1), time.time() - started))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
