"""Журнал гаража: «Ինչ է արվել» — выбор из списка + «Այլ…» (ответ владельца №63). Строки страницы
(routes_garage.*) и то, что сервер принимает их как есть; поведение в браузере —
tests/routes_garage_browser_check.py."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from route_optimizer import store as st  # noqa: E402
from test_route_garage import CARS, TODAY  # noqa: E402

JS = (ROOT / 'static' / 'js' / 'routes_garage.js').read_text(encoding='utf-8')
HTML = (ROOT / 'templates' / 'routes_garage.html').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'css' / 'routes_garage.css').read_text(encoding='utf-8')
# №63: список владельца по порядку («Այլ…» — последним, его добавляет страница)
OWNER_REPAIR = ['Յուղ և ֆիլտրեր (ՏՍ)', 'Արգելակներ', 'Անվադողեր', 'Մարտկոց', 'Կախոց / ղեկ', 'Էլեկտրիկա',
                'Կցորդիչ (սցեպլենիե)', 'Շարժիչ', 'Փոխանցման տուփ', 'Թափք / ապակի']
FIXED = ['Ապահովագրություն', 'Տեխզննում', 'Հարկ']


def _array(name):
    m = re.search(rf"const {name} = \[(.*?)\];", JS, re.S)
    return re.findall(r"'([^']*)'", m.group(1))


def _part(start, end):
    return JS[JS.index(start):JS.index(end)]


def test_lists_are_the_owner_lists():
    """Ремонт и ДТП — список владельца по порядку; страховка / техосмотр / налог — свой; первым «Ընտրեք…», последним
    «Այլ…»; у «только пробега» поля нет."""
    assert _array('REPAIR_WHAT') == OWNER_REPAIR
    assert ("const WHAT_LISTS = { repair: REPAIR_WHAT, accident: REPAIR_WHAT, "
            "fixed: ['Ապահովագրություն', 'Տեխզննում', 'Հարկ'] };") in JS
    fill = _part('function fillWhat', 'function setWhat').replace('\r\n', '\n')
    assert ("$('gjWhat').replaceChildren(h('option', { value: '', text: 'Ընտրեք…' }),\n"
            "            ...list.map(text => h('option', { value: text, text })), "
            "h('option', { value: OTHER, text: 'Այլ…' }));") in fill
    kind = _part('function syncKind', 'function syncOdoHint')
    assert "$('gjWhatBox').hidden = kind === 'odometer';" in kind


def test_edit_maps_list_label_or_other_text():
    """Правка: текст записи — пункт списка её вида → выбран он; иной текст → «Այլ…» и он в поле; в базу — ровно текст
    пункта или введённый текст (без пробелов по краям)."""
    set_what = _part('function setWhat', 'function whatValue')
    assert "const inList = (WHAT_LISTS[kind] || []).includes(text);" in set_what
    assert "$('gjWhat').value = inList ? text : text ? OTHER : '';" in set_what
    assert "$('gjWhatOther').value = inList ? '' : text;" in set_what
    assert "$('gjWhatOther').maxLength = Math.max(120, inList ? 0 : text.length);" in set_what   # старый длинный текст
    assert "return $('gjWhat').value === OTHER ? $('gjWhatOther').value.trim() : $('gjWhat').value;" in JS
    edit = _part('function editEntry', 'function intOrRaw')
    assert "setWhat(e.kind, e.kind === 'odometer' ? '' : e.what);" in edit
    assert "syncKind(false);" in edit and "state.spreadTouched = state.spreadAuto = false;" in edit


def test_kind_switch_resets_other_list_and_save_validates():
    kind = _part('function syncKind', 'function syncOdoHint').replace('\r\n', '\n')
    assert ("        if (state.whatList !== (WHAT_LISTS[kind] || null)) {\n"
            "            const other = $('gjWhat').value === OTHER;\n"
            "            fillWhat(kind);\n"
            "            $('gjWhat').value = other ? OTHER : '';\n"
            "        }") in kind                                 # пункт сброшен, «Այլ…» и его текст остались
    sync = _part('function syncWhat', 'function fieldErrors')
    assert "$('gjWhatOther').required = what === OTHER;" in sync
    assert "$('gjWhatOther').setAttribute('aria-required', String(what === OTHER));" in sync
    save = _part('async function saveEntry', 'async function deleteEntry')
    assert "what: kind === 'odometer' ? state.odoWhat : whatValue() || null," in save
    assert "if (kind !== 'odometer' && !$('gjWhat').value) badNum.what = 'Ընտրեք, թե ինչ է արվել';" in save
    assert "else if (kind !== 'odometer' && !body.what) badNum.what = 'Գրեք կարճ, թե ինչ է արվել';" in save
    assert save.index('badNum.what') < save.index("await api('/api/routes/garage/entries', body)")
    errors = _part('function fieldErrors', 'function syncSpread')
    assert "const whatId = $('gjWhat').value === OTHER ? 'gjWhatOther' : 'gjWhat';" in errors


def test_spread_preselect_only_engine_and_gearbox_never_over_hand_value():
    """Двигатель и КПП — сразу 24 ամիս (если срок не меняли руками в этой форме и он не задан), ушли с них — авто-срок
    снят; шины — только подсказка; подсказка от 300 000 ֏ осталась."""
    assert _array('SPREAD_WHAT') == ['Շարժիչ', 'Փոխանցման տուփ']
    assert "const TIRES_WHAT = 'Անվադողեր';" in JS
    sync = _part('function syncWhat', 'function fieldErrors')
    assert "const big = repair && SPREAD_WHAT.includes(what);" in sync
    assert "if (auto && !state.spreadTouched) {" in sync
    assert "if (big && !$('gjSpread').value) { $('gjSpread').value = '24'; state.spreadAuto = true; }" in sync
    assert "else if (!big && state.spreadAuto) { $('gjSpread').value = ''; state.spreadAuto = false; }" in sync
    hint = _part('function syncSpread', 'function syncKind')      # пояснение — по ТЕКУЩЕМУ сроку
    assert "months = $('gjSpread').value;" in hint and "const unit = repair && SPREAD_WHAT.includes(what);" in hint
    assert ("const why = unit && months ? 'Շարժիչը և փոխանցման տուփը ծառայում են տարիներ՝ ծախսը բաշխվում է ' + months "
            "+ ' ամսվա վրա։'") in hint
    assert ": unit ? 'Շարժիչը և փոխանցման տուփը ծառայում են տարիներ — խորհուրդ է տրվում բաշխել 24 ամսվա վրա։'" in hint
    assert "repair && what === TIRES_WHAT ? 'Եթե ամբողջ հավաքածու է — ընտրեք 24 ամիս։'" in hint
    assert 'Կարող եք փոխել' not in JS and "ծախսը բաշխվում է 24 ամսվա վրա" not in JS   # не «24» при любом сроке
    assert 'syncSpread();' in sync          # выбор пункта и смена срока руками — оба через syncSpread (пояснение)
    assert ("$('gjSpread').addEventListener('change', () => { state.spreadTouched = true; state.spreadAuto = false; "
            "syncSpread(); });") in JS
    assert "$('gjWhat').addEventListener('change', () => {" in JS and "syncWhat(true);" in JS
    assert "$('gjSpreadSuggest').hidden = !(repair && big && !$('gjSpread').value);" in JS


def test_template_select_other_note_versions_and_no_new_static():
    assert '<select id="gjWhat" class="rt-select" required aria-describedby="gjWhatErr"></select>' in HTML
    assert '<div id="gjWhatOtherBox" class="gj-what-other" hidden>' in HTML
    assert 'id="gjWhatOther" class="rt-input" type="text" maxlength="120"' in HTML
    assert '<span id="gjSpreadWhy" class="gj-suggest" hidden></span>' in HTML
    assert 'placeholder="Մանրամասներ՝ օրինակ՝ առջևի, ձախ, որտեղ են վերանորոգել, կտրոնի համար"' in HTML
    assert "routes_garage.css') }}?v=8" in HTML and "routes_garage.js') }}?v=9" in HTML
    assert '.gj-what-other { margin-top: 8px; }' in CSS
    # вход из интернета пропускает ровно эту статику (app_v2._PUBLIC_STATIC); routes_basemap.js — карта дня «Նորմ և
    # փաստ» (04.10), в списке открытых снаружи вместе с ним
    assert re.findall(r"filename='((?:css|js)/[^']+)'", HTML) == ['css/routes.css', 'css/routes_garage.css',
                                                                   'js/routes_basemap.js', 'js/routes_garage.js']
    text = [ln for ln in JS[JS.index('const OTHER'):JS.index('function syncOdoHint')].splitlines()
            if not ln.strip().startswith('//')]
    assert not [ln for ln in text if any('Ѐ' <= ch <= 'ӿ' for ch in ln.split('//')[0])], 'тексты — по-армянски (№58)'


@pytest.mark.parametrize('kind,what', [('repair', w) for w in OWNER_REPAIR] + [('accident', 'Արգելակներ')]
                         + [('fixed', w) for w in FIXED] + [('repair', 'Ռ' * 120)])
def test_server_stores_list_label_or_other_text_as_is(kind, what):
    """Схема и проверка сервера прежние: пункт списка и текст «Այլ…» (до 120 знаков) принимаются как есть."""
    item, errors = st.check_garage_entry({'car_code': 'CAR1', 'day': '2026-09-01', 'kind': kind, 'what': what,
                                          'amount_amd': 50_000, 'odometer_km': 100_000}, CARS, TODAY)
    assert not errors and item.what == what
