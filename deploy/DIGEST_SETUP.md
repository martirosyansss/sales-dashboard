# Բիզնեսի զարկերակ — проактивные алерты «где дыра»

Скрипт [`send_digest.py`](send_digest.py) собирает короткий дайджест по здоровью бизнеса
(индекс здоровья, топ-проблемы в деньгах, худшие территории, кому звонить, динамика за 12 мес)
и отправляет его в **Telegram** и/или на **Email**. Данные берёт из уже работающих KPI-эндпоинтов
(`/api/managers/kpi/health*`) — только чтение, в БД напрямую не ходит.

## Что приходит (пример)

```
📊 Բիզնեսի զարկերակ · 2026-07
🟢 Առողջություն՝ 87/100 — Առողջ վիճակ
💰 Հասույթ՝ 25 223 273 ֏ (+8.4% YoY) · Պարտք՝ 24 355 213 ֏

🔴 Որտեղ ենք կորցնում գումар՝
• Պարտքը աճел է ... (−6 391 673 ֏)
• Թույլ հավաքագրում (−5 744 237 ֏)
...
📞 Ум зангахарел (60+ օр)՝
• Կակтус ... 0XX-XXX-XXX — 193 965 ֏ · 538 օр
📅 Դինамика (12 ամիս)՝ հասույթ 📉 18.5%⚠️ · պарtق 📉 43.3%
```

## Быстрая проверка (без отправки)

```powershell
python deploy\send_digest.py --dry-run
```
Покажет текст дайджеста в консоли и сохранит его в `deploy/last_digest.txt`.

## Telegram (самый простой путь)

1. Напиши **@BotFather** в Telegram → `/newbot` → получи **токен** вида `123456:ABC-...`.
2. Напиши своему новому боту любое сообщение (иначе он не сможет тебе писать).
3. Узнай свой **chat_id**: напиши **@userinfobot** → он пришлёт `Id: 12345678`.
4. Добавь в `.env` (в корне проекта):
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-...
   TELEGRAM_CHAT_ID=12345678
   ```
5. Проверь отправку: `python deploy\send_digest.py`

Для группы: добавь бота в группу, chat_id группы отрицательный (узнать через @userinfobot в группе
или @getidsbot).

## Email (альтернатива/дополнение)

Добавь в `.env`:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=app-password        # для Gmail — «пароль приложения», не основной пароль
DIGEST_EMAIL_TO=owner@company.com
DIGEST_EMAIL_FROM=you@gmail.com
```
Telegram и Email можно включить одновременно — уйдёт в оба.

## Периодичность

- `DIGEST_PERIOD=this-month` (по умолчанию) — текущий месяц с накоплением.
- `--period last-month` — предыдущий полный месяц (для месячного итога).

## Расписание (Windows Task Scheduler)

Ежедневно в 09:00 (по образцу существующих задач деплоя):

```powershell
$py = (Get-Command python).Source
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"C:\Sales Dashboard\deploy\send_digest.ps1`" -PythonExe `"$py`""
$trigger = New-ScheduledTaskTrigger -Daily -At 09:00
Register-ScheduledTask -TaskName 'SalesDashboard-Digest' -Action $action -Trigger $trigger `
  -Description 'Business pulse digest to Telegram/Email' -RunLevel Highest
```

Логи — `logs\digest.log`. Каждый запуск также перезаписывает `deploy\last_digest.txt`.

> Примечание: скрипт обращается к `http://localhost:5000` — сервер дашборда должен быть запущен
> (он и так работает как задача `SalesDashboard-Server`). Базовый URL меняется через `DIGEST_BASE_URL`.
