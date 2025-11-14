# ⚠️ ВАЖНЫЕ ПРЕДУПРЕЖДЕНИЯ

## 🚫 НИ В КОЕМ СЛУЧАЕ НЕЛЬЗЯ:

### 1. ❌ ИЗМЕНЯТЬ БАЗУ ДАННЫХ
```
❌ НЕ создавать новые таблицы
❌ НЕ изменять существующие таблицы  
❌ НЕ удалять таблицы
❌ НЕ добавлять колонки
❌ НЕ изменять структуру
```

**ПРИЧИНА:** База данных **AS-Sales Management 7** - это **PRODUCTION** система с реальными данными компании. Любые изменения могут сломать работу основной ERP системы!

### 2. ❌ МОДИФИЦИРОВАТЬ ДАННЫЕ
```sql
❌ INSERT - НЕ добавлять записи
❌ UPDATE - НЕ изменять записи  
❌ DELETE - НЕ удалять записи
```

**РАЗРЕШЕНО ТОЛЬКО:**
```sql
✅ SELECT - Чтение данных
```

---

## ✅ ЧТО МОЖНО:

### Разрешённые операции:
```sql
✅ SELECT * FROM SALESAGENTS
✅ SELECT * FROM CUSTOMERS  
✅ SELECT * FROM SALES
✅ JOIN таблицы для анализа
✅ GROUP BY для статистики
✅ COUNT, SUM, AVG для агрегации
```

---

## 🗄️ СУЩЕСТВУЮЩАЯ СТРУКТУРА БД

### База данных уже существует:
- **Название:** SalesManagement
- **Система:** AS-Sales Management 7 (Armenian Software)
- **Таблиц:** 87 (production ERP)
- **Режим работы:** READ-ONLY для нашего Dashboard

### Основные таблицы (УЖЕ ЕСТЬ):

#### SALESAGENTS (19 записей)
```
Менеджеры по продажам
Колонки: fID, fCODE, fNAME, fUSERID, fCLOSED, etc.
```

#### CUSTOMERS (1,809 записей)
```
Клиенты
Колонки: fID, fCODE, fNAME, fGROUP, fREGION, fADDRESS, fPHONE, etc.
Группы (fGROUP): 16 групп - это дистрибьюторы
```

#### SALES (388,547 записей)
```
Продажи
Колонки: fISN, fDATE, fCUSTOMERID, fSALESAGENTID, fSALESAREA, fTOTALSUM, fSTATE, etc.
Территории (fSALESAREA): A1-A10, B1, Z9
```

---

## 📋 ПРАВИЛА РАБОТЫ

### 1. Только READ-ONLY запросы
```python
# ✅ ПРАВИЛЬНО
def get_managers():
    query = "SELECT * FROM SALESAGENTS WHERE fCLOSED = 0"
    return db.execute_query(query)

# ❌ НЕПРАВИЛЬНО - ЗАПРЕЩЕНО!
def add_manager(name):
    query = "INSERT INTO SALESAGENTS (fNAME) VALUES (?)"
    db.execute(query, (name,))  # ❌ НЕ ДЕЛАТЬ ТАК!
```

### 2. Использовать существующие поля
```python
# ✅ Менеджеры = SALESAGENTS
# ✅ Дистрибьюторы = CUSTOMERS.fGROUP  
# ✅ Территории = SALES.fSALESAREA
# ✅ Сети (networks) = специальные группы клиентов (000001, 000002, etc.)
```

### 3. Параметризованные запросы
```python
# ✅ ПРАВИЛЬНО - защита от SQL injection
query = "SELECT * FROM CUSTOMERS WHERE fGROUP = ?"
result = db.execute_query(query, (group_code,))

# ❌ НЕПРАВИЛЬНО - опасно!
query = f"SELECT * FROM CUSTOMERS WHERE fGROUP = '{group_code}'"
```

---

## 🔒 БЕЗОПАСНОСТЬ

### Подключение только для чтения:
```python
# В app_v2.py используется DatabaseConnection класс
# Все методы только для SELECT запросов
# Нет методов для INSERT/UPDATE/DELETE
```

### Логирование:
```python
# Все запросы логируются
logger.info(f"Executing SELECT query: {query}")
```

---

## 📝 ЧТО БЫЛО УДАЛЕНО

### ❌ Удалённые файлы (не актуальные):
- **doc/SQL_NEW_TABLES.sql** - УДАЛЁН
  - Причина: Содержал SQL для создания НОВЫХ таблиц
  - Но база уже существует, создавать ничего не нужно
  - Опасно: можно случайно запустить и испортить БД

---

## ✅ ПРАВИЛЬНЫЙ ПОДХОД

### Наш Dashboard:
1. ✅ Подключается к существующей БД
2. ✅ Читает данные из существующих таблиц
3. ✅ Анализирует и визуализирует данные
4. ✅ НЕ изменяет ничего в БД
5. ✅ Работает параллельно с основной ERP системой

### Структура приложения:
```
app_v2.py
├── DatabaseConnection класс
│   └── execute_query() - только SELECT
├── API endpoints (GET only)
│   ├── /api/dashboard/stats
│   ├── /api/managers
│   ├── /api/groups  
│   └── /api/areas
└── HTML templates (отображение)
```

---

## 🎯 ИТОГ

### Помните:
1. **База данных УЖЕ СУЩЕСТВУЕТ** - ничего создавать не нужно
2. **ТОЛЬКО ЧТЕНИЕ** - никаких изменений
3. **PRODUCTION система** - осторожность превыше всего
4. **Параллельная работа** - основная ERP система работает одновременно

### Если нужны изменения в БД:
❌ НЕ делайте сами  
✅ Обратитесь к администратору AS-Sales Management системы  
✅ Используйте официальный интерфейс ERP системы

---

**🚀 Sales Dashboard v2.0 - Безопасный READ-ONLY анализ данных**

*Создано: 13.11.2025*  
*Режим: READ-ONLY*  
*Статус: Production Safe ✅*
