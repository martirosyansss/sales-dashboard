# Агенты, территории и оргструктура

Домен описывает торговых агентов (менеджеров/ван-агентов) системы AS-Sales Management 7 и их привязки к элементам оргструктуры: территориям продаж (Sales Areas), дивизионам, ван-агентам и товарному доступу. Оргструктура и справочники (области, дивизионы, товарные и клиентские группы) хранятся в единой иерархической таблице-дереве `TREES`, дискриминируемой по колонке `fTREEID`. Явных внешних ключей в базе почти нет — связи неявные, по кодам и суррогатным идентификаторам. В аналитическом приложении (`app_v2.py`) этот домен используется для расчёта показателей по менеджерам и по территориям, а также для перевода кодов территорий/групп в человекочитаемые названия.

## dbo.SALESAGENTS  (181 строк)

- Назначение: справочник торговых агентов (менеджеров и ван-агентов). Строка описывает одного агента: код, ФИО, признак закрытия и связку с пользователем/устройством мобильного приложения.
- В коде: `app_v2.py` джойнит `SALES.fSALESAGENTID → SALESAGENTS.fID`, фильтрует активных через `sa.fCLOSED = 0`, отдаёт по менеджеру `CustomerCount`, `SalesCount`, `SUM(fTOTALSUM)`, `AVG(fTOTALSUM)`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Суррогатный ключ агента (PK, кластерный). Цель ссылок `fSALESAGENTID`/`fVANAGENTID` из других таблиц |
| fCODE | nvarchar(12) | нет | Код агента (уникальный, напр. `B001`). Показывается как ManagerCode |
| fNAME | nvarchar(50) | нет | ФИО агента |
| fEXTERNALCODE | nvarchar(20) | нет | Внешний код (для интеграций) |
| fCLOSED | bit | нет | Признак закрытия/архивности агента (0 — активен) |
| fUSERID | int | да | Ссылка на пользователя системы (уникальный индекс) |
| fDEVICEID | uniqueidentifier | да | Идентификатор мобильного устройства агента |
| fPASSPORTID | nvarchar(20) | нет | Паспортные данные агента |
| fISFOREIGN | bit | нет | Признак «иностранный»/внешний агент |
| fISN | uniqueidentifier | нет | Суррогатный ISN-ключ записи (уникальный индекс) |
| fBODY | nvarchar(3000) | да | Сериализованное тело записи (внутренний формат ERP) |
| fTS | timestamp | нет | Версия строки (rowversion) для контроля конкуренции |
| fFOLLOWERSCHEME | nvarchar(3) | нет | Схема подчинения/наблюдателей |
| fQUESTIONNAIRESACCESSSCHEME | nvarchar(3) | нет | Схема доступа к анкетам |
| fVISITRESULTSACCESSSCHEME | nvarchar(3) | нет | Схема доступа к результатам визитов |
| fREMCHECKMODE | varchar(1) | нет | Режим контроля остатков |
| fEXTERNALBODY | nvarchar(max) | да | Внешнее тело записи (для интеграций) |
| fRESERVATIONSCHEME | nvarchar(3) | нет | Схема резервирования товара |

- Ключи и связи: PK `fID`. Неявные связи: `SALES.fSALESAGENTID → fID`, `DOCUMENTS.fSALESAGENTID → fID`, `SALESAGENTAREAS.fSALESAGENTID → fID`, `SALESAGENTDIVISIONS.fSALESAGENTID → fID`, `SALESAGENTVANAGENTSACCESS.fSALESAGENTID → fID` и `fVANAGENTID → fID` (ван-агент — тот же справочник агентов).

## dbo.SALESAGENTAREAS  (205 строк)

- Назначение: связка «многие-ко-многим» между агентом и территориями продаж (Sales Areas), к которым он привязан; отмечает территорию по умолчанию.
- В коде: используется для сборки списка территорий каждого менеджера; название территории берётся из `TREES` по `fTREEID='SArea'`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fSALESAREA | nvarchar(6) | нет | Код территории продаж → `TREES.fCODE` (при `fTREEID='SArea'`) |
| fDEFAULT | bit | нет | Признак территории по умолчанию для агента |
| fROWNUM | smallint | нет | Порядковый номер строки (сортировка) |

