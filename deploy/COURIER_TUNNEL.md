# «Առաքիչ»: Cloudflare Tunnel для терминалов водителей

Терминалы водителей ходят на сервер через интернет по `https://araqich.orix.am/api/courier/v1/…`.
Порты в роутере не открываются: на сервере работает `cloudflared`, он сам держит исходящее
соединение с Cloudflare. Наружу открыт **только** API терминалов, дашборд остаётся во внутренней сети.

```
терминал (SIM) ──HTTPS──► Cloudflare (araqich.orix.am) ──туннель──► cloudflared на сервере ──► http://localhost:5000
                           путь ^/api/courier/v1/ → дашборд       всё остальное → 404
```

Защита в два слоя:
1. **Туннель** (`ingress` в `config.yml`): наружу проксируется только путь `^/api/courier/v1/`, всё остальное —
   `http_status:404` на стороне Cloudflare.
2. **Flask** (`app_v2.py` → `courier.public_guard`): если запрос пришёл с хостом `COURIER_PUBLIC_HOST`
   (по умолчанию `araqich.orix.am`) или с заголовком `Cf-Connecting-Ip`, любой путь, кроме `/api/courier/v1/…`, — 404.
   Даже при ошибке в правилах туннеля дашборд снаружи не откроется. Исключение — вход и журнал гаража для роли
   «Гараж» (точный список путей — раздел 8; на сервере CT115 они открываются в nginx и туннеле только по разделу 8).

API терминалов не использует вход дашборда: у каждого терминала свой токен (выдаётся QR-кодом на странице
«Առաքիչ → Վարորդներ», в базе хранится только sha256, отзывается там же) и PIN водителя (5 ошибок → блок терминала
на 15 минут).

## Что нужно

- Доступ к аккаунту Cloudflare, в котором зона `orix.am` (NS `osmar`/`serenity.ns.cloudflare.com`).
- Права администратора на Windows-сервере, где работает дашборд (`python app_v2.py`, порт 5000).
- Дашборд уже обновлён до версии с разделом «Առաքիչ» (`pip install -r requirements.txt` — нужны `segno` для QR
  и `waitress` — сервер, см. ниже).
- **Дашборд запущен через waitress** (`DASHBOARD_SERVER=waitress`, шаг 6) — **обязательно до запуска туннеля**.
  Встроенный сервер Flask (`app.run`) — для разработки: он не рассчитан на интернет-трафик, медленные клиенты
  (терминалы на мобильной связи) занимают его потоки без предела по соединениям и тайм-аута. waitress — чистый Python,
  16 рабочих потоков, не больше 200 соединений, тайм-аут простоя соединения 120 с.

## 1. Установить cloudflared (на сервере, PowerShell от администратора)

```powershell
winget install --id Cloudflare.cloudflared -e
cloudflared --version
```

## 2. Войти в Cloudflare и создать туннель

```powershell
cloudflared tunnel login                 # откроется браузер: выберите зону orix.am
cloudflared tunnel create araqich        # запомните Tunnel ID; файл <TUNNEL_ID>.json появится в %USERPROFILE%\.cloudflared\
```

Перенесите учётные данные туда, где их увидит служба (она работает от SYSTEM):

```powershell
New-Item -ItemType Directory -Force C:\ProgramData\Cloudflare\cloudflared
Copy-Item "$env:USERPROFILE\.cloudflared\<TUNNEL_ID>.json" C:\ProgramData\Cloudflare\cloudflared\
```

Файл `<TUNNEL_ID>.json` — секрет (кто его имеет, может поднять туннель от вашего имени). В git его не класть.

## 3. Конфигурация

Скопируйте `deploy\cloudflared-courier.yml.example` в `C:\ProgramData\Cloudflare\cloudflared\config.yml`,
подставьте `<TUNNEL_ID>` (в двух местах). Проверка правил без запуска:

```powershell
cloudflared tunnel --config C:\ProgramData\Cloudflare\cloudflared\config.yml ingress validate
cloudflared tunnel --config C:\ProgramData\Cloudflare\cloudflared\config.yml ingress rule https://araqich.orix.am/api/courier/v1/ping   # → правило 0 (localhost:5000)
cloudflared tunnel --config C:\ProgramData\Cloudflare\cloudflared\config.yml ingress rule https://araqich.orix.am/                      # → http_status:404
```

