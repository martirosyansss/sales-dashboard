# -*- coding: utf-8 -*-
"""«Excel ERP-ի համար» (владелец 13.10, route_optimizer.erp_import): առաքիչ и машина заказов отправленного плана — в
официальный шаблон ArmSoft SalesOrder.xlsm (лист «Խմբագրում»); в ERP загружает оператор, сайт не пишет. Здесь: сборка
файла (строки, пересчёты выключены, таблицы строк только «добавлять», остальной архив байт-в-байт, экранирование \r и
_x, битый шаблон), SQL шапки заказа (только чтение) и API (только админ, план не отправлен, прошедший день, файл, проба
одним заказом, заказ с адресом доставки — пропуск, нечего менять). Ни ERP, ни CT115 не трогаются.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_erp_import.py -q
"""
import io
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import erp  # noqa: E402
from route_optimizer import erp_import as ei  # noqa: E402
from route_optimizer import erp_write as ew  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import OrderState, check_sql  # noqa: E402
from test_route_dispatch_erp_write import AGENTS, API_CARS, CAR_DRIVERS, CIDS, NOW, body  # noqa: E402
from test_route_dispatch_same_day import DAY, _build, _page_setup, _plan_stops, client  # noqa: E402,F401
from test_route_optimizer import _SQL_WRITE_RE, _isn  # noqa: E402

TEMPLATE = ei.TEMPLATE.read_bytes()


def _row(**kw):
    base = dict(doc_num='000000346783', day=date(2026, 10, 14), agent='A008/6', customer='10837', courier='B008/10',
                car='333DN33', sales_area='108', comment='', price_type='01')
    return ei.ImportRow(**{**base, **kw})


def _parts(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n) for n in z.namelist()}


def _cells(xml, row):
    m = re.search(rf'<row r="{row}">(.*?)</row>', xml)
    assert m, row
    out = {}
    for c in re.finditer(r'<c r="([A-Z]+)\d+"[^>]*>(.*?)</c>', m.group(1)):
        t = re.search(r'<t[^>]*>(.*?)</t>', c.group(2))
        out[c.group(1)] = t.group(1) if t else re.search(r'<v>(.*?)</v>', c.group(2)).group(1)
    return out


# ============================== файл ==============================

def test_template_is_official_armsoft_with_edit_button():
    parts = _parts(TEMPLATE)
    assert 'xl/vbaProject.bin' in parts
    sheets = ei._sheet_paths(parts)
    assert {ei.EDIT_SHEET, ei.SETTINGS_SHEET} <= set(sheets)
    assert b'macro="[0]!EditDocument"' in parts[sheets[ei.EDIT_SHEET]]


def test_build_fills_edit_sheet_and_keeps_everything_else():
    out = ei.build_xlsm(TEMPLATE, [_row(), _row(doc_num='000000346784', courier='B001/1', car='', priority=0,
                                            delay_days=3, submit_date=date(2026, 10, 15),
                                            delivery_date=date(2026, 10, 16))])
    src, dst = _parts(TEMPLATE), _parts(out)
    assert list(src) == list(dst)                                       # те же части в том же порядке
    sheets = ei._sheet_paths(src)
    changed = {n for n in src if src[n] != dst[n]}
    assert changed <= {sheets[ei.EDIT_SHEET], sheets[ei.SETTINGS_SHEET], 'xl/drawings/vmlDrawing3.vml'} | \
        {n for n in src if n.startswith('xl/ctrlProps/')}
    assert dst['xl/vbaProject.bin'] == src['xl/vbaProject.bin']
    edit = dst[sheets[ei.EDIT_SHEET]].decode('utf-8')
    assert '<dimension ref="A1:AR4"/>' in edit
    assert re.search(r'<row r="1"[ >]', edit) and re.search(r'<row r="2"[ >]', edit)   # заголовки шаблона на месте
    r3, r4 = _cells(edit, 3), _cells(edit, 4)
    assert r3 == {'A': '000000346783', 'B': str((date(2026, 10, 14) - date(1899, 12, 30)).days), 'C': 'A008/6',
                  'D': '108', 'E': '10837', 'F': 'B008/10', 'G': '333DN33', 'J': '01'}
    assert r4['F'] == 'B001/1' and 'G' not in r4 and r4['AL'] == '0' and r4['AJ'] == '3' and 'AE' in r4
    assert r4['H'] == str((date(2026, 10, 16) - date(1899, 12, 30)).days) and 'H' not in r3
    assert 'AC' not in r3 and 'AQ' not in r3                            # адрес и ошибки импорта — пусто
    assert '<row r="5"' not in edit


def test_build_turns_recalculation_off():
    dst = _parts(ei.build_xlsm(TEMPLATE, [_row()]))
    settings = dst[ei._sheet_paths(dst)[ei.SETTINGS_SHEET]].decode('utf-8')
    for ref in ei.SETTINGS_OFF:
        assert re.search(rf'<c r="{ref}"[^>]*t="b"[^>]*><v>0</v>', settings), ref
    boxes = [v.decode() for n, v in dst.items() if n.startswith('xl/ctrlProps/') and b'CheckBox' in v]
    assert len(boxes) == 5 and not any('checked=' in b for b in boxes)
    assert '<x:Checked>' not in dst['xl/drawings/vmlDrawing3.vml'].decode('utf-8')


