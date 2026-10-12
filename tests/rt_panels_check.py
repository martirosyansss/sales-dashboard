# -*- coding: utf-8 -*-
"""Общая часть браузерных проверок сворачиваемых панелей поверх карты (static/js/rt_panels.js; «Առաքում» и «Մեքենաները
առցանց»). Имя без префикса test_: pytest его не собирает; вызывают routes_dispatch_layout_browser_check и
routes_live_browser_check.

panels — [(id, селектор панели, ожидаемый фрагмент подписи таблетки или None)]: каждая панель сворачивается «—» (фокус на
таблетку, aria-expanded=false, панели не видно, в таблетке название и счётчик) и разворачивается Enter на таблетке (фокус
на «—», aria-expanded=true); «Միայն քարտեզ» прячет все (aria-pressed=true), клавиша M возвращает как было; M в поле ввода
ничего не делает; свёрнутая панель остаётся свёрнутой после перезагрузки (reload — перезагрузить и дождаться данных)."""
from __future__ import annotations

VISIBLE = "(s) => { const e = document.querySelector(s); return !!e && !!e.offsetParent && e.getBoundingClientRect().height > 0; }"
ACTIVE = "() => { const a = document.activeElement; return a ? [a.className, a.dataset.panel || ''] : ['', '']; }"


def panels_checks(page, check, panels, tag, reload, field):
    vis = lambda s: page.evaluate(VISIBLE, s)  # noqa: E731
    for pid, sel, label in panels:
        mn = page.locator(f'.rt-pmin[data-panel="{pid}"]')
        pill = page.locator(f'.rt-ppill[data-panel="{pid}"]')
        check(vis(sel) and mn.get_attribute('aria-expanded') == 'true' and pill.is_hidden(),
              f'{tag} {pid}: panel open, «—» aria-expanded=true, no pill')
        mn.click()
        page.wait_for_timeout(250)
        act = page.evaluate(ACTIVE)
        text = pill.inner_text() if pill.is_visible() else ''
        check(not vis(sel) and pill.is_visible() and pill.get_attribute('aria-expanded') == 'false'
              and 'rt-ppill' in act[0] and act[1] == pid and (label is None or label in text),
              f'{tag} {pid}: collapsed → pill «{text}», focus on the pill, aria-expanded=false')
        page.keyboard.press('Enter')
        page.wait_for_timeout(250)
        act = page.evaluate(ACTIVE)
        check(vis(sel) and pill.is_hidden() and 'rt-pmin' in act[0] and act[1] == pid and mn.get_attribute('aria-expanded') == 'true',
              f'{tag} {pid}: Enter on the pill → panel back, focus on «—»')
    # «Միայն քարտեզ» и M
    btn = page.locator('.rt-mapbtn')
    btn.click()
    page.wait_for_timeout(300)
    hidden = [pid for pid, sel, _ in panels if not vis(sel)]
    pills = page.locator('.rt-ppill:not([hidden])').count()
    check(btn.get_attribute('aria-pressed') == 'true' and len(hidden) == len(panels) and pills == len(panels),
          f'{tag} «Միայն քարտեզ»: all panels → pills ({len(hidden)}/{len(panels)}, {pills} pills), aria-pressed=true')
    page.locator(field).focus()
    page.keyboard.press('m')
    page.wait_for_timeout(200)
    check(btn.get_attribute('aria-pressed') == 'true', f'{tag} M in an input field does nothing')
    page.evaluate('document.activeElement.blur()')
    page.keyboard.press('m')
    page.wait_for_timeout(300)
    check(btn.get_attribute('aria-pressed') == 'false' and all(vis(sel) for _, sel, _ in panels)
          and page.locator('.rt-ppill:not([hidden])').count() == 0, f'{tag} M → panels back as they were')
    # запоминается: одна свёрнута — после перезагрузки свёрнута; «Միայն քարտեզ» — тоже
    pid, sel, _ = panels[-1]
    page.locator(f'.rt-pmin[data-panel="{pid}"]').click()
    reload()
    check(not vis(sel) and page.locator(f'.rt-ppill[data-panel="{pid}"]').is_visible(), f'{tag} {pid} stays collapsed after reload')
    page.keyboard.press('m')
    page.wait_for_timeout(200)
    reload()
    check(page.locator('.rt-mapbtn').get_attribute('aria-pressed') == 'true' and not any(vis(s) for _, s, _ in panels),
          f'{tag} «Միայն քարտեզ» remembered after reload')
    page.keyboard.press('m')     # как было до «Միայն քարտեզ»: последняя свёрнута
    page.wait_for_timeout(300)
    check(not vis(sel) and all(vis(s) for p, s, _ in panels if p != pid), f'{tag} M after reload → the state before «Միայն քարտեզ»')
    page.locator(f'.rt-ppill[data-panel="{pid}"]').click()
    page.wait_for_timeout(250)
    check(vis(sel), f'{tag} pill click → {pid} open again')