## 4. DNS

```powershell
cloudflared tunnel route dns araqich araqich.orix.am
```

Команда создаёт в зоне `orix.am` запись `CNAME araqich → <TUNNEL_ID>.cfargotunnel.com` (proxied).
В зоне есть wildcard `*.orix.am` (заглушка хостинга «Welcome to …»): явная запись `araqich` его **перекрывает**
только для этого имени, остальные поддомены (в том числе `sales.orix.am` — «Orix Sales») не меняются.
Если запись `araqich` уже существует — команда откажет; удалите старую запись вручную в панели Cloudflare.

## 5. Запустить как службу Windows

```powershell
cloudflared service install
# служба читает C:\ProgramData\Cloudflare\cloudflared\config.yml; если нет — укажите путь в параметрах службы:
# sc.exe config cloudflared binPath= "\"C:\Program Files (x86)\cloudflared\cloudflared.exe\" --config C:\ProgramData\Cloudflare\cloudflared\config.yml tunnel run"
Start-Service cloudflared
Get-Service cloudflared
```

## 6. Настроить дашборд

В `.env` на сервере (не в git):

```
# обязательно до запуска туннеля: продакшн-сервер waitress вместо встроенного сервера Flask
# (без этой строки или без пакета waitress — запуск как раньше, app.run; в логе будет предупреждение)
DASHBOARD_SERVER=waitress
COURIER_PUBLIC_HOST=araqich.orix.am
# необязательно: адрес API в QR регистрации (по умолчанию https://<COURIER_PUBLIC_HOST>/api/courier/v1)
# COURIER_PUBLIC_URL=https://araqich.orix.am/api/courier/v1
# необязательно: файл базы терминалов (по умолчанию courier.db рядом с app_v2.py)
# COURIER_DB_PATH=C:\Sales Dashboard\courier.db
# только для проверки приложения без ERP: машина TEST, дата 2000-01-01 (на рабочем сервере не включать)
# COURIER_DEMO=1
# рекомендуется: «перец» PIN водителей — длинная случайная строка, хранится только здесь (не в courier.db).
# Что он защищает и как его сменить — ниже. Сгенерировать:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
# COURIER_PIN_PEPPER=<строка>
# только на время смены или снятия перца — прежний перец (см. ниже)
# COURIER_PIN_PEPPER_OLD=<прежняя строка>
```

### Перец PIN водителей (`COURIER_PIN_PEPPER`)

**Что защищает.** В `courier.db` у каждого водителя два значения, полученных из PIN:
- `pin_hash` — werkzeug pbkdf2 (число итераций библиотеки по умолчанию — сейчас 1 000 000, своя соль у записи);
- `pin_tag` — pbkdf2 (60 000 итераций) с одной солью на базу: по нему сервер узнаёт водителя по PIN за одно
  вычисление и проверяет, что PIN не повторяется у двух водителей.

PIN — 4–6 цифр, то есть всего 10 000–1 000 000 вариантов. **Без перца** украденную `courier.db` (или её копию)
перебирают офлайн по любому из двух значений — итерации только растягивают это до часов. **С перцем** `pin_hash`
считается от HMAC-SHA256(перец, PIN), а `pin_tag` — HMAC-SHA256(перец, …) от pbkdf2 PIN; перца нет ни в базе, ни
в её копиях: без `.env` не проверить ни одного варианта. В базе хранится только 8-значный отпечаток перца
(`drivers.pin_scheme` = `pepper:<отпечаток>`, без перца — `plain`; `pin_tag` = `p2:<отпечаток>:…`); по отпечатку
перец не восстановить.

Перец **не** защищает: от подбора PIN на самом сервере (от него — блокировка терминала: 5 ошибок → 15 минут);
от кражи `.env` вместе с базой (храните `.env` отдельно от резервных копий `courier.db`); токены терминалов и
сессий (в базе только sha256 от 32 случайных байт — подбирать нечего); PIN скрытых настроек терминала
(`admin_pin`, хеш без перца).

