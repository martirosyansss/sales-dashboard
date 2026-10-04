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
| `/favicon.ico`, `/static/css/tokens.css`, `/static/css/base.css`, `/static/css/routes.css`, `/static/css/routes_garage.css`, `/static/js/base.js`, `/static/js/routes_garage.js` | GET | статика страницы журнала (у страницы входа своей нет — CDN) |
| `/api/courier/v1/…` | как раньше | API терминалов |

Всё остальное снаружи — 404: в приложении (`app_v2.py`: `_public_path_allowed`, `_PUBLIC_STATIC`), в nginx и в
туннеле. Сессия администратора или пользователя по территориям, пришедшая на `araqich.orix.am`, — тоже 404; вход
таких логинов снаружи — та же ошибка «Неверный логин или пароль», что и при неверном пароле (существование логина не
выдаётся), и попытка идёт в счёт блокировки. Ответы снаружи: cookie сессии `Secure` (плюс `HttpOnly`,
`SameSite=Lax`, как в офисе), заголовок `Strict-Transport-Security: max-age=31536000`. В офисной сети — без изменений.

**Новый файл статики на странице журнала** (новый `<script>`/`<link>` в `routes_garage.html` или `base_v2.html`)
нужно добавить во все три места: `_PUBLIC_STATIC` в `app_v2.py`, `location` в nginx и правило туннеля. Иначе снаружи
этот файл — 404 (страница без него), больше ничего не открывается.

Подбор пароля: 5 неудач по одному логину с одного адреса → блок на 5 минут. Адрес клиента из интернета —
`Cf-Connecting-Ip` (его ставит Cloudflare), но **только** если запрос пришёл от узла туннеля из
`COURIER_TUNNEL_PEERS` (после ProxyFix, т.е. `X-Forwarded-For` от nginx); IPv6 — по сети /64. От других адресов
заголовок не учитывается (подделка из офиса не помогает).

### Порядок выкладки (с OK владельца)

1. **Релиз CT115** с этой версией — по `/opt/araqich/deploy/DEPLOY.md` (схема баз 14 → 17: время у магазина, редизайн
   «Развоза», журнал гаража). Пока nginx и туннель не изменены (шаги 4–5), снаружи по-прежнему открыт только API
   терминалов — приложение само ничего не открывает.
2. **`.env` CT115** — проверить (без них не включать шаги 4–5):
   ```
   FLASK_TRUSTED_PROXY_HOPS=1          # уже есть: request.remote_addr = X-Forwarded-For от nginx
   COURIER_PUBLIC_HOST=araqich.orix.am  # по умолчанию такое же
   # узлы туннеля, которым верим в Cf-Connecting-Ip (через запятую); по умолчанию 192.168.1.11.
   # Пустая строка — не верить никому (счётчик неудач — общий на весь интернет).
   # COURIER_TUNNEL_PEERS=192.168.1.11
   ```
   Перезапустить дашборд после правки `.env`.
3. **Логин начальнику гаража создаётся в дашборде CT115** (не на ПК: у CT115 свой `users.json`): из офиса
   `https://192.168.1.24` → Настройки → Пользователи → «Добавить», роль «Гараж», пароль **не короче 10 символов**
   (лучше 14+ случайных: `python3 -c "import secrets; print(secrets.token_urlsafe(12))"`). Проверить в офисе: вход →
   сразу «Ավտոտնակ», других страниц нет. Сменить роль существующего пользователя на «Гараж» можно только вместе с
   новым паролем.
