/* Страница настроек производства: чек-гриды, оверрайд-паттерн, атомарное сохранение. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    let groups = [];     // [{code, name}]
    let storages = [];   // [{code, name, closed}]
    let settings = null;

    async function load() {
        try {
            const resp = await fetch('/api/production/settings');
            const data = await resp.json();
            groups = data.available_groups || [];
            storages = data.available_storages || [];
            settings = data.settings;
            if (data.error) {
                $('psError').querySelector('span').textContent = data.error;
                $('psError').classList.remove('d-none');
            }
            const lm = $('psLastModified');
            lm.textContent = data.last_modified ? 'ИЗМЕНЕНО ' + data.last_modified.replace('T', ' ') : 'ДЕФОЛТЫ';
            lm.classList.remove('d-none');
            render();
            $('psBody').classList.remove('d-none');
        } catch (e) {
            $('psError').querySelector('span').textContent = 'Не удалось загрузить настройки: ' + e.message;
            $('psError').classList.remove('d-none');
        } finally {
            $('psLoading').classList.add('d-none');
        }
    }

    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g,
            c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function render() {
        // Скаляры
        document.querySelectorAll('[data-field]').forEach(inp => {
            inp.value = settings[inp.dataset.field];
            inp.classList.remove('is-invalid');
        });
        $('psIncludeAgent').checked = !!settings.include_agent_stock;

        // Склады (исключённые = отмеченные)
        const stBox = $('psStorages');
        stBox.innerHTML = '';
        storages.forEach(s => {
            const id = 'st_' + s.code;
            const div = document.createElement('div');
            div.className = 'form-check';
            div.innerHTML = '<input class="form-check-input" type="checkbox" id="' + id + '" value="' + esc(s.code) + '"' +
                (settings.excluded_storages.includes(s.code) ? ' checked' : '') + '>' +
                '<label class="form-check-label" for="' + id + '">' + esc(s.code) + ' — ' + esc(s.name) +
                (s.closed ? ' <span class="prod-sub text-muted">(закрыт)</span>' : '') + '</label>';
            stBox.appendChild(div);
        });

        // Производственные группы
        renderGroups('');
        $('psGroupSearch').addEventListener('input', (e) => renderGroups(e.target.value));

        renderOverrides('psChFamilies', settings.changeover_hours_by_family, 'family');
        renderOverrides('psFamilyMap', settings.family_map, 'text');
        renderOverrides('psRatesGroup', settings.production_rate_by_group, 'group');
        renderOverrides('psRatesProduct', settings.production_rate_by_product, 'pid');

        renderLines();
    }

    // --- Редактор линий ---
    function renderLines() {
        const box = $('psLinesList');
        box.innerHTML = '';
        (settings.lines || []).forEach((L, i) => box.appendChild(lineCard(L, i)));
        if (!(settings.lines || []).length) {
            const hint = document.createElement('div');
            hint.className = 'pc-tile-sub';
            hint.style.padding = '8px 0';
            hint.textContent = 'Линий пока нет — работает одна общая линия. Добавьте свои линии кнопкой ниже.';
            box.appendChild(hint);
        }
    }

    function lineCard(L, idx) {
        L = L || {};
        const card = document.createElement('div');
        card.className = 'pc-line-card';
        const gsel = new Set((L.groups || []).map(String));
        const noGroupChk = '<label class="pc-line-grp"><input type="checkbox" class="lgroup" value=""' +
            (gsel.has('') ? ' checked' : '') + '> <b>Без группы</b> (напр. стаканы)</label>';
        const groupChecks = noGroupChk + groups.map(g =>
            '<label class="pc-line-grp"><input type="checkbox" class="lgroup" value="' + esc(g.code) + '"' +
            (gsel.has(g.code) ? ' checked' : '') + '> ' + esc(g.name || g.code) + '</label>').join('');
        card.innerHTML =
            '<div class="pc-line-head">' +
            '<input class="lname" placeholder="Название линии" value="' + esc(L.name || ('Линия ' + (idx + 1))) + '">' +
            '<button class="del ldel" title="Удалить линию"><i class="fas fa-trash"></i></button>' +
            '</div>' +
            '<div class="pc-line-nums">' +
            '<label>Скорость, шт/час<input class="lrate" type="number" min="0" step="10" value="' + esc(L.rate ?? '') + '"></label>' +
            '<label>Дней/мес<input class="ldays" type="number" min="1" max="31" value="' + esc(L.work_days ?? 22) + '"></label>' +
            '<label>Часов/смену<input class="lshift" type="number" min="1" max="24" step="0.5" value="' + esc(L.shift_hours ?? 8) + '"></label>' +
            '<label>Переналадка, ч<input class="lch" type="number" min="0" max="24" step="0.25" value="' + esc(L.changeover_hours ?? 2) + '"></label>' +
            '</div>' +
            '<div class="pc-tile-sub" style="margin:6px 0 4px">Что делает эта линия (товарные группы; ничего не отмечено = все):</div>' +
            '<div class="pc-line-groups">' + groupChecks + '</div>';
        card.querySelector('.ldel').addEventListener('click', () => card.remove());
        return card;
    }

    function collectLines() {
        const out = [];
        $('psLinesList').querySelectorAll('.pc-line-card').forEach((card, i) => {
            const name = (card.querySelector('.lname').value || '').trim();
            if (!name) return;
            out.push({
                id: 'L' + (i + 1), name: name,
                groups: [...card.querySelectorAll('.lgroup:checked')].map(c => c.value),
                rate: parseFloat(card.querySelector('.lrate').value) || 0,
                work_days: parseInt(card.querySelector('.ldays').value, 10) || 22,
                shift_hours: parseFloat(card.querySelector('.lshift').value) || 8,
                changeover_hours: parseFloat(card.querySelector('.lch').value) || 0,
            });
        });
        return out;
    }

    function renderGroups(q) {
        const box = $('psGroups');
        const checked = new Set(
            [...box.querySelectorAll('input:checked')].map(i => i.value));
        // при первом рендере — из settings
        const selected = checked.size ? checked : new Set(settings.production_groups);
        box.innerHTML = '';
        const needle = (q || '').trim().toLowerCase();
        groups.filter(g => !needle || (g.name || '').toLowerCase().includes(needle) ||
                           g.code.toLowerCase().includes(needle))
            .forEach(g => {
                const id = 'pg_' + g.code;
                const div = document.createElement('div');
                div.className = 'form-check';
                div.innerHTML = '<input class="form-check-input" type="checkbox" id="' + id + '" value="' + esc(g.code) + '"' +
                    (selected.has(g.code) ? ' checked' : '') + '>' +
                    '<label class="form-check-label" for="' + id + '">' + esc(g.name || g.code) + '</label>';
                box.appendChild(div);
            });
        // невидимые (отфильтрованные) отмеченные не должны теряться:
        selected.forEach(code => {
            if (!box.querySelector('input[value="' + CSS.escape(code) + '"]')) {
                const hidden = document.createElement('input');
                hidden.type = 'checkbox'; hidden.checked = true; hidden.value = code;
                hidden.className = 'd-none'; hidden.dataset.hiddenChecked = '1';
                box.appendChild(hidden);
            }
        });
    }

    // Оверрайды: строка = [ключ][значение][удалить]. kind: family|text|group|pid
    function renderOverrides(boxId, obj, kind) {
        const box = $(boxId);
        box.innerHTML = '';
        Object.entries(obj || {}).forEach(([k, v]) => box.appendChild(overrideRow(boxId, kind, k, v)));
    }

    function overrideRow(boxId, kind, key, val) {
        const row = document.createElement('div');
        row.className = 'pc-ovrow';
        const groupSelect = () => '<select class="ov-key" style="flex:1">' +
            '<option value="">— группа —</option>' +
            groups.map(g => '<option value="' + esc(g.code) + '"' + (g.code === key ? ' selected' : '') + '>' +
                esc(g.name || g.code) + '</option>').join('') + '</select>';
        let keyHtml;
        if (kind === 'group' || (kind === 'text' && boxId === 'psFamilyMap')) {
            keyHtml = groupSelect();
        } else if (kind === 'family') {
            keyHtml = '<input class="ov-key" style="flex:1" placeholder="семейство" value="' + esc(key) + '">';
        } else { // pid
            keyHtml = '<input class="ov-key" style="flex:1" placeholder="ID товара" value="' + esc(key) + '">';
        }
        const valAttrs = (boxId === 'psFamilyMap')
            ? 'type="text" placeholder="семейство"'
            : 'type="number" min="0" step="0.1" placeholder="' + (boxId.startsWith('psRates') ? 'шт/час' : 'часы') + '"';
        row.innerHTML = keyHtml +
            '<input class="ov-val" style="width:110px" ' + valAttrs + ' value="' + esc(val) + '">' +
            '<button class="del" title="Удалить"><i class="fas fa-trash"></i></button>';
        row.querySelector('.del').addEventListener('click', () => row.remove());
        return row;
    }

    function collectOverrides(boxId, numeric) {
        const out = {};
        $(boxId).querySelectorAll('.pc-ovrow').forEach(row => {
            const k = (row.querySelector('.ov-key').value || '').trim();
            const vRaw = (row.querySelector('.ov-val').value || '').trim();
            if (!k || !vRaw) return;
            out[k] = numeric ? parseFloat(vRaw) : vRaw;
        });
        return out;
    }

    async function save() {
        document.querySelectorAll('[data-field]').forEach(i => i.classList.remove('is-invalid'));
        $('psError').classList.add('d-none');
        const payload = {
            include_agent_stock: $('psIncludeAgent').checked,
            excluded_storages: [...$('psStorages').querySelectorAll('input:checked')].map(i => i.value),
            production_groups: [...$('psGroups').querySelectorAll('input:checked')].map(i => i.value),
            changeover_hours_by_family: collectOverrides('psChFamilies', true),
            family_map: collectOverrides('psFamilyMap', false),
            production_rate_by_group: collectOverrides('psRatesGroup', true),
            production_rate_by_product: collectOverrides('psRatesProduct', true),
            lines: collectLines(),
        };
        document.querySelectorAll('[data-field]').forEach(inp => {
            payload[inp.dataset.field] = inp.value === '' ? null : Number(inp.value);
        });
        const btn = $('psSaveBtn');
        btn.disabled = true;
        try {
            const resp = await fetch('/api/production/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            if (!data.success) {
                const errors = data.errors || { _: data.error || 'ошибка' };
                const msgs = [];
                Object.entries(errors).forEach(([field, msg]) => {
                    msgs.push((field === '_' ? '' : field + ': ') + msg);
                    const inp = document.querySelector('[data-field="' + field + '"]');
                    if (inp) inp.classList.add('is-invalid');
                });
                $('psError').querySelector('span').textContent = 'Не сохранено: ' + msgs.join('; ');
                $('psError').classList.remove('d-none');
                return;
            }
            $('psError').classList.add('d-none');
            bootstrap.Toast.getOrCreateInstance($('psToast')).show();
            load();
        } catch (e) {
            $('psError').querySelector('span').textContent = 'Ошибка сохранения: ' + e.message;
            $('psError').classList.remove('d-none');
        } finally {
            btn.disabled = false;
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        load();
        $('psSaveBtn').addEventListener('click', save);
        $('psChAddBtn').addEventListener('click', () => $('psChFamilies').appendChild(overrideRow('psChFamilies', 'family', '', '')));
        $('psFamAddBtn').addEventListener('click', () => $('psFamilyMap').appendChild(overrideRow('psFamilyMap', 'text', '', '')));
        $('psRateGroupAddBtn').addEventListener('click', () => $('psRatesGroup').appendChild(overrideRow('psRatesGroup', 'group', '', '')));
        $('psRateProductAddBtn').addEventListener('click', () => $('psRatesProduct').appendChild(overrideRow('psRatesProduct', 'pid', '', '')));
        $('psLineAddBtn').addEventListener('click', () => {
            const n = $('psLinesList').querySelectorAll('.pc-line-card').length;
            // убрать hint, если он есть
            const hint = $('psLinesList').querySelector('.pc-tile-sub');
            if (hint && !$('psLinesList').querySelector('.pc-line-card')) hint.remove();
            $('psLinesList').appendChild(lineCard({ name: 'Линия ' + (n + 1) }, n));
        });
    });
})();