**Включить.** Записать `COURIER_PIN_PEPPER` в `.env` и перезапустить дашборд. `pin_tag` всех водителей
переводятся сразу (PIN для этого не нужен), `pin_hash` — при следующем входе каждого водителя: он входит как
обычно, первый вход дольше на ~0,3 с. Так же при входе пересчитываются хеши старого формата (60 000 итераций,
до схемы 5).

**Сменить** (перец мог попасть к чужим):
1. В `.env`: `COURIER_PIN_PEPPER=<новый>`, `COURIER_PIN_PEPPER_OLD=<прежний>`; перезапустить дашборд.
2. Водители входят как обычно: сервер узнаёт их по прежнему перцу и сразу переводит хеш и tag на новый.
3. Через 1–2 рабочих дня (каждый водитель вошёл хотя бы раз) убрать `COURIER_PIN_PEPPER_OLD`, перезапустить.
   Кто не успел войти, получит на терминале «PIN-ը պետք է նորից սահմանել գրասենյակում», а на странице
   «Վարորդներ» у него будет отметка «սահմանել նորից»: офис задаёт ему новый PIN («Փոփոխել»).

**Снять.** `COURIER_PIN_PEPPER_OLD=<текущий перец>`, строку `COURIER_PIN_PEPPER` убрать, перезапустить; когда все
водители вошли — убрать и `COURIER_PIN_PEPPER_OLD`.

**Перца нет в среде** (сервер запущен без `.env`, перец потерян или заменён без `COURIER_PIN_PEPPER_OLD`). При
запуске в логе — WARNING «PIN активных водителей … сделан с перцем …, которого нет в среде». PIN таких водителей не
проверить: вход отвечает «Սխալ PIN կամ PIN-ը պետք է նորից սահմանել գրասենյակում» (опечатку и такой PIN не
различить; попытка — в счёт блокировки), на «Վարորդներ» — «սահմանել նորից». Их `pin_tag` **не** сбрасываются:
вернули перец в `.env` и перезапустили — водители входят как раньше. Пока перца нет, новый PIN другому водителю не
задаётся («Չի հաջողվում ստուգել PIN-ի կրկնությունը. վերականգնեք COURIER_PIN_PEPPER-ը»): PIN таких водителей
неизвестен и может совпасть. Если перец потерян насовсем — подтвердить на этой ошибке сброс: PIN всех таких водителей
удаляются (их сессии — тоже), каждому задать новый PIN. Терминалы и данные дня это не затрагивает. Перец храните так
же надёжно, как пароль ERP (копия — вне сервера).

Перезапустить дашборд (`schtasks /End /TN SalesDashboard-Server` и `schtasks /Run /TN SalesDashboard-Server`).
В логе `logs\dashboard_*.log` должна быть строка `Server: waitress, 16 потоков` — без неё туннель не включать.

При первом запуске новой версии база `courier.db` сама переходит на текущую схему (одной транзакцией; схема 2 —
точки дня становятся снимками, схема 3 — содержимое точки хранится один раз на все снимки, схема 4 — решения по
предложениям водителей, схема 5 — схема хеша PIN и наибольшее количество строк накладных; прежние данные
сохраняются). Промежуточные снимки `/day` без событий старше 7 дней удаляются автоматически (последний снимок
каждой машины и даты и снимки, на которые ссылаются события, хранятся всегда; наибольшее количество каждой строки —
тоже, поэтому опоздавшее событие проверяется как раньше). Откатить программу на прошлую версию после этого нельзя —
старая версия откажется открывать базу новой схемы; перед обновлением сделайте копию `courier.db`.

`courier.db`, папки `courier_photos\` и `courier_apk\` лежат рядом с `app_v2.py`, в git не попадают и при
автообновлении не удаляются. Их нужно включить в резервное копирование: там деньги, сканы маркировки и фото.

## 7. Проверить

С любого компьютера вне офиса (или с телефона по мобильному интернету):

| Запрос | Ожидается |
|---|---|
| `https://araqich.orix.am/` | 404 |
| `https://araqich.orix.am/login` | 404 (форма входа — только после раздела 8) |
| `https://araqich.orix.am/api/courier/v1/ping` (без токена) | 401 `{"error": "unauthorized", …}` |
| то же с `Authorization: Bearer <токен терминала>` | 200 `{"ok": true, …}` |
| `http://<сервер>:5000/` из офиса | дашборд, как раньше |

