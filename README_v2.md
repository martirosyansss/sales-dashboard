# 🚀 Sales Dashboard v2.0

**Аналитическая панель продаж для AS-Sales Management 7**

READ-ONLY платформа для анализа продаж с красивыми графиками и интерактивными дашбордами.

---

## 📊 Возможности

✅ **Dashboard** - Главная панель со статистикой
✅ **Менеджеры** - Анализ работы 19 продавцов  
✅ **Дистрибьюторы** - 16 групп клиентов
✅ **Территории** - 12 регионов продаж

---

## 🚦 Запуск

```powershell
cd "C:\Sales Dashboard"
C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe app_v2.py
```

Откройте: **http://localhost:5000**

---

## 🗄️ База данных

- **Сервер:** localhost
- **БД:** SalesManagement  
- **Логин:** sa / Aa123456
- **Режим:** ⚠️ **READ-ONLY** (ТОЛЬКО ЧТЕНИЕ!)

### ⚠️ ВАЖНО:
```
❌ НЕ ИЗМЕНЯТЬ структуру БД!
❌ НЕ СОЗДАВАТЬ новые таблицы!
❌ НЕ ИСПОЛЬЗОВАТЬ INSERT/UPDATE/DELETE!
✅ ТОЛЬКО SELECT запросы!
```

**Почему:** База данных - это PRODUCTION система **AS-Sales Management 7**. Любые изменения могут сломать основную ERP систему компании!

### Таблицы:
- **SALESAGENTS** - 19 менеджеров (УЖЕ СУЩЕСТВУЕТ)
- **CUSTOMERS** - 1,809 клиентов (УЖЕ СУЩЕСТВУЕТ)
- **SALES** - 388,547 продаж (УЖЕ СУЩЕСТВУЕТ)

---

## 📡 API Endpoints

```
GET /                              - Dashboard
GET /managers                      - Менеджеры
GET /groups                        - Дистрибьюторы  
GET /areas                         - Территории

GET /api/dashboard/stats           - Статистика
GET /api/managers                  - Список менеджеров
GET /api/managers/<id>             - Детали менеджера
GET /api/groups                    - Группы
GET /api/areas                     - Территории

GET /test-db                       - Тест БД
```

---

## 🛠️ Стек технологий

**Backend:** Python 3.12 + Flask 3.0 + pyodbc  
**Frontend:** Bootstrap 5.3 + Chart.js 4.4 + Alpine.js 3.x + HTMX 1.9  
**Drag & Drop:** SortableJS 1.15  
**Иконки:** Font Awesome 6.4

---

## ✅ Реализовано

- ✅ Подключение к реальной БД AS-Sales Management
- ✅ Dashboard с реальными данными
- ✅ Страницы: Менеджеры, Дистрибьюторы, Территории
- ✅ Графики Chart.js (линейные, столбчатые, круговые)
- ✅ Drag & Drop карточек
- ✅ Темная тема
- ✅ Адаптивный дизайн
- ✅ READ-ONLY mode

---

## ⏳ В планах

- ⏳ Экспорт в Excel/PDF
- ⏳ Фильтры по датам
- ⏳ Светлая тема
- ⏳ Сравнение периодов

---

**v2.0 | 13.11.2025 | GitHub Copilot**
