# Анкеты, опросы и задачи

Домен обслуживает полевую активность торговых агентов помимо продаж: сбор ответов на анкеты (surveys) во время визитов в торговые точки, справочники вопросов и анкет, схемы доступа к анкетам и результатам визитов, схемы назначения исполнителей и наблюдателей задач, а также мерчандайзинговые инвентаризации представленности товара на полке (наличие, количество, собственная и конкурентная цена). Данные наполняются мобильными приложениями van-sales и в дашборде (`app_v2.py`) не используются — это самостоятельный операционный контур ERP AS-Sales Management 7.

> Примечание. В `app_v2.py` нет ни одного обращения к таблицам этого домена (проверено Grep) — бизнес-логика ниже восстановлена из схемы (`docs/database/schema/tables/*.json`), первичных ключей, индексов и реальных примеров данных (`samples`). Смыслы, которые не выводятся однозначно из имени/данных, помечены как «назначение не установлено».

---

## dbo.QUESTIONS  (3 строки)

- Назначение: справочник отдельных вопросов, из которых собираются анкеты; хранит формулировку, тип ответа и параметры прикрепления фотографий.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Суррогатный числовой ключ вопроса (PK, кластерный) |
| fCODE | nvarchar(6) | нет | Уникальный код вопроса (уникальный индекс I_QUESTIONS1) |
| fNAME | nvarchar(255) | нет | Текст (формулировка) вопроса |
| fDESCRIPTION | nvarchar(1000) | нет | Описание/пояснение к вопросу |
| fGROUP | nvarchar(3) | нет | Код группы вопросов (справочник группировки; в примерах пусто) |
| fANSWERTYPE | nvarchar(1) | нет | Код типа ответа (в данных «3», «4»; конкретные коды типов не установлены) |
| fTREECODE | nvarchar(8) | да | Код узла дерева-справочника для ответов со списком значений (fISN/код TREES/TREEDEF); назначение точного дерева не установлено |
| fIMAGESALLOWED | bit | нет | Разрешено ли прикреплять фотографии к ответу |
| fIMAGESISREQUIRED | bit | нет | Обязательно ли прикреплять фотографию |
| fCLOSED | bit | нет | Признак закрытия/архивирования вопроса (не активен) |
| fBODY | nvarchar(3000) | да | Дополнительное тело/параметры вопроса (в примерах NULL); назначение не установлено |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) для контроля конкурентного изменения |
| fHASFRACTION | bit | да | Допускается ли дробное числовое значение в ответе |

- Ключи и связи: PK — `fID`; альтернативный уникальный ключ — `fCODE`. На вопрос ссылаются `QUESTIONNAIREDETAILS.fQUESTIONID → QUESTIONS.fID` и `SURVEYDETAILS.fQUESTIONID → QUESTIONS.fID`. `fTREECODE` — неявная ссылка на справочник TREES/TREEDEF.

---

## dbo.QUESTIONNAIRES  (1 строка)

- Назначение: справочник (шапка) анкет — именованных наборов вопросов, предъявляемых агенту во время визита/опроса.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(6) | нет | Код анкеты (PK, кластерный) |
| fNAME | nvarchar(150) | нет | Наименование анкеты |
| fCLOSED | bit | нет | Признак закрытия/архивирования анкеты |
| fBODY | nvarchar(3000) | да | Дополнительное тело/параметры анкеты (в примере NULL); назначение не установлено |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — `fCODE`. Состав анкеты — в `QUESTIONNAIREDETAILS.fQUESTIONNAIRECODE → QUESTIONNAIRES.fCODE`. Доступность анкеты регулируется через `QUESTIONNAIRESACCESSSCHEMEDETAILS.fQUESTIONNAIRE`.

---

## dbo.QUESTIONNAIREDETAILS  (3 строки)