```powershell
curl.exe -i https://araqich.orix.am/
curl.exe -i https://araqich.orix.am/api/courier/v1/ping
```

## Обслуживание

- Логи службы: `Get-WinEvent -LogName Application -MaxEvents 50 | Where-Object ProviderName -eq cloudflared`.
- Обновить cloudflared: `winget upgrade --id Cloudflare.cloudflared`, затем `Restart-Service cloudflared`.
- Потерян терминал — «Առաքիչ → Վարորդներ → Անջատել»: токен сразу перестаёт работать (401).
- Сессия водителя живёт до 04:00 следующего дня, но не дольше 20 часов; смена PIN или выключение водителя сразу
  закрывают его сессии.
- Пределы на один терминал в день: 300 фото и 300 МБ фото, 1000 сохранённых отклонённых событий (контракт §5 п. 10).
  Упёрся в предел — смотрите терминал (зациклившаяся отправка?), а не поднимайте предел.
- Остановить доступ извне целиком: `Stop-Service cloudflared` (дашборд в офисе продолжит работать).

## 8. Журнал гаража из интернета (сервер CT115, ответ владельца №53)

Начальник гаража открывает журнал «Ավտոտնակ» с телефона: `https://araqich.orix.am/login` → `/routes/garage`.
Раздел — только для CT115 (коннектор туннеля на 192.168.1.11 → nginx CT115 :5000 → waitress). Старая схема из
разделов 1–6 (cloudflared на том же Windows-сервере → `localhost:5000` без nginx) для журнала не подходит: без
`X-Forwarded-Proto https` форма входа не пройдёт проверку CSRF, а счётчик неудач будет общим на весь интернет.

### Что открыто снаружи (три слоя — один и тот же список)

| Путь | Методы | Что |
|---|---|---|
| `/login` | GET, POST | форма входа; снаружи входит **только роль «Гараж»** с паролем не короче 10 символов |
| `/logout` | POST | выход |
| `/routes/garage` | GET | страница журнала (сессия «Гаража»; без входа — на `/login`) |
| `/api/routes/garage`, `/api/routes/garage/…` | GET, POST | API журнала (POST — с CSRF, как в офисе) |
| `/routes/live`, `/api/routes/live`, `/api/routes/live/…` | GET | машины на карте сейчас «Մեքենաները առցանց» (№76; сессия «Гаража», только чтение) |
| `/static/css/routes_live.css`, `/static/js/routes_live.js` | GET | статика карты машин (№76) |
| `/favicon.ico`, `/static/css/tokens.css`, `/static/css/base.css`, `/static/css/routes.css`, `/static/css/routes_garage.css`, `/static/js/base.js`, `/static/js/routes_garage.js`, `/static/js/routes_basemap.js` | GET | статика страницы журнала (у страницы входа своей нет — CDN; Leaflet карты дня «Նորմ և փաստ» — тоже CDN) |
| `/api/courier/v1/…` | как раньше | API терминалов |

Всё остальное снаружи — 404: в приложении (`app_v2.py`: `_public_path_allowed`, `_PUBLIC_STATIC`), в nginx и в
туннеле. Сессия администратора или пользователя по территориям, пришедшая на `araqich.orix.am`, — тоже 404; вход
таких логинов снаружи — та же ошибка, что и при неверном пароле (существование логина не выдаётся), и попытка идёт в
счёт блокировки. Страница входа снаружи — нейтральная, по-армянски («Ավտոտնակ», без названия программы); в офисе —
прежняя. Ответы снаружи: cookie сессии `Secure` (плюс `HttpOnly`, `SameSite=Lax`, как в офисе), заголовок
`Strict-Transport-Security: max-age=31536000`. В офисной сети — без изменений.

