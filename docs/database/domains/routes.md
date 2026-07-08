# Маршруты, визиты и GPS

Домен описывает планирование и фактическое исполнение маршрутов торговых агентов в системе van-sales: шаблоны еженедельных маршрутов, планы визитов (в том числе выгруженные на мобильные устройства), фактические посещения точек продаж с результатом визита, а также сплошной GPS-трек агентов. Дополнительно домен ведёт справочник автомобилей доставки и их закрепление за агентами. Ключевые сущности связаны неявно через `fSALESAGENTID` (агент), `fCUSTOMERID` (клиент/точка) и `fISN`/`fID` (заголовок документа или шаблона); явные внешние ключи в БД почти отсутствуют.

Важные наблюдения по данным:
- `PLANNEDROUTESLIST.fISN` — это ISN документа-плана маршрута в `DOCUMENTS` с `fDOCTYPE = 10` (см. `app_v2.py`, запрос статистики маршрутов). Именно так план визитов сопоставляется с датой (`DOCUMENTS.fDATE`).
- Факт визита фиксируется в `ACTUALROUTES`; сопоставление «план vs факт» в приложении делается по паре (`fCUSTOMERID`, дата) — по совпадению определяются выполненные, а по отсутствию — пропущенные (missed) и внеплановые (unplanned) визиты.
- `AGENTLOCATIONS` — самая крупная таблица домена (~4 млн строк) — непрерывный поток координат; `ACTUALROUTES` (~460 тыс.) хранит агрегированные точки-визиты с координатами момента прихода.

---

## dbo.AGENTLOCATIONS  (4 064 596 строк)

- Назначение: сплошной GPS-трек торговых агентов — периодические замеры координат устройства во времени. Используется для восстановления траектории перемещения агента.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Идентификатор торгового агента (→ SALESAGENTS.fID) |
| fDATETIME | datetime | нет | Момент фиксации координаты |
| fLATITUDE | decimal(10,6) | нет | Широта |
| fLONGITUDE | decimal(10,6) | нет | Долгота |
| fACCURACY | money | нет | Точность позиционирования (радиус погрешности, метры) |

- Ключи и связи: кластерный индекс `PK_AGENTLOCATIONS` по (`fSALESAGENTID`, `fDATETIME`). Явного PK/FK нет. Неявная связь: `fSALESAGENTID` → `SALESAGENTS.fID`.

## dbo.ACTUALROUTES  (460 333 строк)

- Назначение: журнал фактических визитов агента к клиентам — время прихода/ухода, координаты, результат визита. Основной источник факта посещений точек продаж.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROWISN | uniqueidentifier | нет | Суррогатный ключ строки визита (уникальный) |
| fDATE | smalldatetime | нет | Дата визита (день маршрута) |
| fSALESAGENTID | int | нет | Агент, совершивший визит (→ SALESAGENTS.fID) |
| fCUSTOMERID | int | нет | Посещённый клиент/точка (→ CUSTOMERS.fID) |
| fSTARTTIME | datetime | нет | Время начала визита (прихода) |
| fENDTIME | datetime | да | Время окончания визита (ухода) |
| fLATITUDE | decimal(10,6) | да | Широта в момент визита |
| fLONGITUDE | decimal(10,6) | да | Долгота в момент визита |
| fACCURACY | money | да | Точность позиционирования (метры) |
| fVISITRESULT | nvarchar(6) | нет | Код результата визита (в samples «01»); справочник кодов результата |
| fCOMMENT | nvarchar(50) | нет | Комментарий к визиту |
| fROWNUM | smallint | нет | Порядковый номер точки в маршруте дня |
| fFORMOBILEPLANNED | bit | нет | Признак: визит был из плана, выгруженного на мобильное устройство |

- Ключи и связи: кластерный индекс `PK_ACTUALROUTES` по (`fSALESAGENTID`, `fDATE`); уникальный индекс `I_ACTUALROUTES` по `fROWISN`. Неявные связи: `fSALESAGENTID` → `SALESAGENTS.fID`, `fCUSTOMERID` → `CUSTOMERS.fID`.