- Назначение: строки состава анкеты — привязка вопросов к анкете, их порядок и обязательность заполнения.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fQUESTIONNAIRECODE | nvarchar(6) | нет | Код анкеты-владельца (часть PK) → QUESTIONNAIRES.fCODE |
| fISREQUIRED | bit | нет | Обязателен ли вопрос в данной анкете |
| fROWNUM | smallint | нет | Порядковый номер вопроса в анкете (сортировка, с 0) |
| fQUESTIONID | int | нет | Ссылка на вопрос (часть PK) → QUESTIONS.fID |

- Ключи и связи: PK — (`fQUESTIONNAIRECODE`, `fQUESTIONID`). Связи: `fQUESTIONNAIRECODE → QUESTIONNAIRES.fCODE`, `fQUESTIONID → QUESTIONS.fID`.

---

## dbo.SURVEYDETAILS  (3 строки)

- Назначение: строки результатов опроса — фактические ответы на вопросы анкеты, зафиксированные в конкретном документе визита/опроса; хранит текст ответа и заметки.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ документа-шапки опроса/визита (часть PK); ссылается на шапку опроса (в текущем наборе схемы отдельная таблица-шапка SURVEYS/VISITS отсутствует) |
| fANSWER | nvarchar(4000) | нет | Значение ответа (текст, «true»/«false» для булевых, число и т.п. — в зависимости от fANSWERTYPE вопроса) |
| fROWNUM | smallint | нет | Порядковый номер строки ответа в документе (с 0) |
| fNOTES | nvarchar(1000) | да | Дополнительные заметки к ответу |
| fQUESTIONID | int | нет | Ссылка на вопрос (часть PK) → QUESTIONS.fID |

- Ключи и связи: PK — (`fISN`, `fQUESTIONID`). Связи: `fQUESTIONID → QUESTIONS.fID`; `fISN` → шапка документа опроса/визита (по соглашению fISN — суррогатный ключ документа).

---

## dbo.QUESTIONNAIRESACCESSSCHEMES  (1 строка)

- Назначение: справочник схем доступа к анкетам — именованные наборы правил, определяющие, какие анкеты доступны (например, схема «Բոլոր» — «Все»).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код схемы доступа (PK, кластерный) |
| fNAME | nvarchar(50) | нет | Наименование схемы доступа |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — `fCODE`. Детализация правил — в `QUESTIONNAIRESACCESSSCHEMEDETAILS.fSCHEMECODE → QUESTIONNAIRESACCESSSCHEMES.fCODE`.

---

## dbo.QUESTIONNAIRESACCESSSCHEMEDETAILS  (1 строка)

- Назначение: строки схемы доступа к анкетам — правила разрешения/запрета доступа к конкретной анкете либо ко всем анкетам через метод отбора.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Код схемы-владельца (часть PK) → QUESTIONNAIRESACCESSSCHEMES.fCODE |
| fQUESTIONNAIREMETHOD | varchar(1) | нет | Метод отбора анкет для правила (в примере «0» при пустой анкете — трактуется как «все»; коды методов не установлены) |
| fQUESTIONNAIRE | nvarchar(6) | да | Код конкретной анкеты (часть PK) → QUESTIONNAIRES.fCODE; NULL при обобщённом правиле |
| fACCESS | bit | нет | Разрешён (1) или запрещён (0) доступ по правилу |
| fROWNUM | smallint | нет | Порядковый номер строки правила (с 0) |

- Ключи и связи: PK — (`fSCHEMECODE`, `fQUESTIONNAIREMETHOD`, `fQUESTIONNAIRE`). Связи: `fSCHEMECODE → QUESTIONNAIRESACCESSSCHEMES.fCODE`, `fQUESTIONNAIRE → QUESTIONNAIRES.fCODE`.

---

## dbo.VISITRESULTSACCESSSCHEMES  (1 строка)

- Назначение: справочник схем доступа к результатам визитов — именованные наборы правил видимости результатов визитов (например, «Բոլորը» — «Все»).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код схемы доступа (PK, кластерный) |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — `fCODE`. Детализация — в `VISITRESULTSACCESSSCHEMEDETAILS.fSCHEMECODE → VISITRESULTSACCESSSCHEMES.fCODE`.