**Новый файл статики на странице журнала** (новый `<script>`/`<link>` в `routes_garage.html` или `base_v2.html`)
нужно добавить во все три места: `_PUBLIC_STATIC` в `app_v2.py`, `location` в nginx и правило туннеля. Иначе снаружи
этот файл — 404 (страница без него), больше ничего не открывается.

### Защита входа из интернета

- **По логину и адресу:** 5 неудач → блок на 5 минут. Адрес клиента — `Cf-Connecting-Ip` (его ставит Cloudflare), но
  **только** если запрос пришёл от узла туннеля из `COURIER_TUNNEL_PEERS` (после ProxyFix, т.е. `X-Forwarded-For` от
  nginx); IPv6 — по сети /64. От других адресов заголовок не учитывается (подделка из офиса не помогает).
- **По логину со всего интернета:** больше 30 неудач за час (с любых адресов) → вход этим логином из интернета — 429
  до конца окна; в журнале — WARNING. Из офиса этот логин входит как обычно.
- **Не больше 2 проверок пароля одновременно** (одна — ≈ 0,3 с процессора): лишние попытки входа из интернета сразу
  получают 429 без проверки — поток попыток не займёт сервер, API терминалов и офис работают дальше.
- **nginx** (шаг 6): POST `/login` — не чаще 6 в минуту с одного адреса (запас 5) и 30 в минуту всего (запас 10), сверх —
  429. Адрес здесь — целиком (IPv6 не по /64): общий предел 30 в минуту держит и тех, кто меняет адреса.
- Ответ 429 — та же страница входа: «Չափազանց շատ փորձեր։ Կրկնեք մի քանի րոպեից։» (причина видна только в журнале;
  отказы 429 пишутся не чаще раза в минуту на причину — со счётом пропущенных, поток попыток журнал не забьёт).
- **Журнал входов** (лог дашборда, строки `[Auth]`): «Вход», «Неудачный вход», «Вход … отклонён (причина)», «Вход из
  интернета не разрешён (роль …, пароль верный/неверный)», «Выход» — с логином, адресом клиента и «интернет»/«офис».
  Пароль в журнал не пишется никогда. Строка «не разрешён: 'admin' (роль admin, пароль **верный**)» значит, что пароль
  администратора знает кто-то в интернете, — сменить его.
- **Сессии:** действуют не дольше 7 дней от входа (работа срок не продлевает), потом — вход заново. Смена пароля
  (Настройки → Пользователи) закрывает все сессии этого логина (любая роль). «Выход» роли «Гараж» — тоже все её
  сессии на всех устройствах; в офисе «Выход» — как раньше, только этот браузер. Телефон начальника гаража
  потерян — в офисе сменить пароль его логина. `users.json` записывается атомарно (временный файл → замена): сбой
  посреди записи не портит его.
- Хэш пароля со старыми параметрами пересчитывается при ближайшем входе (время проверки не отличается от проверки
  несуществующего логина).
- `users.json` читается и меняется под одной блокировкой процесса: одновременные вход, выход и правка пользователей
  не теряют изменения друг друга и не возвращают удалённого пользователя. На Windows (ПК) замена файла повторяется до
  5 раз, если его держит открытым другая программа; не удалось — в журнале ERROR, а у «Выхода» «Гаража» — WARNING
  «сессии на других устройствах НЕ отозваны» (тогда сменить пароль этого логина).
- **Принятый остаточный риск.** Кто знает логин «Гаража», может закрыть вход этим логином **из интернета**: примерно
  одна неверная попытка раз в 120 секунд держит бюджет «30 неудач за час» исчерпанным. Начальник гаража, уже вошедший
  на телефоне, работает дальше (сессия до 7 дней); вход из офиса не затронут; каждая попытка — в журнале
  («Неудачный вход», адрес). Поэтому логин «Гаража» — **неочевидный** (не `garage`, `garaj`, имя или фамилия;
  например, `avt-` + 6 случайных символов). Запоминание «знакомого устройства» в cookie, чтобы обходить бюджет, —
  не делаем.
- Возможное усиление позже: Cloudflare Access (одноразовый код на почту) перед путями журнала — владелец пока
  отказался («хватит пароля»).

### Порядок выкладки (с OK владельца)

