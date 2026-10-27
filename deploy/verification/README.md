# Проверки релиза Araqich

Проверки используют синтетические заказы, временные SQLite и подменённые источники данных. Рабочие базы и ERP для них не нужны. Отдельные процессы нужны потому, что локальные стенды подменяют модули и часы на время проверки.

Из корня репозитория в PowerShell:

```powershell
$env:DB_USER = 'isolated_test'
$env:DB_PASSWORD = 'synthetic-test-only'
$env:DB_SERVER = '127.0.0.1,1'
$env:FLASK_SECRET_KEY = 'isolated-release-test-key-never-use-in-production'
$env:ROUTES_LEARNING_NIGHTLY = '0'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -q
python -m pytest deploy/verification/refuels/test_refuels.py -q
python -m pytest deploy/verification/swap/test_swap.py -q
python deploy/verification/check_default_day.py
```

`refuels` проверяет корректировки, фильтры, итоговые суммы, права доступа, фотографии и отсутствие записи. `swap` проверяет обмен рейсов, ограничения, подтверждение загруженных рейсов, отмену и конфликт ревизий. `check_default_day.py` проверяет границу 16:00 в Ереване и сохранение явно выбранной даты.

Стенды `harness.py` предназначены только для локальных тестов; приложение их не импортирует и серверный пакет их не включает.