---

## dbo.VISITRESULTSACCESSSCHEMEDETAILS  (1 строка)

- Назначение: строки схемы доступа к результатам визитов — правила разрешения/запрета доступа к конкретному типу результата визита либо ко всем.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Код схемы-владельца (часть PK) → VISITRESULTSACCESSSCHEMES.fCODE |
| fVISITRESULTMETHOD | varchar(1) | нет | Метод отбора результатов визита для правила (в примере «0» — обобщённо «все»; коды не установлены) |
| fVISITRESULT | nvarchar(6) | да | Код конкретного результата/типа визита (часть PK); NULL при обобщённом правиле; назначение справочника не установлено |
| fACCESS | bit | нет | Разрешён (1) или запрещён (0) доступ по правилу |
| fROWNUM | smallint | нет | Порядковый номер строки правила (с 0) |

- Ключи и связи: PK — (`fSCHEMECODE`, `fVISITRESULTMETHOD`, `fVISITRESULT`). Связь: `fSCHEMECODE → VISITRESULTSACCESSSCHEMES.fCODE`.

---

## dbo.TASKFOLLOWERSSCHEMES  (0 строк)

- Назначение: справочник схем наблюдателей/исполнителей задач — именованные наборы правил, определяющих, кто назначается на задачи. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код схемы (PK, кластерный) |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — `fCODE`. Детализация — в `TASKFOLLOWERSSCHEMEDETAILS.fSCHEMECODE → TASKFOLLOWERSSCHEMES.fCODE`.

---

## dbo.TASKFOLLOWERSSCHEMEDETAILS  (0 строк)

- Назначение: строки схемы исполнителей задач — сопоставление типа задачи и исполнителя с разрешением доступа. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Код схемы-владельца (часть PK) → TASKFOLLOWERSSCHEMES.fCODE |
| fTASKTYPEMETHOD | varchar(1) | нет | Метод отбора типов задач (коды не установлены) |
| fTASKTYPE | nvarchar(6) | да | Код типа задачи (часть PK); NULL при обобщённом правиле; справочник типов не установлен |
| fEXECUTORMETHOD | varchar(1) | да | Метод отбора исполнителей (коды не установлены) |
| fEXECUTORID | int | да | Идентификатор исполнителя (часть PK); вероятно → SALESAGENTS.fID, точная целевая таблица не установлена |
| fACCESS | bit | нет | Разрешён (1) или запрещён (0) доступ по правилу |
| fROWNUM | smallint | нет | Порядковый номер строки правила (с 0) |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — (`fSCHEMECODE`, `fTASKTYPEMETHOD`, `fTASKTYPE`, `fEXECUTORMETHOD`, `fEXECUTORID`). Связь: `fSCHEMECODE → TASKFOLLOWERSSCHEMES.fCODE`.

---

## dbo.PRODUCTSINVENTORIES  (4 строки)

- Назначение: шапка документа мерчандайзинговой инвентаризации в торговой точке — фиксация факта проверки представленности товара агентом (дата, клиент, агент, территория, тип планограммы).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ документа (уникальный индекс I_PRODUCTSINVENTORIES1); на него ссылаются строки |
| fDATE | smalldatetime | нет | Дата инвентаризации/визита |
| fDOCNUM | nvarchar(12) | нет | Номер документа (PK, кластерный) |
| fCUSTOMERID | int | нет | Клиент/торговая точка → CUSTOMERS.fID |
| fSALESAGENTID | int | нет | Торговый агент, выполнивший инвентаризацию → SALESAGENTS.fID |
| fCOMMENT | nvarchar(255) | нет | Комментарий к документу |
| fSALESAREA | nvarchar(6) | нет | Код территории продаж (справочник TREES/TREEDEF) |
| fCREATIONTYPEID | uniqueidentifier | да | Идентификатор типа/источника создания документа (индекс I_PRODUCTSINVENTORIES3); назначение справочника не установлено |
| fSTATE | tinyint | да | Состояние документа (в примерах «2»; конкретные коды состояний не установлены) |
| fCREATEMETHOD | varchar(1) | нет | Метод создания (в примерах «1»; коды не установлены) |
| fDOCGROUP | nvarchar(3) | нет | Код группы документа (в примерах пусто) |
| fCONTACT | nvarchar(50) | нет | Контактное лицо в точке |
| fPLANOGRAMTYPE | nvarchar(3) | нет | Код типа планограммы (в примерах пусто); справочник не установлен |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус выгрузки во внешнюю систему (в примерах пусто) |