1. **Релиз CT115** с этой версией — по `/opt/araqich/deploy/DEPLOY.md` (схема баз 14 → 17: время у магазина, редизайн
   «Развоза», журнал гаража). Пока nginx и туннель не изменены (шаги 6–7), снаружи по-прежнему открыт только API
   терминалов — приложение само ничего не открывает. **После выкладки все (и офис) один раз входят заново:** прежние
   cookie без отметки версии пароля и времени входа больше не действуют.
2. **Версии пакетов на CT115** (известные уязвимости старых): `waitress ≥ 3.0.1`, `Werkzeug ≥ 3.0.6` — так в
   `requirements.txt`. Проверить тем же Python, которым запущен дашборд:
   ```sh
   python3 -c "import importlib.metadata as m; print('waitress', m.version('waitress'), 'Werkzeug', m.version('werkzeug'))"
   ```
   Ниже — поставить по `requirements.txt` (как при релизе) и проверить снова; без этого шаги 6–7 не делать.
3. **`.env` CT115** — проверить (без них не включать шаги 6–7):
   ```
   FLASK_TRUSTED_PROXY_HOPS=1          # уже есть: request.remote_addr = X-Forwarded-For от nginx
   COURIER_PUBLIC_HOST=araqich.orix.am  # по умолчанию такое же
   # узлы туннеля, которым верим в Cf-Connecting-Ip (через запятую); по умолчанию 192.168.1.11.
   # Пустая строка — не верить никому (счётчик неудач — общий на весь интернет).
   # COURIER_TUNNEL_PEERS=192.168.1.11
   ```
   Перезапустить дашборд после правки `.env`.
4. **Логин начальнику гаража создаётся в дашборде CT115** (не на ПК: у CT115 свой `users.json`): из офиса
   `https://192.168.1.24` → Настройки → Пользователи → «Добавить», роль «Гараж», пароль **не короче 10 символов**
   (лучше 14+ случайных: `python3 -c "import secrets; print(secrets.token_urlsafe(12))"`). Проверить в офисе: вход →
   сразу «Ավտոտնակ», других страниц нет. Сменить роль существующего пользователя на «Гараж» можно только вместе с
   новым паролем.
5. **Cloudflare, зона orix.am: «Always Use HTTPS» — ОБЯЗАТЕЛЬНО, до шагов 6–7.** SSL/TLS → Edge Certificates →
   Always Use HTTPS = On (или правило перенаправления на https для `araqich.orix.am`). Почему: без него
   `http://araqich.orix.am/login` отдаётся по обычному HTTP. Начальник гаража набрал адрес без `https://`, открыл
   старую закладку или ссылку из мессенджера — форма входа уходит **открытым текстом**, и пароль виден по дороге
   (мобильная сеть, Wi-Fi, провайдеры). HSTS при первом заходе ещё не работает (браузер о сайте не знает), а `Secure`
   защищает только cookie, не пароль в форме. Проверка — только после неё дальше:
   ```sh
   curl -sI http://araqich.orix.am/login | head -3     # 301 или 308, Location: https://araqich.orix.am/login
   ```