- Ключи и связи: PK (`fSALESAGENTID`, `fSALESAREA`), кластерный. Неявные связи: `fSALESAGENTID → SALESAGENTS.fID`; `fSALESAREA → TREES.fCODE` (`fTREEID='SArea'`); тот же код территории встречается в `CUSTOMERSALESAREAS.fSALESAREA` и `SALES.fSALESAREA`.

## dbo.SALESAGENTDIVISIONS  (787 строк)

- Назначение: связка «многие-ко-многим» между агентом и дивизионами (подразделениями оргструктуры); отмечает дивизион по умолчанию.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fDIVISION | nvarchar(6) | нет | Код дивизиона → `TREES.fCODE` (при `fTREEID='Division'`) |
| fDEFAULT | bit | нет | Признак дивизиона по умолчанию |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK (`fSALESAGENTID`, `fDIVISION`), кластерный. Неявные связи: `fSALESAGENTID → SALESAGENTS.fID`; `fDIVISION → TREES.fCODE` (`fTREEID='Division'`).

## dbo.SALESAGENTVANAGENTSACCESS  (298 строк)

- Назначение: доступ торгового агента к ван-агентам (мобильным точкам продаж/машинам). Задаёт, с какими ван-агентами может работать данный агент, и какой из них по умолчанию.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fVANAGENTID | int | нет | Ссылка на ван-агента → `SALESAGENTS.fID` (тот же справочник) |
| fDEFAULT | bit | нет | Ван-агент по умолчанию |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK (`fSALESAGENTID`, `fVANAGENTID`), кластерный. Обе колонки-идентификатора ссылаются на `SALESAGENTS.fID` (агент ↔ ван-агент).

## dbo.SALESAGENTPRODUCTSACCESS  (846 строк)

- Назначение: правила товарного доступа агента. Строка разрешает/запрещает (`fACCESS`) агенту работать с товаром или товарной группой; тип строки задаётся `fPRODUCTTYPE` (напр. `0` — общее правило, `1` — по группе товаров).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fPRODUCTTYPE | varchar(1) | нет | Тип правила доступа (`0` — общее, `1` — по товарной группе, и т.п.) |
| fPRODUCTID | int | да | Ссылка на конкретный товар → `PRODUCTS.fID` (NULL, если правило групповое) |
| fPRODUCTGROUP | nvarchar(6) | да | Код товарной группы → `TREES.fCODE` (напр. `fTREEID='ProdGrp'`); NULL для общих правил |
| fACCESS | bit | нет | Разрешён (1) или запрещён (0) доступ |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK (`fSALESAGENTID`, `fPRODUCTTYPE`, `fPRODUCTID`, `fPRODUCTGROUP`), кластерный. Неявные связи: `fSALESAGENTID → SALESAGENTS.fID`; `fPRODUCTID → PRODUCTS.fID`; `fPRODUCTGROUP → TREES.fCODE` (дерево товарных групп).

## dbo.SALESAGENTPREFEREDPRODUCTS  (1155 строк)

- Назначение: список предпочитаемых (приоритетных) товаров агента с заданным порядком отображения/предложения клиенту.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fPRODUCTID | int | нет | Ссылка на товар → `PRODUCTS.fID` |
| fORDER | smallint | нет | Порядок приоритета товара для агента |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK (`fSALESAGENTID`, `fPRODUCTID`), кластерный. Неявные связи: `fSALESAGENTID → SALESAGENTS.fID`; `fPRODUCTID → PRODUCTS.fID`.

## dbo.ACCESSIBLESALESAREAS  (0 строк)

