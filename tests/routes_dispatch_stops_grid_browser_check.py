# -*- coding: utf-8 -*-
"""Таблица «Խանութներ» под картой в виде AG Grid, тема Quartz (владелец 11.11.2026, «профессиональный грид, бесплатный»).

Запуск из корня проекта:  python tests/routes_dispatch_stops_grid_browser_check.py [папка для снимков]
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и день — из
tests/routes_dispatch_browser_check.py; к ответу /api/routes/dispatch добавлены магазины с неизвестным весом, длинным
адресом и окном приёма. Порт 8834. Библиотека AG Grid на странице не подключается — только её вид.

A 1920: рамка 1 px со скруглением 8 px; шапка темнее панели, с разделителями столбцов (у всех видимых, кроме последнего);
  строки не ниже 44 px; «քաշն անհայտ է» — плашкой в одну строку; адрес — не больше двух строк, третья не видна;
  у рейса — полоска цвета машины; итог — тоном шапки;
B «Փոփոխել»: рейс без отступов карточки (в рамке грида), кнопки строк на месте;
C тёмная тема: шапка своя (не как в светлой), рамка видна;
D 1366 и 1101: вбок страница не прокручивается, таблица не шире панели;
E 1000: прежние строки-карточки (без рамки грида, шапки нет).
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import copy
import json
import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8834
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
EXTRA = [('Հարություն Խաչատրյան ԱՁ', 'Երևան, Քանաքեռ-Զեյթուն, Ռուբինյանց 24/1', 0, None),
         ('Նաիրա Սարգսյան ԱՁ', 'Երևան, Քանաքեռ-Զեյթուն, Կարապետ Ուլնեցու 3-րդ փողոց, 5 շենքի կից, մուտքը բակից', 66, None),
         ('Անդակո ՍՊԸ', 'Երևան Ահարոնյան 1/6 ++', 210, {'kind': 'before', 't1': 720})]
GRID = """() => {
    const st = document.getElementById('dpStopsTable'), cs = getComputedStyle(st), panel = getComputedStyle(document.getElementById('dpStops'));
    const head = st.querySelector('.dp-st-h'), hs = getComputedStyle(head);
    const cols = [...head.children].filter(c => getComputedStyle(c).display !== 'none' && !c.classList.contains('rt-sr-only'));
    const seps = cols.map(c => getComputedStyle(c, '::after').content !== 'none');
    const rows = [...st.querySelectorAll('.dp-st-r[data-cid]')].map(r => Math.round(r.getBoundingClientRect().height));
    const unk = [...st.querySelectorAll('.dp-st-c-kg.dp-kg-unknown')].map(k => ({ h: k.getBoundingClientRect().height, ws: getComputedStyle(k).whiteSpace,
        bg: getComputedStyle(k).backgroundColor }));
    const addr = [...st.querySelectorAll('.dp-st-r[data-cid] .dp-st-c-addr')].map(a => { const s = getComputedStyle(a), lh = parseFloat(s.lineHeight) || 20;
        return { h: a.clientHeight, lh, pt: parseFloat(s.paddingTop), pb: parseFloat(s.paddingBottom), lines: Math.round(a.clientHeight / lh) }; });
    const trip = st.querySelector('.dp-st-triphead'), total = st.querySelector('.dp-st-total');
    const r = st.getBoundingClientRect(), p = document.getElementById('dpStops').getBoundingClientRect();
    return { radius: cs.borderTopLeftRadius, border: cs.borderTopWidth, borderColor: cs.borderTopColor, headBg: hs.backgroundColor, panelBg: panel.backgroundColor,
        headShown: hs.display !== 'none', seps, rows, unk, addr, tripShadow: trip ? getComputedStyle(trip).boxShadow : '',
        totalBg: total ? getComputedStyle(total).backgroundColor : '', right: r.right, panelRight: p.right,
        side: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}"""


def enrich(route):
    resp = route.fetch()
    data = resp.json()
    plan = data.get('plan')
    if isinstance(plan, dict):
        for t in plan.get('trucks', []):
            for tr in t.get('trips', []):
                base = tr['stops']
                extra = []
                for i, (name, addr, kg, win) in enumerate(EXTRA):
                    s = copy.deepcopy(base[i % len(base)])
                    s.update(customer_id=9000 + i, code=str(12800 + i), name=name, address=addr, kg=kg, window=win)
                    extra.append(s)
                tr['stops'] = base + extra
    route.fulfill(response=resp, body=json.dumps(data), content_type='application/json')


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-grid-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1920, 'height': 945})
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                ctx.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)

            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.evaluate("document.getElementById('dpPrepOpen')?.click()")
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.evaluate("document.getElementById('dpBuild').click()")
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            ctx.route('**/api/routes/dispatch?date=*', enrich)
            page.reload()                  # мастер сборки остаётся открытым — после перезагрузки его нет
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.wait_for_selector('#dpStops:not([hidden])', timeout=30000)
            page.wait_for_timeout(800)
            page.locator('#dpStops').scroll_into_view_if_needed()

            # A
            g = page.evaluate(GRID)
            check(g['radius'] == '8px' and g['border'] == '1px', f"A рамка 1 px, скругление 8 px ({g['border']}, {g['radius']})")
            check(g['headShown'] and g['headBg'] != g['panelBg'], f"A шапка видна и темнее панели ({g['headBg']} / {g['panelBg']})")
            check(len(g['seps']) >= 5 and all(g['seps'][:-1]) and not g['seps'][-1], f"A разделители у столбцов шапки, кроме последнего {g['seps']}")
            check(g['rows'] and min(g['rows']) >= 44, f"A строки не ниже 44 px {g['rows']}")
            check(g['unk'] and all(u['ws'] == 'nowrap' and u['h'] < 26 and u['bg'] not in ('rgba(0, 0, 0, 0)', 'transparent') for u in g['unk']),
                  f"A неизвестный вес — плашка в одну строку {g['unk']}")
            check(g['addr'] and all(a['lines'] <= 2 and a['pt'] == 0 and a['pb'] == 0 for a in g['addr']),
                  f"A адрес не больше двух строк, без вертикального отступа (третья строка не видна) {[a['lines'] for a in g['addr']]}")
            check('inset' in g['tripShadow'], f"A полоска цвета машины у рейса ({g['tripShadow']})")
            check(g['totalBg'] == g['headBg'], 'A итог — тоном шапки')
            check(g['side'] <= 0, f"A вбок не прокручивается ({g['side']})")
            if shots:
                page.locator('#dpStops').screenshot(path=str(shots / 'grid-1920.png'))

            # B
            page.locator('#dpStops .dp-st-triphead .dp-editbtn').first.click()
            page.wait_for_timeout(500)
            ed = page.evaluate("""() => { const t = document.querySelector('#dpStops .dp-trip.is-editing'); if (!t) return null;
                const s = getComputedStyle(t); return { m: s.marginLeft, p: s.paddingTop, acts: t.querySelectorAll('.dp-stop-acts').length,
                rows: t.querySelectorAll('.dp-st-r[data-cid]').length }; }""")
            check(ed and ed['m'] == '0px' and ed['p'] == '0px', f'B рейс «Փոփոխել» без отступов карточки {ed}')
            check(ed and ed['acts'] == ed['rows'] and ed['rows'] > 0, f'B кнопки у каждой строки {ed}')
            if shots:
                page.locator('#dpStops').screenshot(path=str(shots / 'grid-edit-1920.png'))
            page.locator('#dpStops .dp-st-triphead .dp-editbtn').first.click()
            page.wait_for_timeout(400)

            # C
            page.evaluate("document.documentElement.setAttribute('data-rt-theme', 'dark')")
            page.wait_for_timeout(200)
            d = page.evaluate(GRID)
            check(d['headBg'] != g['headBg'] and d['headBg'] != d['panelBg'] and d['borderColor'] != g['borderColor'],
                  f"C тёмная тема: своя шапка и рамка ({d['headBg']}, {d['borderColor']})")
            if shots:
                page.locator('#dpStops').screenshot(path=str(shots / 'grid-dark-1920.png'))
            page.evaluate("document.documentElement.setAttribute('data-rt-theme', 'light')")

            # D
            for w in (1366, 1101):
                page.set_viewport_size({'width': w, 'height': 900})
                page.wait_for_timeout(500)
                x = page.evaluate(GRID)
                check(x['side'] <= 0 and x['right'] <= x['panelRight'] + 0.5, f"D {w}: вбок не прокручивается, таблица в панели ({x['side']}, {x['right']:.0f} ≤ {x['panelRight']:.0f})")
                check(x['addr'] and all(a['lines'] <= 2 for a in x['addr']), f"D {w}: адрес не больше двух строк")
                if shots:
                    page.locator('#dpStops').screenshot(path=str(shots / f'grid-{w}.png'))

            # E
            page.set_viewport_size({'width': 1000, 'height': 900})
            page.wait_for_timeout(500)
            e = page.evaluate("""() => { const st = document.getElementById('dpStopsTable'), h = st.querySelector('.dp-st-h');
                return { border: getComputedStyle(st).borderTopWidth, head: h ? getComputedStyle(h).display : 'none' }; }""")
            check(e['border'] == '0px' and e['head'] == 'none', f'E 1000: прежние строки-карточки {e}')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'ошибок страницы и консоли нет' + ('' if not errors else ': ' + '; '.join(errors[:5])))
    print(f'{sum(results)}/{len(results)} OK')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
