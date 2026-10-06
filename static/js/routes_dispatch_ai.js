/* «Հարցրու AI-ին» на странице «Развоз» (ответ владельца №52): панель чата — вопросы логиста по плану дня,
   POST /api/routes/dispatch/ask (вместе с планом на экране rev / seen: устарел — 409, «Թարմացնել»). История
   разговора — здесь, по дням (state.ai.chats); ответ AI выводится только через textContent (абзацы и строки
   «• » — без разметки); ожидание ответа — не дольше 150 с.
   Вынесено из routes_dispatch.js: страница подключает панель RoutesDispatchAI.attach(...) и передаёт общее —
   $, state, api (ошибки сервера переводит её словарь SERVER_HY), announce, dayHuman, truckLabel, load. */
(function () {
    'use strict';

    window.RoutesDispatchAI = {
        // панели нет на странице (AI не включён) — null
        attach({ $, state, api, announce, dayHuman, truckLabel, load }) {
            if (!$('dpAi')) return null;

            const AI_SUGGEST = [
                'Ամփոփիր օրը երեք նախադասությամբ',
                'Ո՞ր մեքենան ունի ամենաշատ ազատ տեղ',
                'Ո՞ր երթն է ամենաուշը վերադառնում, և ինչու',
                'Ինչու՞ են որոշ խանութներ դուրս մնացել երթերից',
                'Ի՞նչ կլինի, եթե այսօր մեկ մեքենա չաշխատի',
            ];
            const AI_HISTORY = 12;          // реплик истории в запросе — как ai_chat.MAX_HISTORY
            const aiChat = () => {
                if (!state.ai.chats.has(state.day)) state.ai.chats.set(state.day, []);
                return state.ai.chats.get(state.day);
            };
            // Что выбрано на карте — подсказка модели к вопросу («почему так?» о выбранном рейсе)
            function aiFocus() {
                const f = state.mapFocus, plan = state.data && state.data.plan;
                const t = f && plan && plan.trucks.find(x => x.car_code === f.truck);
                if (!t) return null;
                const i = f.trip == null ? -1 : t.trips.findIndex(tr => tr.id === f.trip);
                return truckLabel(t) + (i >= 0 ? ', երթ ' + (i + 1) : t.trips.length > 1 ? ', բոլոր երթերը' : ', երթ 1');
            }
            function aiOpen(open) {
                $('dpAi').hidden = !open;
                $('dpAiOpen').hidden = open;
                $('dpAiOpen').setAttribute('aria-expanded', String(open));
                if (open) { aiRender(); $('dpAiInput').focus(); return; }
                // на телефоне (№81) кнопки нет — AI во вкладках внизу: фокус возвращается на вкладку
                const tab = document.getElementById('dpTabAi');
                if ($('dpAiOpen').offsetParent === null && tab && !tab.hidden) tab.focus(); else $('dpAiOpen').focus();
            }
            // Ответ AI — абзацы и строки-пункты «• …» (без разметки: всё через textContent)
            function aiText(box, text) {
                let ul = null;
                String(text).replace(/\*\*(.+?)\*\*/g, '$1').split('\n').forEach(raw => {
                    const line = raw.trim();
                    if (!line) { ul = null; return; }
                    const m = /^(?:[•\-*]|\d+[.)])\s+(.+)$/.exec(line);
                    if (m) {
                        if (!ul) { ul = document.createElement('ul'); box.appendChild(ul); }
                        const li = document.createElement('li');
                        li.textContent = m[1];
                        ul.appendChild(li);
                    } else {
                        ul = null;
                        const p = document.createElement('p');
                        p.textContent = line;
                        box.appendChild(p);
                    }
                });
            }
            function aiMsg(role, text, note) {
                const div = document.createElement('div');
                div.className = 'dp-ai-msg ' + (role === 'user' ? 'is-user' : 'is-bot');
                if (role === 'user') div.textContent = text; else aiText(div, text);
                if (note) { const n = document.createElement('span'); n.className = 'dp-ai-note'; n.textContent = note; div.appendChild(n); }
                return div;
            }
            function aiRender() {
                const log = $('dpAiLog'), chat = aiChat();
                log.textContent = '';
                state.ai.shownDay = state.day;
                $('dpAiSub').textContent = 'Պատասխանում է ' + dayHuman(state.day) + ' թվերով · ոչինչ չի փոխում';
                if (!chat.length) {
                    const hello = document.createElement('p');
                    hello.className = 'dp-ai-hello';
                    hello.textContent = 'Հարցրեք այս օրվա երթերի, մեքենաների, խանութների կամ ժամերի մասին։ '
                        + 'AI-ն տեսնում է նույն թվերը, ինչ էջը, և ոչինչ չի փոխում։ Օրինակ՝';
                    const sugs = document.createElement('div');
                    sugs.className = 'dp-ai-sugs';
                    AI_SUGGEST.forEach(q => {
                        const b = document.createElement('button');
                        b.type = 'button';
                        b.className = 'dp-ai-sug';
                        b.innerHTML = '<i class="fas fa-arrow-right" aria-hidden="true"></i><span></span>';
                        b.lastChild.textContent = q;
                        b.addEventListener('click', () => aiAsk(q));
                        sugs.appendChild(b);
                    });
                    log.append(hello, sugs);
                }
                chat.forEach(m => log.appendChild(aiMsg(m.role, m.text, m.note)));
                log.scrollTop = log.scrollHeight;
            }
            // fromInput — вопрос из поля ввода (его очистить); подсказка — набранный текст не трогать
            async function aiAsk(question, fromInput) {
                const q = String(question || '').trim();
                if (!q || state.ai.busy || !state.day) return;
                const day = state.day, chat = aiChat(), log = $('dpAiLog'), focus = aiFocus();
                // история — только удачные пары вопрос/ответ (чередование user/assistant для API)
                const history = chat.slice(-AI_HISTORY).map(m => ({ role: m.role, text: m.text }));
                if (!chat.length) log.textContent = '';
                const asked = [aiMsg('user', q)];
                if (focus) { const f = document.createElement('span'); f.className = 'dp-ai-focus'; f.textContent = 'քարտեզում՝ ' + focus; asked.push(f); }
                log.append(...asked);
                const wait = document.createElement('div');
                wait.className = 'dp-ai-msg is-bot';
                wait.innerHTML = '<span class="dp-ai-typing"><span class="dp-ai-dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Նայում եմ օրվա թվերին…</span></span>';
                log.appendChild(wait);
                log.scrollTop = log.scrollHeight;
                state.ai.busy = true;
                $('dpAiSend').disabled = true;
                if (fromInput) { $('dpAiInput').value = ''; aiResize(); }
                $('dpAiInput').focus();         // нажатая подсказка или «Կրկնել» исчезли — фокус в поле ввода
                // план на экране: сервер сверит его с текущим и не ответит о другом плане (409 stale)
                const plan = state.data.plan;
                const seen = plan ? plan.trucks.flatMap(t => t.trips.map(tr => [tr.id, tr.return, tr.over_time])) : null;
                try {
                    const r = await api('POST', '/api/routes/dispatch/ask', { date: day, question: q, history, focus, rev: state.data.rev, seen }, 150000);
                    const note = r.truncated ? 'Պատասխանը կտրվել է՝ շատ երկար էր։ Հարցրեք ավելի նեղ։' : '';
                    chat.push({ role: 'user', text: q }, { role: 'assistant', text: r.answer, note });
                    if (wait.isConnected) wait.replaceWith(aiMsg('assistant', r.answer, note));
                    else if (state.day === day && !$('dpAi').hidden) aiRender();     // панель перерисовали, пока ждали
                    announce('AI-ն պատասխանեց');
                } catch (e) {
                    if (state.day !== day) return;
                    const err = document.createElement('div');
                    err.className = 'dp-ai-msg is-err';
                    err.setAttribute('role', 'alert');
                    err.textContent = e.message || String(e);
                    const again = document.createElement('button');
                    again.type = 'button';
                    again.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                    if (e.status === 409 && e.data && e.data.stale) {
                        // план или настройки изменились после открытия страницы: перечитать день и начать новый разговор —
                        // в прежних ответах цифры прежнего плана; вопрос остаётся в поле ввода
                        const note = document.createElement('span');
                        note.className = 'dp-ai-note';
                        note.textContent = 'Թարմացնելուց հետո կսկսվի նոր զրույց՝ նախորդ պատասխանները հին թվերով էին։';
                        err.appendChild(note);
                        if (!$('dpAiInput').value.trim()) { $('dpAiInput').value = q; aiResize(); }
                        again.innerHTML = '<i class="fas fa-rotate" aria-hidden="true"></i><span>Թարմացնել</span>';
                        again.addEventListener('click', async () => {
                            if (state.ai.busy || state.busy) return;
                            // разговор очищается, только если день перечитан; ошибка загрузки — на странице, разговор остаётся
                            if (await load(day) && state.day === day) {
                                state.ai.chats.set(day, []);
                                if (!$('dpAi').hidden) aiRender();
                            }
                            $('dpAiInput').focus();
                        });
                    } else {
                        again.innerHTML = '<i class="fas fa-rotate-right" aria-hidden="true"></i><span>Կրկնել</span>';
                        again.addEventListener('click', () => {
                            if (state.ai.busy) return;      // идёт другой вопрос — не терять этот
                            err.remove(); asked.forEach(x => x.remove()); aiAsk(q);
                        });
                        err.appendChild(document.createElement('br'));
                    }
                    err.appendChild(again);
                    if (wait.isConnected) wait.replaceWith(err); else { aiRender(); log.appendChild(err); }
                } finally {
                    state.ai.busy = false;
                    $('dpAiSend').disabled = false;
                    log.scrollTop = log.scrollHeight;
                }
            }
            function aiResize() {
                const el = $('dpAiInput');
                el.style.height = 'auto';
                el.style.height = Math.min(el.scrollHeight + 2, 150) + 'px';
            }
            function aiInit() {
                if (!$('dpAi')) return;
                $('dpAiOpen').addEventListener('click', () => aiOpen(true));
                $('dpAiClose').addEventListener('click', () => aiOpen(false));
                $('dpAiNew').addEventListener('click', () => { if (state.ai.busy) return; state.ai.chats.set(state.day, []); aiRender(); $('dpAiInput').focus(); });
                $('dpAiForm').addEventListener('submit', (e) => { e.preventDefault(); aiAsk($('dpAiInput').value, true); });
                $('dpAiInput').addEventListener('input', aiResize);
                // Enter — отправить, Shift+Enter — новая строка; Esc — закрыть панель
                $('dpAiInput').addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); aiAsk($('dpAiInput').value, true); }
                });
                $('dpAi').addEventListener('keydown', (e) => { if (e.key === 'Escape') aiOpen(false); });
            }

            aiInit();
            return {
                // день загружен или сменился: кнопка видна; открытая панель другого дня — разговор этого дня
                onDay(day) {
                    $('dpAiOpen').hidden = !$('dpAi').hidden;
                    if (!$('dpAi').hidden && state.ai.shownDay !== day) aiRender();
                },
            };
        },
    };
})();
