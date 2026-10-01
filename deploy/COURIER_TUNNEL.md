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
   Даже при ошибке в правилах туннеля дашборд снаружи не откроется.

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

**Перец потерян.** Проверить PIN водителей, чей хеш сделан с ним, нельзя: вход отвечает «PIN-ը պետք է նորից
սահմանել գրասենյակում» (неверная попытка — как обычно, в счёт блокировки), на «Վարորդներ» — «սահմանել նորից».
Задать новый `COURIER_PIN_PEPPER`, перезапустить и каждому такому водителю задать новый PIN. Терминалы, сессии и
данные дня это не затрагивает. Поэтому перец храните так же надёжно, как пароль ERP (копия — вне сервера).

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
| `https://araqich.orix.am/login` | 404 |
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