- Назначение: доступные агенту территории продаж (кэш/материализация доступа). В боевой БД таблица пуста — фактический список территорий агента ведётся в `SALESAGENTAREAS`. Задокументирована по схеме, в аналитике не используется.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Ссылка на агента → `SALESAGENTS.fID` |
| fSALESAREA | nvarchar(6) | нет | Код территории продаж → `TREES.fCODE` (`fTREEID='SArea'`) |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK (`fSALESAGENTID`, `fSALESAREA`), кластерный. Неявные связи аналогичны `SALESAGENTAREAS`. Таблица пуста (0 строк).

## dbo.TREES  (265 строк)

- Назначение: универсальный справочник-дерево оргструктуры и классификаторов. Разные логические справочники хранятся в одной таблице и различаются по `fTREEID` (напр. `SArea` — территории продаж, `Division` — дивизионы, `CustGrp` — группы клиентов, `AssetGrp`, `CsDscGrp` и др.). Иерархия строится через `fPARENT`/`fPATH`.
- В коде: основной источник названий территорий и групп. Типовой джойн: `TREES t ON t.fCODE = <код> AND t.fTREEID = 'SArea'` (или `'Division'`, `'CustGrp'`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fTREEID | nvarchar(8) | нет | Дискриминатор дерева/справочника (`SArea`, `Division`, `CustGrp`, …) |
| fCODE | nvarchar(20) | нет | Код элемента в пределах дерева (значение, на которое ссылаются `fSALESAREA`, `fDIVISION`, `fGROUP` и т.п.) |
| fCAPTION | nvarchar(150) | нет | Отображаемое название элемента |
| fPATH | nvarchar(255) | нет | Материализованный путь в дереве (напр. `001!`) |
| fPARENT | nvarchar(20) | нет | Код родительского элемента (пусто у корня) |
| fLEAF | bit | нет | Признак листа дерева (нет потомков) |
| fCLOSED | bit | нет | Признак закрытия/архивности элемента |
| fISN | uniqueidentifier | нет | Суррогатный ISN-ключ элемента |
| fDOCBASEISN | uniqueidentifier | да | Ссылка на ISN базового документа/объекта (при наличии) |
| fBODY | nvarchar(3000) | да | Сериализованное тело записи (внутренний формат ERP) |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fSPEC | nvarchar(255) | нет | Спецификация/доп. атрибуты элемента |

- Ключи и связи: уникальный кластерный индекс (`fTREEID`, `fCODE`) — фактический составной ключ. Внутренняя иерархия: `fPARENT → fCODE` в пределах одного `fTREEID`. Неявные связи наружу: `fCODE` (`SArea`) ← `SALESAGENTAREAS.fSALESAREA`, `CUSTOMERSALESAREAS.fSALESAREA`, `SALES.fSALESAREA`, `DOCUMENTS.fSALESAREA`; `fCODE` (`Division`) ← `SALESAGENTDIVISIONS.fDIVISION`; `fCODE` (`CustGrp`) ← `CUSTOMERS.fGROUP`.

## dbo.TREESLOG  (52 строк)

- Назначение: журнал изменений элементов справочника `TREES`. Фиксирует, кто, когда, с какого компьютера и какую операцию выполнил над узлом дерева.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fTREEID | nvarchar(8) | нет | Дерево изменённого элемента → `TREES.fTREEID` |
| fCODE | nvarchar(20) | нет | Код изменённого элемента → `TREES.fCODE` |
| fDATE | datetime | нет | Дата/время операции (default `getdate()`) |
| fUSERID | int | нет | Пользователь, выполнивший операцию |
| fOP | tinyint | нет | Код операции (напр. вставка/изменение/удаление) |
| fCOMMENT | nvarchar(255) | да | Комментарий к изменению |
| fCOMPNAME | nvarchar(32) | нет | Имя компьютера-источника изменения |
| fSOURCE | smallint | нет | Код источника изменения |

- Ключи и связи: PK (`fTREEID`, `fCODE`, `fDATE`, `fOP`), кластерный. Неявная связь: (`fTREEID`, `fCODE`) → `TREES` (изменённый элемент).

## Связи домена

- **Агент — центр домена.** `SALESAGENTS.fID` — точка привязки для всех таблиц назначений: территории (`SALESAGENTAREAS`), дивизионы (`SALESAGENTDIVISIONS`), ван-агенты (`SALESAGENTVANAGENTSACCESS`), товарный доступ (`SALESAGENTPRODUCTSACCESS`) и предпочитаемые товары (`SALESAGENTPREFEREDPRODUCTS`).
- **Ван-агент — это тоже агент.** В `SALESAGENTVANAGENTSACCESS` обе колонки (`fSALESAGENTID`, `fVANAGENTID`) ссылаются на `SALESAGENTS.fID`.
- **Справочники через TREES.** Коды `fSALESAREA`, `fDIVISION`, `fPRODUCTGROUP` — это `TREES.fCODE` при соответствующем `fTREEID` (`SArea`, `Division`, товарное дерево). Иерархия внутри дерева — через `fPARENT`.
- **Связь с соседними доменами.** Через территорию (`SArea`) домен смыкается с клиентами и продажами: `CUSTOMERSALESAREAS.fSALESAREA`, `SALES.fSALESAREA`, `DOCUMENTS.fSALESAREA` используют те же коды территорий. Через агента — с документами продаж: `SALES.fSALESAGENTID`, `DOCUMENTS.fSALESAGENTID → SALESAGENTS.fID`. Товарные привязки смыкаются с доменом товаров (`PRODUCTS.fID`).
- **Аудит.** `TREESLOG` протоколирует изменения `TREES` по составному ключу (`fTREEID`, `fCODE`).

## Примеры отчётных запросов

Все запросы — только чтение (SELECT), используют реально существующие колонки.

**1. Активные агенты со списком их территорий (с названиями).**
```sql
SELECT ag.fCODE      AS ManagerCode,
       ag.fNAME      AS ManagerName,
       aa.fSALESAREA AS AreaCode,
       t.fCAPTION    AS AreaName,
       aa.fDEFAULT   AS IsDefaultArea
FROM SALESAGENTS ag
INNER JOIN SALESAGENTAREAS aa ON aa.fSALESAGENTID = ag.fID
LEFT  JOIN TREES t ON t.fCODE = aa.fSALESAREA AND t.fTREEID = 'SArea'
WHERE ag.fCLOSED = 0
ORDER BY ag.fNAME, aa.fDEFAULT DESC, aa.fROWNUM;
```

**2. Количество территорий и дивизионов у каждого агента.**
```sql
SELECT ag.fCODE,
       ag.fNAME,
       COUNT(DISTINCT aa.fSALESAREA) AS AreasCount,
       COUNT(DISTINCT ad.fDIVISION)  AS DivisionsCount
FROM SALESAGENTS ag
LEFT JOIN SALESAGENTAREAS     aa ON aa.fSALESAGENTID = ag.fID
LEFT JOIN SALESAGENTDIVISIONS ad ON ad.fSALESAGENTID = ag.fID
WHERE ag.fCLOSED = 0
GROUP BY ag.fCODE, ag.fNAME
ORDER BY AreasCount DESC;
```

**3. Дерево территорий продаж (SArea) с родителем и признаком листа.**
```sql
SELECT t.fCODE,
       t.fCAPTION,
       t.fPARENT,
       p.fCAPTION AS ParentName,
       t.fLEAF,
       t.fCLOSED
FROM TREES t
LEFT JOIN TREES p ON p.fCODE = t.fPARENT AND p.fTREEID = t.fTREEID
WHERE t.fTREEID = 'SArea'
ORDER BY t.fPATH;
```

**4. Доступ агентов к ван-агентам (агент → ван-агент по именам).**
```sql
SELECT ag.fCODE  AS AgentCode,
       ag.fNAME  AS AgentName,
       van.fCODE AS VanAgentCode,
       van.fNAME AS VanAgentName,
       va.fDEFAULT AS IsDefaultVan
FROM SALESAGENTVANAGENTSACCESS va
INNER JOIN SALESAGENTS ag  ON ag.fID  = va.fSALESAGENTID
INNER JOIN SALESAGENTS van ON van.fID = va.fVANAGENTID
ORDER BY ag.fNAME, va.fDEFAULT DESC;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