6. **nginx CT115.**

   a) Пределы на вход — файл `/etc/nginx/conf.d/araqich-garage-limits.conf` (контекст `http {}`: `conf.d/*.conf`
   подключается внутри `http`; проверить — `nginx -T | grep araqich_login`):
   ```nginx
   # Вход журнала гаража из интернета (№53): считаются только POST /login от узла туннеля (192.168.1.11) и с самого
   # CT115 (127.0.0.1). limit_req работает раньше allow/deny: без привязки к $remote_addr машина в офисе с
   # поддельным Cf-Connecting-Ip заполняла бы общий счётчик и закрывала вход начальнику гаража.
   map "$request_method:$remote_addr" $araqich_login_ip {
       "~^POST:(192\.168\.1\.11|127\.0\.0\.1)$"  $http_cf_connecting_ip;
       default                                   '';
   }
   map "$request_method:$remote_addr" $araqich_login_all {
       "~^POST:(192\.168\.1\.11|127\.0\.0\.1)$"  login;
       default                                   '';
   }
   limit_req_zone $araqich_login_ip  zone=araqich_login_ip:10m rate=6r/m;
   limit_req_zone $araqich_login_all zone=araqich_login_all:1m rate=30r/m;
   ```
   (Пустой ключ — GET и прочие методы, запросы не от туннеля — nginx не считает; их всё равно не пускает снипет b.)

   b) Общая часть location'ов — файл `/etc/nginx/snippets/araqich-garage-public.conf`:
   ```nginx
   # Журнал гаража из интернета (№53). В приложение — только запросы туннеля: с Cf-Connecting-Ip и от 192.168.1.11
   # (или 127.0.0.1 — проверка на самом CT115); значит, каждый пропущенный запрос приложение видит как внешний.
   # Остальное — ровно как location /: без заголовка Cloudflare (418) и не от туннеля (403) → @araqich_office.
   error_page 403 418 = @araqich_office;
   if ($http_cf_connecting_ip = '') { return 418; }
   allow 192.168.1.11;
   allow 127.0.0.1;
   deny all;
   client_max_body_size 64k;
   proxy_intercept_errors off;   # ответы приложения (401/403/404/429) — как есть; error_page — только для 403/418 nginx
   proxy_pass http://araqich_backend;
   proxy_set_header Host $http_host;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_set_header X-Forwarded-Proto https;
   # остальные proxy_* (таймауты, proxy_http_version и т.п.) — как в location ^~ /api/courier/v1/
   ```
   `if … return` срабатывает раньше `allow/deny` (фаза rewrite) и внутри `if` нет ничего, кроме `return`, — так
   безопасно. Ответы самого приложения `error_page` не перехватывает (`proxy_intercept_errors off` — явно).

   c) В `server { listen 5000; … }` рядом с `location ^~ /api/courier/v1/` (тот блок и `location /` не меняются):
   ```nginx
   location = /login {
       limit_req zone=araqich_login_ip  burst=5  nodelay;
       limit_req zone=araqich_login_all burst=10 nodelay;
       limit_req_status 429;
       include snippets/araqich-garage-public.conf;
   }
   location = /logout                       { include snippets/araqich-garage-public.conf; }
   location = /routes/garage                { include snippets/araqich-garage-public.conf; }
   location = /api/routes/garage            { include snippets/araqich-garage-public.conf; }
   location ^~ /api/routes/garage/          { include snippets/araqich-garage-public.conf; }
   location = /favicon.ico                  { include snippets/araqich-garage-public.conf; }
   location = /static/css/tokens.css        { include snippets/araqich-garage-public.conf; }
   location = /static/css/base.css          { include snippets/araqich-garage-public.conf; }
   location = /static/css/routes.css        { include snippets/araqich-garage-public.conf; }
   location = /static/css/routes_garage.css { include snippets/araqich-garage-public.conf; }
   location = /static/js/base.js            { include snippets/araqich-garage-public.conf; }
   location = /static/js/routes_garage.js   { include snippets/araqich-garage-public.conf; }
   location = /static/js/routes_basemap.js  { include snippets/araqich-garage-public.conf; }
   # №76 «Մեքենաները առցանց» (карта машин; приложение пускает только GET и только сессию «Гаража»)
   location = /routes/live                  { include snippets/araqich-garage-public.conf; }
   location = /api/routes/live              { include snippets/araqich-garage-public.conf; }
   location ^~ /api/routes/live/            { include snippets/araqich-garage-public.conf; }
   location = /static/css/routes_live.css   { include snippets/araqich-garage-public.conf; }
   location = /static/js/routes_live.js     { include snippets/araqich-garage-public.conf; }

   # Не от туннеля или без заголовка Cloudflare (офис по старой ссылке http://192.168.1.24:5000/login, прочие
   # процессы на 192.168.1.11, curl на CT115 без заголовка) — ровно как location /.
   location @araqich_office {
       if ($http_cf_connecting_ip != '') { return 404; }
       if ($host = araqich.orix.am) { return 404; }
       return 308 https://192.168.1.24$request_uri;
   }
   ```
   Затем `nginx -t && systemctl reload nginx`.