## dbo.PLANNEDROUTESLIST  (1 081 449 строк)

- Назначение: строки плана маршрута (список запланированных к посещению клиентов) для конкретного документа-плана. Заголовок плана хранится в `DOCUMENTS` (`fDOCTYPE = 10`), откуда берётся дата плана.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | ISN документа-плана маршрута (→ DOCUMENTS.fISN, fDOCTYPE=10) |
| fCUSTOMERID | int | нет | Запланированный клиент/точка (→ CUSTOMERS.fID) |
| fCOMMENT | nvarchar(50) | нет | Комментарий к строке плана |
| fROWNUM | smallint | нет | Порядковый номер точки в маршруте |
| fDELIVERYADDRESSID | int | нет | Адрес доставки/точки визита (→ адрес клиента) |

- Ключи и связи: кластерный уникальный `PK_PLANNEDROUTESLIST` по (`fISN`, `fROWNUM`). Неявные связи: `fISN` → `DOCUMENTS.fISN` (заголовок плана), `fCUSTOMERID` → `CUSTOMERS.fID`, `fDELIVERYADDRESSID` → адрес доставки клиента.

## dbo.PLANNEDROUTESLISTMOBILE  (176 145 строк)

- Назначение: план визитов, привязанный напрямую к дате и агенту (выгрузка плана на мобильное устройство агента). В отличие от `PLANNEDROUTESLIST`, дата хранится в самой строке, без обращения к `DOCUMENTS`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата планового маршрута |
| fSALESAGENTID | int | нет | Агент, которому назначен маршрут (→ SALESAGENTS.fID) |
| fCUSTOMERID | int | нет | Запланированный клиент/точка (→ CUSTOMERS.fID) |
| fCOMMENT | nvarchar(50) | нет | Комментарий к строке плана |
| fROWNUM | smallint | нет | Порядковый номер точки в маршруте дня |
| fISN | uniqueidentifier | нет | Суррогатный ключ строки (уникальный) |

- Ключи и связи: кластерный `PK_PLANNEDROUTESLISTMOBILE` по (`fSALESAGENTID`, `fDATE`); уникальные индексы по (`fDATE`,`fSALESAGENTID`,`fCUSTOMERID`,`fROWNUM`) и по `fISN`. Неявные связи: `fSALESAGENTID` → `SALESAGENTS.fID`, `fCUSTOMERID` → `CUSTOMERS.fID`.

## dbo.PLANNEDROUTEDOCS  (7 строк)

- Назначение: связка документа-плана маршрута с другими документами (например, привязка сопутствующих документов к плану). Малый объём указывает на служебное/редко используемое сопоставление.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | ISN документа-плана маршрута (→ DOCUMENTS.fISN / план) |
| fDOCISN | uniqueidentifier | нет | ISN связанного документа (→ DOCUMENTS.fISN) |
| fCOMMENT | nvarchar(50) | нет | Комментарий к связке |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный уникальный `PK_PLANNEDROUTEDOCS` по (`fISN`, `fROWNUM`); уникальный индекс по (`fISN`, `fDOCISN`). Неявные связи: `fISN` и `fDOCISN` → `DOCUMENTS.fISN`.

## dbo.ROUTETEMPLATES  (50 строк)

- Назначение: шаблоны (заголовки) регулярных маршрутов агента — задают периодичность посещений по неделям и дням недели для территории/агента. По шаблонам генерируются плановые маршруты.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Идентификатор шаблона маршрута (PK) |
| fSALESAREA | nvarchar(6) | нет | Код территории продаж (→ справочник территорий, часто TREES/CUSTOMERSALESAREAS) |
| fSALESAGENTID | int | нет | Агент-владелец шаблона (→ SALESAGENTS.fID) |
| fWEEK | tinyint | нет | Номер недели в цикле периодичности |
| fWEEKDAY | tinyint | нет | День недели маршрута |
| fPERIODICITY | tinyint | нет | Периодичность повторения маршрута (в неделях) |
| fCOMMENT | nvarchar(255) | нет | Комментарий/наименование шаблона |
| fTS | timestamp | нет | Версия строки (rowversion, для конкурентного доступа) |
| fBODY | nvarchar(3000) | да | Служебное/расширенное тело (доп. настройки шаблона) |