- Ключи и связи: PK — `fDOCNUM`; логический ключ документа — `fISN`. Строки — `PRODUCTSINVENTORYDETAILS.fISN → PRODUCTSINVENTORIES.fISN`. Неявные связи: `fCUSTOMERID → CUSTOMERS.fID`, `fSALESAGENTID → SALESAGENTS.fID`, `fSALESAREA` → TREES/TREEDEF.

---

## dbo.PRODUCTSINVENTORYDETAILS  (0 строк)

- Назначение: строки инвентаризации — по каждому товару фиксируется наличие на полке, количество, отпускная и конкурентная цена. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ документа-шапки (PK, кластерный) → PRODUCTSINVENTORIES.fISN |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID (индекс I_PRODUCTSINVENTORYDETAILS1) |
| fAVAILABLE | bit | нет | Наличие товара на полке (представлен/нет) |
| fQUANTITY | money | нет | Зафиксированное количество на полке |
| fPRICE | money | нет | Цена товара в точке (наша/отпускная) |
| fCOMPETITIVEPRICE | money | нет | Цена конкурента на аналогичный товар |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация по позиции |
| fROWNUM | smallint | нет | Порядковый номер строки в документе (с 0) |

- Ключи и связи: PK (кластерный, неуникальный) — `fISN`. Связи: `fISN → PRODUCTSINVENTORIES.fISN`, `fPRODUCTID → PRODUCTS.fID`.

---

## Связи домена

Внутри домена выделяются четыре независимых контура, объединённых общей темой полевой активности агента:

1. Анкеты и опросы (шапка → строки → результаты):
   - `QUESTIONS` (вопросы) ← `QUESTIONNAIREDETAILS.fQUESTIONID` — состав анкеты; `QUESTIONNAIREDETAILS.fQUESTIONNAIRECODE → QUESTIONNAIRES.fCODE`.
   - Фактические ответы: `SURVEYDETAILS.fQUESTIONID → QUESTIONS.fID`; строки группируются по `SURVEYDETAILS.fISN` (документ опроса/визита; отдельная таблица-шапка в текущем наборе схемы отсутствует).

2. Схемы доступа к анкетам: `QUESTIONNAIRESACCESSSCHEMES.fCODE` ← `QUESTIONNAIRESACCESSSCHEMEDETAILS.fSCHEMECODE`; правило может указывать на анкету через `fQUESTIONNAIRE → QUESTIONNAIRES.fCODE`.

3. Схемы доступа к результатам визитов: `VISITRESULTSACCESSSCHEMES.fCODE` ← `VISITRESULTSACCESSSCHEMEDETAILS.fSCHEMECODE`.

4. Схемы исполнителей/наблюдателей задач: `TASKFOLLOWERSSCHEMES.fCODE` ← `TASKFOLLOWERSSCHEMEDETAILS.fSCHEMECODE`; `fEXECUTORID` предположительно → SALESAGENTS.fID.

5. Мерчандайзинговые инвентаризации: `PRODUCTSINVENTORIES.fISN` ← `PRODUCTSINVENTORYDETAILS.fISN`; строки ссылаются на `PRODUCTS.fID`.

