"""«Առաքման պայմաններ» прямо в «Развозе» (владелец 08.10: «изменение надо делать в модальном окне не выходя с этой
страницы»): диалог dpCondDlg вместо перехода в /routes/settings; сохраняет {customer_id, access, window, solo, center}
без "unload_min" — время у магазина (свой диалог «Ժամանակ խանութում») остаётся как есть."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_route_optimizer import _dispatch_setup, client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
ACCESS = {'mode': 'allow', 'trucks': ['CAR1']}
WINDOW = {'kind': 'between', 't1': 600, 't2': 720, 'tol': None}


def _js() -> str:
    return (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')


def _function(js: str, name: str) -> str:
    start = js.index(f'async function {name}(')
    return js[start:js.index('\n    }\n', start)]


def test_api_conditions_without_unload_keep_store_time(client):
    """Форма диалога — {customer_id, access, window, solo, center}: условия меняются, время у магазина — нет."""
    _dispatch_setup(client, [])
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'unload_min': 25}).status_code == 200
    r = client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': ACCESS, 'window': WINDOW,
                                                           'solo': True, 'center': True})
    assert r.status_code == 200, r.get_json()
    row = client.get('/api/routes/customer-vehicles?customer_id=103').get_json()['customers'][0]
    assert (row['vehicle_access'], row['window'], row['solo'], row['center'], row['unload_min']) == \
        (ACCESS, WINDOW, True, True, 25.0)
    # снять всё — время у магазина по-прежнему своё
    r = client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': None, 'window': None,
                                                           'solo': False, 'center': False})
    assert r.status_code == 200, r.get_json()
    row = client.get('/api/routes/customer-vehicles?customer_id=103').get_json()['customers'][0]
    assert (row['vehicle_access'], row['window'], row['solo'], row['center'], row['unload_min']) == \
        (None, None, False, False, 25.0)


def test_page_has_conditions_dialog():
    """Диалог рядом с «Ժամանակ խանութում» (в конце #rtDispatch), поля и тексты — как в «Условиях магазина»."""
    html = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    for piece in ('id="dpCondDlg"', 'id="dpCondTitle"', 'id="dpCondLead"', 'id="dpCondLoad"', 'id="dpCondKind"',
                  'id="dpCondFirst"', 'id="dpCondFirstLabel"', 'id="dpCondT1"', 'id="dpCondLast"', 'id="dpCondT2"',
                  'id="dpCondTolerance"', 'id="dpCondTol"', 'id="dpCondTimeHint"', 'id="dpCondMode"',
                  'id="dpCondChoicesTitle"', 'id="dpCondTrucks"', 'id="dpCondVehicleHint"', 'id="dpCondCenterRow"',
                  'id="dpCondCenter"', 'id="dpCondCenterHint"', 'id="dpCondSolo"', 'id="dpCondSoloHint"',
                  'id="dpCondErr"', 'id="dpCondSave"', 'id="dpCondCancel"',
                  'Խանութի առաքման պայմանները', 'Ընդունման ժամ', 'Որ մեքենաները կարող են սպասարկել',
                  'Կենտրոն՝ թույլատրված մեքենաներին', 'Առանձին երթ',
                  '<option value="between">Միջակայքում</option>', '<option value="deny">Բոլորը, բացի ընտրվածներից</option>'):
        assert piece in html, piece
    dlg = html[html.index('id="dpCondDlg"'):html.index('</dialog>', html.index('id="dpCondDlg"'))]
    assert 'unload' not in dlg.lower().replace('dp-unload-hint', '')          # времени у магазина в диалоге нет
    assert html.index('id="dpUnloadDlg"') < html.index('id="dpCondDlg"') < html.index('</div>\n{% endblock %}')
    css = (ROOT / 'static' / 'css' / 'routes_dispatch.css').read_text(encoding='utf-8')
    assert '.dp-cond-dlg' in css and '.dp-cond-truck' in css


def test_stop_button_opens_dialog_instead_of_settings_page():
    js = _js()
    assert "vb.href = '/routes/settings?customer=" not in js
    i = js.index("vb.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-vehiclebtn'")
    block = js[i - 200:i + 500]
    assert "document.createElement('button')" in block and "vb.type = 'button'" in block
    assert "vb.addEventListener('click', () => openCond(stop))" in block
    assert "$('dpCondDlg').open" in js[js.index('const interacting'):js.index('async function poll')]


def test_dialog_js_loads_fresh_and_posts_without_unload():
    js = _js()
    open_ = _function(js, 'openCond')
    assert "api('GET', '/api/routes/customer-vehicles?customer_id=' + encodeURIComponent(stop.customer_id))" in open_
    assert 'state.condSeq' in open_ and 'Բեռնում եմ խանութի տվյալները…' in open_
    assert 'Խանութը չի գտնվել — թարմացրեք էջը' in open_ and 'Այլևս ցուցակում չէ' in open_
    assert '.innerHTML' not in open_                                          # данные ERP — только textContent
    save = _function(js, 'saveCond')
    posts = re.findall(r"api\('POST', '/api/routes/customer-vehicles', (\{[^}]*\})\)", save)
    assert posts == ['{ customer_id: stop.customer_id, access, window: win, solo, center }']
    assert 'unload_min' not in save
    assert "center = mode === 'allow' && $('dpCondCenter').checked" in save
    assert 'առաքման պայմանները պահպանված են բոլոր օրերի համար։' in save and 'UNLOAD_REBUILD' in save and 'await reloadQuiet()' in save
    for msg in ('Նշեք ժամը։', 'Միջակայքի վերջը պետք է լինի սկզբից ուշ։', 'Թույլատրելի շեղումը՝ 0-ից մինչև 120 րոպե։'):
        assert msg in js, msg
    assert "$('dpCondDlg').addEventListener('cancel', (e) => { if (state.busy) e.preventDefault(); })" in js


def test_dialog_js_review_fixes():
    """Ревью: «без изменений» не путает коды машин с запятой; «Չեղարկել» заблокирована на время сохранения, а ошибка при
    закрытом диалоге не теряется; нечисло в допуске — ошибка, а не 0; Enter в полях сохраняет."""
    js = _js()
    assert "a.mode + ':' + JSON.stringify([...a.trucks].sort())" in js
    assert "'select, input, #dpCondSave' + (cancel ? ', #dpCondCancel' : '')" in js
    save = _function(js, 'saveCond')
    assert 'lockCond(true, true)' in save and 'lockCond(false, true)' in save
    assert "if ($('dpCondDlg').open) $('dpCondErr').textContent = e.message; else showActionError(e);" in save
    read = js[js.index('function readCondWindow('):js.index('const condWindowKey')]
    assert "$('dpCondTol').validity.badInput" in read and 'Թույլատրելի շեղումը՝ 0-ից մինչև 120 րոպե։' in read
    assert "e.key === 'Enter' && e.target instanceof HTMLInputElement" in js
    assert "[': не удалось сохранить водителя машины', '" in js