- Ключи и связи: кластерный уникальный `PK_ROUTETEMPLATES` по `fID`; индексы по `fSALESAREA` и `fSALESAGENTID`. Ссылается: `ROUTETEMPLATESLIST.fID` → `ROUTETEMPLATES.fID`. Неявные связи: `fSALESAGENTID` → `SALESAGENTS.fID`, `fSALESAREA` → справочник территорий.

## dbo.ROUTETEMPLATESLIST  (1 572 строк)

- Назначение: строки шаблона маршрута — список клиентов/точек, входящих в шаблон, с порядком обхода. Дочерняя таблица к `ROUTETEMPLATES`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Идентификатор шаблона (→ ROUTETEMPLATES.fID) |
| fCUSTOMERID | int | нет | Клиент/точка в шаблоне (→ CUSTOMERS.fID) |
| fCOMMENT | nvarchar(50) | нет | Комментарий к строке |
| fROWNUM | smallint | нет | Порядковый номер точки в шаблоне |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fDELIVERYADDRESSID | int | нет | Адрес доставки/точки визита (→ адрес клиента) |

- Ключи и связи: кластерный уникальный `PK_ROUTETEMPLATESLIST` по (`fID`, `fROWNUM`). Внешний ключ `FK_ROUTETEMPLATESLIST_fID`: `fID` → `ROUTETEMPLATES.fID`. Неявные связи: `fCUSTOMERID` → `CUSTOMERS.fID`.

## dbo.ROUTECHANGEREQUESTS  (1 строка)

- Назначение: заявки на изменение шаблона/плана маршрута (заголовок заявки) — предложение агента изменить состав или расписание визитов с состоянием обработки. По структуре повторяет `ROUTETEMPLATES` плюс поля даты и статуса.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Идентификатор заявки (PK) |
| fCREATIONDATE | datetime | нет | Дата/время создания заявки |
| fSALESAGENTID | int | нет | Автор заявки — агент (→ SALESAGENTS.fID) |
| fWEEK | tinyint | нет | Номер недели в цикле периодичности |
| fWEEKDAY | tinyint | нет | День недели маршрута |
| fPERIODICITY | tinyint | нет | Периодичность повторения |
| fCOMMENT | nvarchar(255) | нет | Комментарий к заявке |
| fDATE | smalldatetime | нет | Дата, к которой относится изменение |
| fSTATE | smallint | нет | Статус заявки (по умолчанию 0 — новая/на рассмотрении) |

- Ключи и связи: кластерный уникальный `PK_ROUTECHANGEREQUESTS` по `fID`. Неявные связи: `fSALESAGENTID` → `SALESAGENTS.fID`; `fID` → `ROUTECHANGEREQUESTSDETAILS.fREQUESTID`.

## dbo.ROUTECHANGEREQUESTSDETAILS  (32 строки)

- Назначение: строки заявки на изменение маршрута — предлагаемый список клиентов/точек с адресами доставки. Дочерняя таблица к `ROUTECHANGEREQUESTS`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fREQUESTID | int | нет | Идентификатор заявки (→ ROUTECHANGEREQUESTS.fID) |
| fCUSTOMERID | int | нет | Клиент/точка в заявке (→ CUSTOMERS.fID) |
| fDELIVERYADDRESSID | int | нет | Адрес доставки/точки визита (→ адрес клиента) |
| fCOMMENT | nvarchar(50) | нет | Комментарий к строке |
| fROWNUM | smallint | нет | Порядковый номер точки в заявке |

- Ключи и связи: кластерный уникальный `PK_ROUTECHANGEREQUESTSDETAILS` по (`fREQUESTID`, `fROWNUM`). Неявная связь: `fREQUESTID` → `ROUTECHANGEREQUESTS.fID`, `fCUSTOMERID` → `CUSTOMERS.fID`.

