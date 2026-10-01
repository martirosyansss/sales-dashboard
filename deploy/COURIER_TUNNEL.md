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
- Дашборд уже обновлён до версии с разделом «Առաքիչ» (`pip install -r requirements.txt` — нужен `segno` для QR).

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
COURIER_PUBLIC_HOST=araqich.orix.am
# необязательно: адрес API в QR регистрации (по умолчанию https://<COURIER_PUBLIC_HOST>/api/courier/v1)
# COURIER_PUBLIC_URL=https://araqich.orix.am/api/courier/v1
# необязательно: файл базы терминалов (по умолчанию courier.db рядом с app_v2.py)
# COURIER_DB_PATH=C:\Sales Dashboard\courier.db
# только для проверки приложения без ERP: машина TEST, дата 2000-01-01 (на рабочем сервере не включать)
# COURIER_DEMO=1
```

Перезапустить дашборд (`schtasks /End /TN SalesDashboard-Server` и `schtasks /Run /TN SalesDashboard-Server`).

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
- Остановить доступ извне целиком: `Stop-Service cloudflared` (дашборд в офисе продолжит работать).