4. **nginx CT115, сервер :5000.** Файл `/etc/nginx/snippets/araqich-garage-public.conf`:
   ```nginx
   # Журнал гаража из интернета (№53): только от узла туннеля и локально; из офиса — как location / (на https).
   allow 192.168.1.11;
   allow 127.0.0.1;
   deny all;
   error_page 403 = @araqich_office;
   proxy_pass http://araqich_backend;
   proxy_set_header Host $http_host;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_set_header X-Forwarded-Proto https;
   # остальные proxy_* (таймауты, proxy_http_version и т.п.) — как в location ^~ /api/courier/v1/
   ```
   В `server { listen 5000; … }` рядом с `location ^~ /api/courier/v1/` (тот блок и `location /` не меняются):
   ```nginx
   location = /login                        { include snippets/araqich-garage-public.conf; }
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

   # Не от туннеля (офис по старой ссылке http://192.168.1.24:5000/login и т.п.) — ровно как location /.
   location @araqich_office {
       if ($http_cf_connecting_ip != '') { return 404; }
       if ($host = araqich.orix.am) { return 404; }
       return 308 https://192.168.1.24$request_uri;
   }
   ```
   `error_page 403 = @araqich_office` нужен, чтобы офис по `http://192.168.1.24:5000/login` получал, как и сейчас,
   308 на `https://192.168.1.24/login`, а не 403 от `deny all` (ответы самого приложения он не перехватывает:
   `proxy_intercept_errors` выключен). Затем: `nginx -t && systemctl reload nginx`.
5. **Туннель.** Правила для `araqich.orix.am` — **перед** последним `service: http_status:404`, `service` — тот же,
   что у существующего правила `^/api/courier/v1/` (nginx CT115 :5000). Если туннель настроен файлом `config.yml` на
   192.168.1.11:
   ```yaml
     - hostname: araqich.orix.am
       path: '^/(login|logout|favicon\.ico|routes/garage)$'
       service: <как у правила ^/api/courier/v1/>
     - hostname: araqich.orix.am
       path: '^/api/routes/garage(/[a-z0-9_-]+)*$'
       service: <как у правила ^/api/courier/v1/>
     - hostname: araqich.orix.am
       path: '^/static/(css/(tokens|base|routes|routes_garage)\.css|js/(base|routes_garage)\.js)$'
       service: <как у правила ^/api/courier/v1/>
   ```
   Проверка и перезапуск: `cloudflared tunnel --config <config.yml> ingress validate`;
   `… ingress rule https://araqich.orix.am/login` → новое правило; `… ingress rule https://araqich.orix.am/settings`
   → `http_status:404`; перезапустить службу `cloudflared`. Если туннель управляется из кабинета Cloudflare
   (Zero Trust → Networks → Tunnels → туннель → Public Hostname): добавить три записи — поддомен `araqich`, домен
   `orix.am`, Path — те же три выражения, Service — как у записи API.
6. **Cloudflare, зона orix.am:** SSL/TLS → Edge Certificates → **Always Use HTTPS** включено (или правило
   перенаправления на https для `araqich.orix.am`): пароль не должен идти по http ни разу. HSTS приложение выдаёт само.

### Проверка после выкладки

С телефона по мобильному интернету (не из офиса):

| Запрос | Ожидается |
|---|---|
| `https://araqich.orix.am/login` | форма входа |
| `http://araqich.orix.am/login` | 301/308 на `https://…` (шаг 6) |
| вход логином администратора (верный пароль) | «Неверный логин или пароль» |
| вход логином «Гаража» | сразу «Ավտոտնակ»; список машин, записи, пробег открываются |
| `https://araqich.orix.am/`, `/settings`, `/routes`, `/routes/garage/`, `/static/js/settings.js` | 404 |
| `https://araqich.orix.am/api/courier/v1/ping` | 401, как раньше |

```sh
curl -sI https://araqich.orix.am/login | grep -iE 'strict-transport|set-cookie'
#   strict-transport-security: max-age=31536000
#   set-cookie: session=…; Secure; HttpOnly; Path=/; SameSite=Lax
curl -s -o /dev/null -w '%{http_code}\n' https://araqich.orix.am/settings             # 404
curl -s -o /dev/null -w '%{http_code}\n' https://araqich.orix.am/api/routes/garage    # 401 (без входа)
```

Из офиса — как раньше: `https://192.168.1.24/` — дашборд (без HSTS); `http://192.168.1.24:5000/login` —
308 на `https://192.168.1.24/login`:
```sh
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://192.168.1.24:5000/login
```

**Закрыть журнал снаружи** (дашборд и API терминалов работают дальше): убрать три правила туннеля и `location`'ы
шага 4 (`nginx -t && systemctl reload nginx`). Приложение менять не нужно.