## dbo.CARS  (10 строк)

- Назначение: справочник автомобилей доставки с ограничениями по грузоподъёмности (объём и вес) и признаком закрытия. Используется для планирования развозки товара.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(12) | нет | Код автомобиля (PK); в данных встречается госномер |
| fNAME | nvarchar(50) | нет | Наименование/марка автомобиля |
| fMAXCAPACITYBYVOLUME | money | нет | Максимальная вместимость по объёму |
| fDESCRINTAXSRV | nvarchar(50) | нет | Описание для налоговой службы |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fCOLOR | varchar(9) | да | Цвет отображения (для UI/карты) |
| fMINCAPACITYBYVOLUME | money | нет | Минимальная загрузка по объёму |
| fISCLOSED | bit | нет | Признак закрытой (неактивной) записи |
| fEXTERNALCODE | nvarchar(20) | нет | Внешний код (интеграция) |
| fBODY | nvarchar(3000) | да | Служебное/расширенное тело записи |
| fMINCAPACITYBYWEIGHT | money | нет | Минимальная загрузка по весу |
| fMAXCAPACITYBYWEIGHT | money | нет | Максимальная вместимость по весу |

- Ключи и связи: кластерный уникальный `PK_CARS` по `fCODE`. Неявная связь: `fCODE` → `SALESAGENTCARS.fCARCODE`.

## dbo.SALESAGENTCARS  (51 строка)

- Назначение: закрепление автомобилей за торговыми агентами (кто на какой машине ездит), с признаком автомобиля по умолчанию.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Агент (→ SALESAGENTS.fID) |
| fCARCODE | nvarchar(12) | нет | Код автомобиля (→ CARS.fCODE) |
| fDEFAULT | bit | нет | Признак автомобиля по умолчанию для агента |
| fROWNUM | smallint | нет | Порядковый номер записи |

- Ключи и связи: кластерный уникальный `PK_SALESAGENTCARS` по (`fSALESAGENTID`, `fCARCODE`). Неявные связи: `fSALESAGENTID` → `SALESAGENTS.fID`, `fCARCODE` → `CARS.fCODE`.

---

## Связи домена

Планирование (шаблоны → план → выгрузка на мобильный):
- `ROUTETEMPLATES` (заголовок шаблона) 1—N `ROUTETEMPLATESLIST` по `fID` (единственный явный FK домена). Шаблон привязан к агенту (`fSALESAGENTID`) и территории (`fSALESAREA`).
- Заявки на изменение маршрута: `ROUTECHANGEREQUESTS` (заголовок) 1—N `ROUTECHANGEREQUESTSDETAILS` по `fREQUESTID`; заявка привязана к агенту.
- Плановые маршруты: `PLANNEDROUTESLIST` — строки плана, где `fISN` = ISN документа-плана в `DOCUMENTS` (`fDOCTYPE = 10`); дата плана берётся из `DOCUMENTS.fDATE`. `PLANNEDROUTEDOCS` связывает документ-план (`fISN`) с другими документами (`fDOCISN`).
- `PLANNEDROUTESLISTMOBILE` — тот же план, но денормализованный под мобильное устройство (`fDATE` + `fSALESAGENTID` в самой строке), без обращения к `DOCUMENTS`.

Исполнение (факт + GPS):
- `ACTUALROUTES` — фактические визиты; сопоставляются с планом по (`fCUSTOMERID`, дата). Признак `fFORMOBILEPLANNED` указывает, что визит исходил из мобильного плана.
- `AGENTLOCATIONS` — непрерывный GPS-трек по `fSALESAGENTID` + `fDATETIME`; служит контекстом траектории вокруг визитов из `ACTUALROUTES`.

Транспорт:
- `CARS` 1—N `SALESAGENTCARS` по `fCARCODE`/`fCODE`; агент связывает домен с автомобилями доставки.