7. **Туннель.** Правила для `araqich.orix.am` — **перед** последним `service: http_status:404`, `service` — тот же,
   что у существующего правила `^/api/courier/v1/` (nginx CT115 :5000). Если туннель настроен файлом `config.yml` на
   192.168.1.11:
   ```yaml
     - hostname: araqich.orix.am
       path: '^/(login|logout|favicon\.ico|routes/garage|routes/live)$'
       service: <как у правила ^/api/courier/v1/>
     - hostname: araqich.orix.am
       path: '^/api/routes/(garage|live)(/[a-z0-9_-]+)*$'
       service: <как у правила ^/api/courier/v1/>
     - hostname: araqich.orix.am
       path: '^/static/(css/(tokens|base|routes|routes_garage|routes_live)\.css|js/(base|routes_garage|routes_basemap|routes_live)\.js)$'
       service: <как у правила ^/api/courier/v1/>
   ```
   Проверка и перезапуск: `cloudflared tunnel --config <config.yml> ingress validate`;
   `… ingress rule https://araqich.orix.am/login` → новое правило; `… ingress rule https://araqich.orix.am/settings`
   → `http_status:404`; перезапустить службу `cloudflared`. Если туннель управляется из кабинета Cloudflare
   (Zero Trust → Networks → Tunnels → туннель → Public Hostname): добавить три записи — поддомен `araqich`, домен
   `orix.am`, Path — те же три выражения, Service — как у записи API.

### Проверка после выкладки

С телефона по мобильному интернету (не из офиса):

| Запрос | Ожидается |
|---|---|
| `https://araqich.orix.am/login` | форма входа «Ավտոտնակ» (по-армянски, без «Sales Dashboard») |
| `http://araqich.orix.am/login` | 301/308 на `https://…` (шаг 5) |
| вход логином администратора (верный пароль) | «Սխալ մուտքանուն կամ գաղտնաբառ»; в журнале — «не разрешён … роль admin» |
| вход логином «Гаража» | сразу «Ավտոտնակ»; список машин, записи, пробег открываются |
| `https://araqich.orix.am/`, `/settings`, `/routes`, `/routes/garage/`, `/static/js/settings.js` | 404 |
| `https://araqich.orix.am/api/courier/v1/ping` | 401, как раньше |

```sh
curl -sI https://araqich.orix.am/login | grep -iE 'strict-transport|set-cookie'
#   strict-transport-security: max-age=31536000
#   set-cookie: session=…; Secure; HttpOnly; Path=/; SameSite=Lax
curl -s -o /dev/null -w '%{http_code}\n' https://araqich.orix.am/settings             # 404
curl -s -o /dev/null -w '%{http_code}\n' https://araqich.orix.am/api/routes/garage    # 401 (без входа)
# предел nginx: POST без токена формы приложение отклоняет (403, пароль не проверяется) — первые 6 ответов 403, дальше 429
for i in $(seq 1 10); do curl -s -o /dev/null -w '%{http_code} ' -X POST https://araqich.orix.am/login; done; echo
```

На самом CT115 (запросы с 127.0.0.1 пропускаются, но без заголовка Cloudflare — как `location /`):
```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: araqich.orix.am' http://127.0.0.1:5000/login                   # 404
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: araqich.orix.am' -H 'Cf-Connecting-Ip: 203.0.113.1' \
     http://127.0.0.1:5000/login                                                                                  # 200
```

Из офиса — как раньше: `https://192.168.1.24/` — дашборд (без HSTS, страница входа прежняя); `http://192.168.1.24:5000/login` —
308 на `https://192.168.1.24/login`:
```sh
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://192.168.1.24:5000/login
```

В журнале дашборда после входа с телефона — строка `[Auth] Вход: '<логин>' (роль garage), IP <адрес телефона>, интернет`
(адрес — внешний, не 192.168.1.11: значит, `COURIER_TUNNEL_PEERS` и `X-Forwarded-For` настроены верно).

**Закрыть журнал снаружи** (дашборд и API терминалов работают дальше): убрать три правила туннеля и `location`'ы
шага 6c (`nginx -t && systemctl reload nginx`). Приложение менять не нужно.
