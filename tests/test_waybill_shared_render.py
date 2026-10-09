# -*- coding: utf-8 -*-
"""Лист «Բեռնագիր» — один рендер на две страницы: window.RtWaybill в static/js/base.js (его грузят base_v2.html обеих и он
открыт снаружи, araqich.orix.am) печатает и «Развоз», и склад «Պահեստ» (ответ владельца после №78). Своей копии рендера ни
у одной страницы нет; склад берёт данные только своим API. Рендер — в node, если он есть (без node — пропуск).

Запуск:  python -m pytest tests/test_waybill_shared_render.py -q
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'js'


def _read(name):
    return (JS / name).read_text(encoding='utf-8')


def test_one_renderer_for_both_pages():
    base, dispatch, warehouse = _read('base.js'), _read('routes_dispatch.js'), _read('routes_warehouse.js')
    assert base.count('window.RtWaybill = ') == 1 and 'function waybillHtml(t, d, wb)' in base
    for page in (dispatch, warehouse):
        assert 'function waybillHtml' not in page and 'ԲԵՌՆԱԳԻՐ' not in page and 'window.RtWaybill.html(' in page
    assert 'const wbName = (r) => window.RtWaybill.name(r);' in dispatch                   # Excel — те же строки,
    assert 'const wbNotes = (tr) => window.RtWaybill.notes(tr);' in dispatch               # при вызове, не при загрузке
    assert '/api/routes/warehouse/waybill?' in warehouse and '/api/routes/dispatch' not in warehouse
    assert 'Բեռնման հերթականություն' not in dispatch                                   # и в Excel — только итог по товару
    tpl = (ROOT / 'templates' / 'base_v2.html').read_text(encoding='utf-8')
    assert "filename='js/base.js') }}?v=5\"" in tpl                                    # новый base.js — мимо кэша
    page = (ROOT / 'templates' / 'routes_warehouse.html').read_text(encoding='utf-8')
    assert "routes_warehouse.js') }}?v=8" in page and "routes_warehouse.css') }}?v=5" in page


NODE = r'''
global.window = {};
require(process.argv[1]);
const W = window.RtWaybill;
const row = { code: '0101', name: 'Կաթ <1լ>', unit: 'հատ', qty: 30, pack: 12, packs: 2, loose: 6, kg: 31.5, unknown: false };
const wb = { rev: 7, driver: 'Արամ', helper: null, trips: [
    { id: 4, no: 1, loading_start: '08:10', depart: '08:40', stops: 3, kg: 31.5, orders: 2, invoiced: 2, rows: [row],
      loading: [{ no: 1, stop: 2, code: 'C2', name: 'Խանութ <2>', split: true, kg: 31.5, rows: [row] },
                { no: 2, stop: 1, code: 'C1', name: 'Խանութ 1', split: false, kg: 0, rows: [] }] },
    { id: 9, no: 2, loading_start: '12:00', depart: '12:20', stops: 1, kg: 0, orders: 1, invoiced: 0, rows: [] }] };
process.stdout.write(JSON.stringify({ html: W.html({ car_code: 'CAR1', name: 'HOWO' }, { day: '2026-10-06', weekday: 2 }, wb),
    unknown: W.name({ unknown: true, product_id: 77 }), notes: W.notes(wb.trips[1]),
    gift: W.name({ name: 'Գառնի 6լ', qty: 11, gift: 1 }), nogift: W.name({ name: 'Գառնի 6լ', qty: 10 }),
    api: Object.keys(W).sort() }));
'''


@pytest.mark.skipif(shutil.which('node') is None, reason='нет node')
def test_renderer_output():
    out = subprocess.run(['node', '-e', NODE, str(JS / 'base.js')], capture_output=True, check=True, timeout=60)
    got = json.loads(out.stdout.decode('utf-8'))
    html = got['html']
    assert html.startswith('<!doctype html><html lang="hy">') and html.endswith('</body></html>')
    assert html.count('<section class="sheet"><h1>ԲԵՌՆԱԳԻՐ</h1>') == 2
    assert '<b>HOWO · CAR1</b> · երեքշաբթի, 06.10.2026 · Երթ 1 / 2' in html
    assert 'Կաթ &lt;1լ&gt;' in html and '<1լ>' not in html                              # с сервера — через esc
    assert '2 փաթեթ + 6 հատ' in html and 'Վարորդ՝ <b>Արամ</b>' in html and 'պլան № 7' in html
    assert 'Ապրանքներ չկան' in html
    assert got['unknown'] == 'ERP-ում անհայտ ապրանք (ID 77)'
    # №90: подарки ERP — уже в количестве строки; лист и Excel пишут, сколько из них подарки
    assert (got['gift'], got['nogift']) == ('Գառնի 6լ · այդ թվում՝ 1 նվեր', 'Գառնի 6լ')
    assert got['notes'] == ['Քանակները՝ պատվերներից․ ապրանքագրեր դեռ չկան։']
    # владелец 09.10 «не печатай раздельно по магазинам, нужно общее количество по SKU»: только итог рейса по товару —
    # блока погрузки по магазинам (№87 п. 4) нет, даже если сервер прислал tr.loading
    assert 'Բեռնման հերթականություն' not in html and 'Խանութ' not in html and 'կետ №' not in html
    assert html.count('<td class="c">0101</td>') == 1                                  # строка товара — один раз
    assert got['api'] == ['html', 'name', 'notes']