Связи с соседними доменами:
- Агенты: `fSALESAGENTID` → `SALESAGENTS.fID` (во всех таблицах, кроме `CARS`).
- Клиенты/точки: `fCUSTOMERID` → `CUSTOMERS.fID`; `fDELIVERYADDRESSID` → адрес доставки клиента.
- Документы: `PLANNEDROUTESLIST.fISN`, `PLANNEDROUTEDOCS.fISN/fDOCISN` → `DOCUMENTS.fISN`.
- Территории: `ROUTETEMPLATES.fSALESAREA` → справочник территорий (`CUSTOMERSALESAREAS`/TREES).
- Продажи (домен продаж): факт визита из `ACTUALROUTES` в отчётах приложения сопоставляется с фактом заказа из `SALES` по территории и клиенту.

## Примеры отчётных запросов

1) План vs факт визитов по территории за период (аналог логики `route-stats` в app_v2.py):

```sql
WITH AreaCustomers AS (
    SELECT fCUSTOMERID
    FROM CUSTOMERSALESAREAS
    WHERE fSALESAREA = '101'
),
PlannedVisits AS (
    SELECT l.fCUSTOMERID, CAST(d.fDATE AS DATE) AS VisitDate
    FROM DOCUMENTS d
    JOIN PLANNEDROUTESLIST l ON d.fISN = l.fISN
    WHERE d.fDOCTYPE = 10
      AND d.fDATE >= '2025-06-01' AND d.fDATE <= '2025-06-30'
      AND l.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
),
ActualVisits AS (
    SELECT a.fCUSTOMERID, CAST(a.fDATE AS DATE) AS VisitDate
    FROM ACTUALROUTES a
    WHERE a.fDATE >= '2025-06-01' AND a.fDATE <= '2025-06-30'
      AND a.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
)
SELECT
    (SELECT COUNT(*) FROM PlannedVisits) AS PlannedCount,
    (SELECT COUNT(*) FROM ActualVisits)  AS VisitedCount,
    (SELECT COUNT(*) FROM PlannedVisits p
       WHERE NOT EXISTS (SELECT 1 FROM ActualVisits a
                         WHERE a.fCUSTOMERID = p.fCUSTOMERID AND a.VisitDate = p.VisitDate)
    ) AS MissedCount,
    (SELECT COUNT(*) FROM ActualVisits a
       WHERE NOT EXISTS (SELECT 1 FROM PlannedVisits p
                         WHERE p.fCUSTOMERID = a.fCUSTOMERID AND p.VisitDate = a.VisitDate)
    ) AS UnplannedCount;
```

2) Активность агентов по фактическим визитам за день (число визитов и средняя длительность):

```sql
SELECT
    a.fSALESAGENTID,
    COUNT(*)                                        AS VisitsCount,
    COUNT(DISTINCT a.fCUSTOMERID)                   AS CustomersVisited,
    AVG(DATEDIFF(SECOND, a.fSTARTTIME, a.fENDTIME)) AS AvgVisitSeconds
FROM ACTUALROUTES a
WHERE a.fDATE = '2025-06-10'
  AND a.fENDTIME IS NOT NULL
GROUP BY a.fSALESAGENTID
ORDER BY VisitsCount DESC;
```

3) Распределение результатов визитов (fVISITRESULT) за период:

```sql
SELECT
    a.fVISITRESULT,
    COUNT(*) AS Cnt
FROM ACTUALROUTES a
WHERE a.fDATE >= '2025-06-01' AND a.fDATE <= '2025-06-30'
GROUP BY a.fVISITRESULT
ORDER BY Cnt DESC;
```

4) Автомобили, закреплённые за агентами (с признаком «по умолчанию»):

```sql
SELECT
    sac.fSALESAGENTID,
    c.fCODE,
    c.fNAME,
    sac.fDEFAULT,
    c.fMAXCAPACITYBYVOLUME,
    c.fMAXCAPACITYBYWEIGHT
FROM SALESAGENTCARS sac
JOIN CARS c ON c.fCODE = sac.fCARCODE
WHERE c.fISCLOSED = 0
ORDER BY sac.fSALESAGENTID, sac.fDEFAULT DESC;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