Связи с соседними доменами (все неявные, по соглашению об именовании; явных FK в БД нет):
- Клиенты (домен customers): `PRODUCTSINVENTORIES.fCUSTOMERID → CUSTOMERS.fID`.
- Агенты (домен agents): `PRODUCTSINVENTORIES.fSALESAGENTID → SALESAGENTS.fID`; `TASKFOLLOWERSSCHEMEDETAILS.fEXECUTORID` (предположительно SALESAGENTS).
- Товары (домен products): `PRODUCTSINVENTORYDETAILS.fPRODUCTID → PRODUCTS.fID`.
- Справочники-деревья: `PRODUCTSINVENTORIES.fSALESAREA` и `QUESTIONS.fTREECODE` → TREES/TREEDEF.

---

## Примеры отчётных запросов

Все запросы — только чтение (SELECT), по реально существующим колонкам.

1. Состав анкеты с вопросами, порядком и обязательностью:

```sql
SELECT  qn.fCODE           AS AnketaCode,
        qn.fNAME           AS AnketaName,
        qd.fROWNUM         AS RowNum,
        qd.fISREQUIRED     AS IsRequired,
        q.fCODE            AS QuestionCode,
        q.fNAME            AS QuestionText,
        q.fANSWERTYPE      AS AnswerType
FROM        dbo.QUESTIONNAIRES        qn
JOIN        dbo.QUESTIONNAIREDETAILS  qd ON qd.fQUESTIONNAIRECODE = qn.fCODE
JOIN        dbo.QUESTIONS             q  ON q.fID = qd.fQUESTIONID
WHERE       qn.fCLOSED = 0
ORDER BY    qn.fCODE, qd.fROWNUM;
```

2. Ответы конкретного опроса (по документу fISN) с текстом вопросов:

```sql
SELECT  sd.fISN            AS SurveyISN,
        sd.fROWNUM         AS RowNum,
        q.fNAME            AS QuestionText,
        sd.fANSWER         AS Answer,
        sd.fNOTES          AS Notes
FROM        dbo.SURVEYDETAILS sd
JOIN        dbo.QUESTIONS     q ON q.fID = sd.fQUESTIONID
ORDER BY    sd.fISN, sd.fROWNUM;
```

3. Реестр мерчандайзинговых инвентаризаций по клиентам и агентам за период:

```sql
SELECT  pi.fDOCNUM         AS DocNum,
        pi.fDATE           AS InventoryDate,
        pi.fCUSTOMERID     AS CustomerId,
        c.fNAME            AS CustomerName,
        pi.fSALESAGENTID   AS AgentId,
        pi.fSALESAREA      AS SalesArea,
        pi.fSTATE          AS State
FROM        dbo.PRODUCTSINVENTORIES pi
LEFT JOIN   dbo.CUSTOMERS           c ON c.fID = pi.fCUSTOMERID
WHERE       pi.fDATE >= '2022-01-01' AND pi.fDATE < '2023-01-01'
ORDER BY    pi.fDATE, pi.fDOCNUM;
```

4. Позиции инвентаризации со сравнением своей и конкурентной цены (наличие на полке):

```sql
SELECT  pi.fDOCNUM             AS DocNum,
        pi.fDATE               AS InventoryDate,
        pid.fPRODUCTID         AS ProductId,
        p.fNAME                AS ProductName,
        pid.fAVAILABLE         AS OnShelf,
        pid.fQUANTITY          AS Qty,
        pid.fPRICE             AS OurPrice,
        pid.fCOMPETITIVEPRICE  AS CompetitorPrice
FROM        dbo.PRODUCTSINVENTORYDETAILS pid
JOIN        dbo.PRODUCTSINVENTORIES      pi ON pi.fISN = pid.fISN
LEFT JOIN   dbo.PRODUCTS                 p  ON p.fID = pid.fPRODUCTID
ORDER BY    pi.fDOCNUM, pid.fROWNUM;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
