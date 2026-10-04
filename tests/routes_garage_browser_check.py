# -*- coding: utf-8 -*-
"""Проверка формы «Ավտոտնակ» в настоящем браузере: «Ինչ է արվել» — выбор из списка + «Այլ…» (ответ владельца №63).

Запуск из корня проекта:  python tests/routes_garage_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_dispatch_browser_check.py (настоящие шаблон, статика и blueprint, поддельная ERP, машины CAR1 и CAR2),
всё во временной папке. Порт 8768 на 127.0.0.1 (8766 и 8767 — у проверок «Развоза» и «Բեռնագիր»).

A списки: ремонт и ДТП — список владельца по порядку, первым «Ընտրեք…» (поле обязательное), последним «Այլ…»;
  страховка / техосмотр / налог — «Ապահովագրություն», «Տեխզննում», «Հարկ», «Այլ…»; у «только пробега» поля нет;
B смена вида: ремонт ↔ ДТП — выбор остаётся (список общий), ремонт → страховка — выбор сброшен;
C «Բաշխել»: «Շարժիչ» и «Փոխանցման տուփ» — сразу 24 ամիս и пояснение; ушли на другой пункт — срок снят; «Անվադողեր» —
  только подсказка про полный комплект; срок, выбранный руками (36 или «Ոչ»), выбор пункта не меняет;
D проверка до отправки: пункт не выбран — «Ընտրեք…», «Այլ…» без текста — «Գրեք կարճ…» и фокус в поле; запроса нет;
E сохранение: пункт списка — в базе ровно его текст; «Այլ…» — введённый текст; двигатель с 36 ամիս — срок 36, КПП с
  «Ոչ» руками — без срока;
F правка: запись с текстом пункта — выбран он; с другим текстом — «Այլ…» и текст в поле; двигатель с 36 — срок 36 не
  тронут (и при повторном выборе двигателя); КПП без срока — открытие правки срок не ставит; страховка — свой список;
G телефон 390×860: список высотой ≥ 44 px, горизонтальной прокрутки нет.
Ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов — шрифты, CDN).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8768
BASE = f'http://127.0.0.1:{PORT}'
REPAIR = ['Յուղ և ֆիլտրեր (ՏՍ)', 'Արգելակներ', 'Անվադողեր', 'Մարտկոց', 'Կախոց / ղեկ', 'Էլեկտրիկա',
          'Կցորդիչ (սցեպլենիե)', 'Շարժիչ', 'Փոխանցման տուփ', 'Թափք / ապակի']
FIXED = ['Ապահովագրություն', 'Տեխզննում', 'Հարկ']


def options(page):
    return page.eval_on_selector_all('#gjWhat option', 'els => els.map(o => [o.value, o.textContent])')


def ready(page):
    """Страница загрузила данные и собрала форму (машины и список «Ինչ է արվել» — после ответа сервера)."""
    page.wait_for_selector('#gjCar option[value="CAR1"]', state='attached')
    page.wait_for_selector('#gjWhat option[value="__other"]', state='attached')


def fill_entry(page, day, kind, km, amount=None, what=None, other=None):
    page.select_option('#gjCar', 'CAR1')
    page.fill('#gjDay', day)
    page.select_option('#gjKind', kind)
    if what is not None:
        page.select_option('#gjWhat', what)
    if other is not None:
        page.fill('#gjWhatOther', other)
    if amount is not None:
        page.fill('#gjAmount', str(amount))
    page.fill('#gjOdoKm', str(km))


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    tmp = tempfile.mkdtemp(prefix='garage-check-')
    app = base.build_app(tmp, base.FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
            page.on('console', lambda m: errors.append(f'console: {m.text}')
                    if m.type == 'error' and not base.is_ignorable(m) else None)
            page.on('request', lambda r: posts.append(r.post_data_json) if r.method == 'POST'
                    and r.url.endswith('/api/routes/garage/entries') else None)
            page.goto(f'{BASE}/routes/garage')
            ready(page)

            # A списки
            opts = options(page)
            check(opts == [['', 'Ընտրեք…']] + [[t, t] for t in REPAIR] + [['__other', 'Այլ…']],
                  'A ремонт: «Ընտրեք…», список владельца по порядку, «Այլ…»')
            check(page.eval_on_selector('#gjWhat', 'e => e.required') and page.is_hidden('#gjWhatOtherBox'),
                  'A поле обязательное, текста «Այլ…» не видно')
            page.select_option('#gjKind', 'accident')
            check(options(page) == opts, 'A ДТП — тот же список')
            page.select_option('#gjKind', 'fixed')
            check(options(page) == [['', 'Ընտրեք…']] + [[t, t] for t in FIXED] + [['__other', 'Այլ…']],
                  'A страховка / техосмотр / налог — свой список')
            page.select_option('#gjKind', 'odometer')
            check(page.is_hidden('#gjWhatBox'), 'A «только пробег» — поля «Ինչ է արվել» нет')

            # B смена вида
            page.select_option('#gjKind', 'repair')
            page.select_option('#gjWhat', 'Արգելակներ')
            page.select_option('#gjKind', 'accident')
            check(page.input_value('#gjWhat') == 'Արգելակներ', 'B ремонт → ДТП: выбор остался')
            page.select_option('#gjKind', 'fixed')
            check(page.input_value('#gjWhat') == '', 'B ДТП → страховка: выбор сброшен')

            # C «Բաշխել»
            page.select_option('#gjKind', 'repair')
            page.select_option('#gjWhat', 'Շարժիչ')
            check(page.input_value('#gjSpread') == '24' and page.is_visible('#gjSpreadWhy')
                  and 'Շարժիչը' in page.inner_text('#gjSpreadWhy'), 'C «Շարժիչ» — 24 ամիս и пояснение')
            page.select_option('#gjWhat', 'Արգելակներ')
            check(page.input_value('#gjSpread') == '' and page.is_hidden('#gjSpreadWhy'),
                  'C другой пункт — авто-срок снят')
            page.select_option('#gjWhat', 'Անվադողեր')
            check(page.input_value('#gjSpread') == '' and 'ամբողջ հավաքածու' in page.inner_text('#gjSpreadWhy'),
                  'C «Անվադողեր» — только подсказка, срок не выбран')
            page.select_option('#gjWhat', 'Փոխանցման տուփ')
            check(page.input_value('#gjSpread') == '24', 'C «Փոխանցման տուփ» — 24 ամիս')
            page.select_option('#gjSpread', '36')                    # руками
            page.select_option('#gjWhat', 'Շարժիչ')
            page.select_option('#gjWhat', 'Արգելակներ')
            check(page.input_value('#gjSpread') == '36', 'C срок 36 руками — выбор пункта его не меняет')
            page.select_option('#gjSpread', '')                      # руками «Ոչ»
            page.select_option('#gjWhat', 'Շարժիչ')
            check(page.input_value('#gjSpread') == '', 'C «Ոչ» руками — двигатель срок не ставит')

            # D проверка до отправки
            page.reload()
            ready(page)
            fill_entry(page, '2026-09-01', 'repair', 120_500, 85_000)
            page.click('#gjSave')
            check(page.inner_text('#gjWhatErr') == 'Ընտրեք, թե ինչ է արվել' and not posts,
                  'D пункт не выбран — ошибка, запроса нет')
            page.select_option('#gjWhat', '__other')
            check(page.is_visible('#gjWhatOther') and page.evaluate('document.activeElement.id') == 'gjWhatOther',
                  'D «Այլ…» — поле текста видно и в фокусе')
            page.click('#gjSave')
            check(page.inner_text('#gjWhatErr') == 'Գրեք կարճ, թե ինչ է արվել' and not posts
                  and page.evaluate('document.activeElement.id') == 'gjWhatOther',
                  'D «Այլ…» без текста — ошибка, фокус в поле')
            check(page.get_attribute('#gjWhatOther', 'maxlength') == '120', 'D текст «Այլ…» — до 120 знаков')

            # E сохранение
            page.select_option('#gjWhat', 'Արգելակներ')
            page.click('#gjSave')
            page.wait_for_function("document.getElementById('gjStatus').textContent.includes('պահպանվեց')")
            fill_entry(page, '2026-09-10', 'repair', 121_000, 40_000, '__other', '  Ռադիատոր  ')
            page.click('#gjSave')
            page.wait_for_function("document.querySelectorAll('#gjRows button[data-act=\"edit\"]').length >= 2")
            fill_entry(page, '2026-09-20', 'repair', 122_000, 2_400_000, 'Շարժիչ')
            page.select_option('#gjSpread', '36')
            page.click('#gjSave')
            page.wait_for_function("document.querySelectorAll('#gjRows button[data-act=\"edit\"]').length >= 3")
            fill_entry(page, '2026-09-22', 'repair', 122_200, 900_000, 'Փոխանցման տուփ')
            page.select_option('#gjSpread', '')                      # руками «Ոչ»
            page.click('#gjSave')
            page.wait_for_function("document.querySelectorAll('#gjRows button[data-act=\"edit\"]').length >= 4")
            fill_entry(page, '2026-09-25', 'fixed', 122_500, 120_000, 'Ապահովագրություն')
            page.click('#gjSave')
            page.wait_for_function("document.querySelectorAll('#gjRows button[data-act=\"edit\"]').length >= 5")
            saved = {e['day']: e for e in page.request.get(f'{BASE}/api/routes/garage/entries').json()['entries']}
            check([saved[d]['what'] for d in ('2026-09-01', '2026-09-10', '2026-09-20', '2026-09-22', '2026-09-25')]
                  == ['Արգելակներ', 'Ռադիատոր', 'Շարժիչ', 'Փոխանցման տուփ', 'Ապահովագրություն'],
                  'E в базе — текст пункта или введённый текст')
            check([saved[d]['spread_months'] for d in ('2026-09-01', '2026-09-20', '2026-09-22')] == [None, 36, None],
                  'E двигатель — срок 36 (руками), КПП — «Ոչ» (руками), тормоза — без срока')
            check(len(posts) == 5, f'E ровно 5 запросов записи ({len(posts)})')

            # F правка
            def edit(day):
                page.click(f'#gjRows button[data-act="edit"][data-id="{saved[day]["id"]}"]')
                page.wait_for_function("document.getElementById('gjCancel').hidden === false")

            edit('2026-09-10')
            check(page.input_value('#gjWhat') == '__other' and page.is_visible('#gjWhatOther')
                  and page.input_value('#gjWhatOther') == 'Ռադիատոր', 'F «Ռադիատոր» — «Այլ…» и текст в поле')
            edit('2026-09-01')
            check(page.input_value('#gjWhat') == 'Արգելակներ' and page.is_hidden('#gjWhatOtherBox')
                  and page.input_value('#gjWhatOther') == '', 'F «Արգելակներ» — выбран пункт, текста нет')
            edit('2026-09-20')
            check(page.input_value('#gjWhat') == 'Շարժիչ' and page.input_value('#gjSpread') == '36',
                  'F двигатель — срок 36 не тронут')
            page.select_option('#gjWhat', 'Արգելակներ')
            page.select_option('#gjWhat', 'Շարժիչ')
            check(page.input_value('#gjSpread') == '36', 'F двигатель снова выбран — заданный срок 36 не перебит на 24')
            edit('2026-09-22')
            check(page.input_value('#gjWhat') == 'Փոխանցման տուփ' and page.input_value('#gjSpread') == ''
                  and page.is_visible('#gjSpreadWhy'), 'F КПП без срока — правка срок не ставит, пояснение видно')
            edit('2026-09-25')
            check(page.input_value('#gjWhat') == 'Ապահովագրություն' and options(page)[1][1] == FIXED[0],
                  'F страховка — свой список, пункт выбран')
            page.click('#gjCancel')

            # G телефон
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(200)
            box = page.locator('#gjWhat').bounding_box()
            check(box and box['height'] >= 44, f'G список на телефоне высотой ≥ 44 px ({box and box["height"]})')
            check(not page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth'),
                  'G горизонтальной прокрутки нет')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'ошибок страницы и консоли нет' + (': ' + '; '.join(errors[:3]) if errors else ''))
    print(f'{sum(results)}/{len(results)} проверок')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
