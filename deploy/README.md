# Автообновление Sales Dashboard с GitHub

Рабочий сервер сам подтягивает обновления из ветки `main` каждые 5 минут
и перезапускает дашборд, если появились новые коммиты.

## Как это работает

```
разработка (design-review) → Pull Request → merge в main
                                              ↓
        сервер: задача SalesDashboard-AutoUpdate (каждые 5 мин)
        git fetch → есть новое? → git reset --hard origin/main
        → pip install -r requirements.txt → перезапуск дашборда
```

Две задачи планировщика Windows (работают от SYSTEM, без входа пользователя):

| Задача | Что делает | Когда |
|---|---|---|
| `SalesDashboard-Server` | запускает `python app_v2.py`, лог в `logs\dashboard_*.log` | при загрузке сервера |
| `SalesDashboard-AutoUpdate` | pull + перезапуск, лог в `logs\autoupdate.log` | каждые 5 минут |

Локальные файлы сервера (`.env`, `kpi_*.json`, `dashboard_*.json` и прочие
настройки пользователя) в git не отслеживаются и при обновлении **не трогаются**.

## Требования на сервере

- Windows 10/11 или Windows Server
- Python 3.10+ в PATH (`python --version`)
- Git (`winget install --id Git.Git -e`)
- ODBC Driver 17 for SQL Server (уже стоит, раз дашборд работал)
- Сетевой доступ к SQL Server и к github.com

## Установка (один раз)

### 1. Создать токен доступа (репозиторий приватный)

GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → Generate new token:

- **Repository access**: Only select repositories → `sales-dashboard`
- **Permissions**: Contents → **Read-only** (больше ничего)
- Срок действия — на ваше усмотрение (после истечения обновления остановятся,
  нужно будет обновить URL: `git remote set-url origin https://x-access-token:НОВЫЙ_ТОКЕН@github.com/martirosyansss/sales-dashboard.git`)

### 2. Запустить установщик на сервере

Скопируйте `install_server.ps1` на сервер и выполните в PowerShell **от администратора**:

```powershell
powershell -ExecutionPolicy Bypass -File install_server.ps1 -Token "github_pat_ВАШ_ТОКЕН"
```

Установщик превратит `C:\Sales Dashboard` в git-клон **не удаляя** локальные
`.env` и JSON-настройки, поставит зависимости и зарегистрирует обе задачи.
Другая папка или ветка: `-AppDir "D:\Dashboard" -Branch design-review`.

### 3. Заполнить .env

Если `.env` на сервере не было, установщик создаст его из `.env.example` —
откройте и впишите реальные значения (`DB_PASSWORD` обязательно,
`ANTHROPIC_API_KEY` — если нужен AI-ассистент). Проще всего скопировать
готовый `.env` с рабочей машины.

### 4. Проверить

- Дашборд: `http://ИМЯ_СЕРВЕРА:5000` (порт 5000 должен быть открыт в брандмауэре
  для локальной сети: `New-NetFirewallRule -DisplayName "Sales Dashboard" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow`)
- Логи: `C:\Sales Dashboard\logs\`

## Управление

```powershell
# перезапустить дашборд вручную
schtasks /End /TN SalesDashboard-Server
schtasks /Run /TN SalesDashboard-Server

# обновиться немедленно, не дожидаясь 5 минут
schtasks /Run /TN SalesDashboard-AutoUpdate

# приостановить автообновление
schtasks /Change /TN SalesDashboard-AutoUpdate /DISABLE   # включить: /ENABLE
```

## Базы SQLite: резервная копия перед обновлением со сменой схемы

`courier.db` («Առաքիչ») и `route_optimizer.db` («Маршруты») лежат рядом с `app_v2.py`, в git не входят и
автообновлением не затираются. Программа при первом обращении сама переводит базу на новую схему
(одной транзакцией: сбой — база остаётся прежней). Старая программа базу новой схемы **не открывает**
(«создана более новой версией программы»).

Обучение «Развоза» по факту машин (learning-loop) меняет схемы: `courier.db` 5 → 6 (трек машины
`track_points`, индексы), `route_optimizer.db` 12 → 13 (журнал выученных норм `learned_norms`,
переключатели `learning_switch`). Обе миграции **только добавляют** таблицы и индексы — данные не меняются.

**До слияния таких изменений в `main`** (сервер подтянет их в течение 5 минут):

```powershell
schtasks /End /TN SalesDashboard-Server          # остановить, чтобы копия была целой (WAL)
$d = Get-Date -Format yyyyMMdd-HHmm
Copy-Item "C:\Sales Dashboard\courier.db" "C:\Sales Dashboard\courier.db.bak-$d"
Copy-Item "C:\Sales Dashboard\route_optimizer.db" "C:\Sales Dashboard\route_optimizer.db.bak-$d"
schtasks /Run /TN SalesDashboard-Server
```

Откат: остановить дашборд, вернуть прежний код (`git reset --hard <прежний коммит>` и приостановить
автообновление) и **восстановить обе базы из этих копий** — старая программа новые схемы не откроет.
Данные, принятые после обновления (трек, заправки, выученные нормы), при откате теряются.

## Безопасность

- Токен хранится в открытом виде в `C:\Sales Dashboard\.git\config` —
  поэтому он должен быть **fine-grained и только на чтение одного репозитория**.
- `FLASK_DEBUG` держите `False` (значение по умолчанию): отладчик Werkzeug
  позволяет выполнять код любому, кто откроет страницу ошибки.
- Перед открытием API терминалов «Առաքիչ» в интернет (Cloudflare Tunnel) запускайте дашборд
  через waitress: `DASHBOARD_SERVER=waitress` в `.env` (пакет — в `requirements.txt`). Без этой
  строки — встроенный сервер Flask, как раньше. Подробно — `deploy/COURIER_TUNNEL.md`.
- Обновление делает `git reset --hard` — любые ручные правки кода на сервере
  будут затёрты. Правки вносите только через git.
