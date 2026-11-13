/* Разовое подтверждение изменения правил машин; секрет не сохраняется в браузере. */
(function () {
    'use strict';
    let dialog = null, pending = false;
    function promptPassword(message, context) {
        if (pending) return Promise.reject(new Error('Հաստատման պատուհանն արդեն բաց է։'));
        pending = true;
        if (!dialog) {
            dialog = document.createElement('dialog');
            dialog.id = 'rtSuperadminDialog'; dialog.className = 'rt-superadmin'; dialog.lang = 'hy';
            dialog.setAttribute('aria-labelledby', 'rtSuperadminTitle');
            dialog.setAttribute('aria-describedby', 'rtSuperadminHelp rtSuperadminError');
            // Постоянная разметка; сообщения сервера вставляются только через textContent.
            dialog.innerHTML = '<form method="dialog" autocomplete="off">'
                + '<span class="rt-superadmin-eyebrow">Superadmin</span><h2 id="rtSuperadminTitle">Հաստատել փոփոխությունը</h2>'
                + '<p id="rtSuperadminHelp">Խանութի մեքենայի ամրացումը կամ արգելքը փոխելու համար մուտքագրեք Superadmin-ի գաղտնաբառը։</p>'
                + '<label for="rtSuperadminPassword">Գաղտնաբառ</label><input id="rtSuperadminPassword" type="password" maxlength="128" autocomplete="off" required>'
                + '<p id="rtSuperadminError" role="alert"></p><div class="rt-superadmin-actions">'
                + '<button type="button" id="rtSuperadminCancel" class="rt-btn rt-btn-ghost">Չեղարկել</button>'
                + '<button type="submit" class="rt-btn rt-btn-primary">Հաստատել</button></div></form>';
            (document.querySelector('#rtDispatch, #rtSettings') || document.body).append(dialog);
        }
        const input = dialog.querySelector('input'), form = dialog.querySelector('form');
        const origin = document.activeElement;
        dialog.querySelector('#rtSuperadminTitle').textContent = context && context.title || 'Հաստատել փոփոխությունը';
        dialog.querySelector('#rtSuperadminHelp').textContent = context && context.help || 'Խանութի մեքենայի ամրացումը կամ արգելքը փոխելու համար մուտքագրեք Superadmin-ի գաղտնաբառը։';
        input.value = ''; input.setAttribute('aria-invalid', message ? 'true' : 'false');
        dialog.querySelector('#rtSuperadminError').textContent = message || '';
        return new Promise(resolve => {
            let result = null;
            const finish = () => {
                input.value = ''; form.removeEventListener('submit', submit);
                dialog.removeEventListener('close', finish); dialog.querySelector('#rtSuperadminCancel').onclick = null;
                pending = false; resolve(result);
                requestAnimationFrame(() => { if (origin && origin.isConnected && !origin.disabled) origin.focus(); });
            };
            const submit = event => { event.preventDefault(); if (form.reportValidity()) { result = input.value; input.value = ''; dialog.close(); } };
            form.addEventListener('submit', submit); dialog.addEventListener('close', finish);
            dialog.querySelector('#rtSuperadminCancel').onclick = () => dialog.close();
            dialog.showModal(); input.focus();
        });
    }
    async function run(send, context) {
        let password, error = '';
        try {
            try { return await send(); } catch (e) {
                if (!e.data || e.data.superadmin_required !== true) throw e;
                error = e.data.superadmin_invalid ? e.message : '';
            }
            for (;;) {
                password = await promptPassword(error, context);
                if (password === null) throw Object.assign(new Error('Փոփոխությունը չպահպանվեց։'), { cancelled: true });
                try { return await send(password); } catch (e) {
                    if (!e.data || e.data.superadmin_required !== true) throw e;
                    error = e.message;
                } finally { password = undefined; }
            }
        } finally { password = undefined; }
    }
    window.RouteSuperadmin = Object.freeze({ run });
})();