def test_openpyxl_reads_the_file():
    openpyxl = pytest.importorskip('openpyxl')
    comment = '1 հատ <ավել> & "կտաք"\x01\r\n2-րդ տող _x0041_'
    wb = openpyxl.load_workbook(io.BytesIO(ei.build_xlsm(TEMPLATE, [_row(comment=comment)])), keep_vba=True)
    ws = wb[ei.EDIT_SHEET]
    assert ws['A3'].value == '000000346783' and ws['B3'].value.date() == date(2026, 10, 14)
    # экранирование; управляющий символ убран; \r\n и «_x0041_» — ровно как в ERP: Excel раскодирует _xHHHH_ (openpyxl —
    # нет, раскодируем здесь, как Excel)
    excel = re.sub(r'_x([0-9A-F]{4})_', lambda m: chr(int(m.group(1), 16)), ws['I3'].value)
    assert excel == '1 հատ <ավել> & "կտաք"\r\n2-րդ տող _x0041_'
    assert [wb[ei.SETTINGS_SHEET][f'C{i}'].value for i in range(6, 10)] == [False] * 4


def test_broken_template_is_refused():
    parts = _parts(TEMPLATE)
    sheets = ei._sheet_paths(parts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for n, v in parts.items():
            z.writestr(n, v.replace(b'<c r="C7"', b'<c r="X7"') if n == sheets[ei.SETTINGS_SHEET] else v)
    with pytest.raises(ei.TemplateError):
        ei.build_xlsm(buf.getvalue(), [_row()])


def test_template_rewrite_mode_is_refused():
    """Таблица строк в режиме «Վերարտագրել տողերը» с пустыми строками стёрла бы строки заказов — файл не собирается."""
    parts = _parts(TEMPLATE)
    sheet = ei._sheet_paths(parts)[ei.SETTINGS_SHEET]
    xml = parts[sheet].decode('utf-8')
    rewrite = ei._shared(xml, 'A2')
    for ref in ei.GRID_MODES:
        bad = re.sub(rf'(<c r="{ref}"[^>]*t="s"[^>]*><v>)\d+(</v>)', rf'\g<1>{rewrite}\g<2>', xml)
        assert bad != xml
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for n, v in parts.items():
                z.writestr(n, bad.encode('utf-8') if n == sheet else v)
        with pytest.raises(ei.TemplateError):
            ei.build_xlsm(buf.getvalue(), [_row()])


def test_file_name_has_time():
    assert ei.file_name(date(2026, 10, 14), datetime(2026, 10, 13, 9, 5)) == 'ERP-araqich-2026-10-14-0905.xlsm'


def test_order_heads_sql_is_read_only():
    sql = erp.SQL_ORDER_HEADS.format(ph='?, ?')
    check_sql(sql)
    assert not _SQL_WRITE_RE.search(sql)
    assert sql.count('WITH (NOLOCK)') == 3
    assert 'fDELIVERYDATE' in sql and 'fDELIVERYADDRESSID' in sql


# ============================== API ==============================

@pytest.fixture
def day(client, monkeypatch):
    """План 01.10 (сегодня) на CAR1 и CAR2; ERP — подделка: заказы 1, 2, 3 и 70 у менеджера A001 без машины; роль — из
    g.user_role (как ставит app_v2), по умолчанию admin."""
    role = {'value': 'admin'}

    @client.application.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    for car, name in CAR_DRIVERS.items():
        state.store.save_truck_driver(car, DAY, name, 'qa')
    orders = {i: {'van': 1, 'car': '', 'children': False, 'address': 0} for i in (_isn(1), _isn(2), _isn(3), _isn(70))}
    reads = []

    def load(asked):
        reads.append(sorted(asked))
        states = {i: OrderState(i, 'N' + i[-1], CIDS[i], 2, o['van'], o['car'], o['children'], body(van=o['van']))
                  for i, o in orders.items() if i in asked}
        heads = {i: {'doc_num': 'N' + i[-1], 'day': date(2026, 10, 1), 'agent': 'A001', 'customer': str(CIDS[i]),
                     'sales_area': '108', 'comment': 'տուն 5', 'price_type': '01', 'tax_number': '',
                     'submit_date': None, 'cosign_date': None, 'assignor': '', 'account': '', 'doc_group': '',
                     'delay_days': 0, 'pay_type': '1', 'priority': 0, 'contact': '', 'create_method': '1',
                     'division': '', 'send_status': '2', 'delivery_date': None, 'address_id': orders[i]['address']}
                 for i in states}
        return states, AGENTS, API_CARS, heads
    state.erp_import_loader = load
    client.orders, client.reads, client.role = orders, reads, role
    return client


def _sent(client):
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _file_rows(resp):
    parts = _parts(resp.data)
    edit = parts[ei._sheet_paths(parts)[ei.EDIT_SHEET]].decode('utf-8')
    n = int(re.search(r'<dimension ref="A1:AR(\d+)"/>', edit).group(1))
    return [_cells(edit, r) for r in range(3, n + 1)]


def test_api_admin_only(day):
    _sent(day)
    for role in ('user', 'garage', 'warehouse', None):
        day.role['value'] = role
        assert day.get('/api/routes/dispatch/erp-import?date=' + DAY).status_code == 403
    assert day.reads == []


def test_api_needs_sent_plan_and_today_or_later(day):
    _build(day, ('CAR1', 'CAR2'))
    r = day.get('/api/routes/dispatch/erp-import?date=' + DAY)
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_SENT
    r = day.get('/api/routes/dispatch/erp-import?date=2026-09-30')
    assert r.status_code == 409 and r.get_json()['error'] == views.ERP_WRITE_PAST
    assert day.get('/api/routes/dispatch/erp-import?date=x').status_code == 400
    assert day.get(f'/api/routes/dispatch/erp-import?date={DAY}&isn=bad').status_code == 400


def test_api_file_has_courier_and_car_of_sent_plan(day):
    owner = _plan_stops(_sent(day))
    r = day.get('/api/routes/dispatch/erp-import?date=' + DAY)
    assert r.status_code == 200 and r.mimetype == views.XLSM_MIME
    assert re.search(rf'filename="ERP-araqich-{DAY}-\d{{4}}\.xlsm"', r.headers['Content-Disposition'])
    assert (r.headers['X-Erp-Rows'], r.headers['X-Erp-Skipped']) == ('3', '0')
    rows = {x['E']: x for x in _file_rows(r)}
    assert set(rows) == {'101', '102', '104'}                           # заказ 70 план не видел — не в файле
    for cid, x in rows.items():
        truck = owner[int(cid)]
        if truck == 'CAR1':
            assert (x['F'], x['G']) == ('B001/1', 'CAR1')
        else:                                                           # CAR2 закрыта в ERP — машина как была (пусто)
            assert x['F'] == 'B002/1' and 'G' not in x
        assert (x['C'], x['D'], x['I'], x['J'], x['AK'], x['AN'], x['AP']) == ('A001', '108', 'տուն 5', '01', '1', '1', '2')
    assert day.orders[_isn(1)]['van'] == 1                              # сайт ничего не пишет


def test_api_trial_one_order(day):
    _sent(day)
    r = day.get(f'/api/routes/dispatch/erp-import?date={DAY}&isn={_isn(2).lower()}')
    assert r.status_code == 200 and r.headers['X-Erp-Rows'] == '1'
    assert [x['A'] for x in _file_rows(r)] == ['N' + _isn(2)[-1]]


def test_api_order_with_delivery_address_is_skipped(day):
    """Макрос ArmSoft отправляет адрес всегда, а код адреса сайт не знает — заказ с адресом не в файле, он — пропуск."""
    _sent(day)
    day.orders[_isn(1)]['address'] = 7
    r = day.get('/api/routes/dispatch/erp-import?date=' + DAY)
    assert r.status_code == 200 and (r.headers['X-Erp-Rows'], r.headers['X-Erp-Skipped']) == ('2', '1')
    assert {x['A'] for x in _file_rows(r)} == {'N' + _isn(2)[-1], 'N' + _isn(3)[-1]}
    r = day.get(f'/api/routes/dispatch/erp-import?date={DAY}&isn={_isn(1)}')
    assert r.status_code == 409
    assert r.get_json()['error'] == f'{views.ERP_IMPORT_NOTHING} (1 պատվեր)․ {views.ERP_IMPORT_ADDRESS}՝ 1'


def test_api_nothing_to_change(day):
    owner = _plan_stops(_sent(day))
    for i, cid in ((_isn(1), 101), (_isn(2), 102), (_isn(3), 104)):
        day.orders[i].update(van=3199 if owner[cid] == 'CAR1' else 3200, car='CAR1' if owner[cid] == 'CAR1' else '')
    day.orders[_isn(3)]['children'] = True                              # накладная уже есть — пропуск
    r = day.get('/api/routes/dispatch/erp-import?date=' + DAY)
    assert r.status_code == 409                                         # причины с числами и подсказка о порядке (№100.1)
    assert r.get_json()['error'] == (f"{views.ERP_IMPORT_NOTHING} (3 պատվեր)․ {ew.REASON_HY['unchanged']}՝ 2; "
                                     f"{ew.REASON_HY['has_child_docs']}՝ 1։ {views.ERP_IMPORT_CHILDREN_HINT}")


def test_page_menu_item_and_versions():
    html = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'id="dpMenuErpXlsm" hidden' in html and 'Excel ERP-ի համար' in html
    assert "/api/routes/dispatch/erp-import?" in js and 'X-Erp-Rows' in js
