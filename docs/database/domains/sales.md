# Продажи (расходные накладные)

Домен описывает реализацию товара покупателям в системе van-sales / дистрибуции AS-Sales Management 7: шапки расходных накладных (`SALES`) и их строки (`SALEDOCDETAILS`), бесплатные позиции-подарки (`SALEDOCGIFTS`), движение депозитной (возвратной) тары и обмены товара в продаже (`SALEDOCPRODUCTSONDEPOSIT`, `SALEDOCEXCHANGES`), документы, сформированные торговым агентом на мобильном устройстве (`SALESAGENTCREATEDDOCUMENTS`, `SALESAGENTCREATEDDOCUMENTBODIES`), а также учётные регистры движений и остатков проданных и депозитных товаров (`HISOLDPRODUCTS`, `HIRESTSOLDPRODUCTS`, `HIDEPOSITPRODUCTS`, `HIRESTDEPOSITPRODUCTS`). Ключевая связка домена — суррогатный ключ документа `fISN`: шапка и все дочерние строки/регистры ссылаются на накладную по нему.

> Важно: боевая таблица — `dbo.SALES` (UPPERCASE, ~370 тыс. строк). Существует также демонстрационная `dbo.Sales` (mixed-case, 0 строк) — это legacy/демо, к домену отношения не имеет.

---

## dbo.SALES  (370 572 строк)

- Назначение: шапка расходной накладной (реализации). Одна строка = один документ продажи: дата, дивизион, покупатель, торговый/van-агент, суммы (итог, НДС, акциз, экосбор), территория, тип оплаты и статус документа.
- Ключевой фильтр в приложении: `fSTATE = 2` — проведённый (подтверждённый) документ; неподтверждённые/черновые в отчёты не попадают.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ документа; на него ссылаются строки и регистры |
| fDATE | smalldatetime | нет | Дата документа продажи |
| fDIVISION | nvarchar(6) | нет | Код дивизиона/подразделения (справочник TREES) |
| fDOCNUM | nvarchar(12) | нет | Номер документа (кластерный PK `PK_SALES`) |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fSALESAGENTID | int | нет | Торговый агент (оформивший заказ) → SALESAGENTS.fID |
| fVANAGENTID | int | нет | Агент фургона / водитель-экспедитор → SALESAGENTS.fID |
| fDELIVERYCAR | nvarchar(12) | нет | Код автомобиля доставки |
| fPRICELISTTYPE | nvarchar(2) | да | Тип прайс-листа, применённого к документу |
| fDELAYDAYS | smallint | нет | Отсрочка платежа в днях |
| fPAYTYPE | nvarchar(1) | да | Тип оплаты (в коде `fPAYTYPE = 2` трактуется как продажа в кредит/с отсрочкой; пустая строка — наличный/без отсрочки) |
| fTOTALSUM | money | нет | Итоговая сумма документа (основная метрика выручки) |
| fVATSUM | money | нет | Сумма НДС |
| fEXCISESUM | money | нет | Сумма акциза |
| fCOMMENT | nvarchar(255) | нет | Комментарий к документу |
| fSALESAREA | nvarchar(6) | нет | Территория/район продаж (справочник TREES, fTREEID='SArea') |
| fDOCGROUP | nvarchar(3) | нет | Группа документа |
| fEXPTOTAXPROG | bit | нет | Признак экспорта в налоговую программу |
| fTAXSERIANUMBER | nvarchar(11) | да | Серия/номер налогового документа |
| fECRCHECKNUM | nvarchar(12) | нет | Номер фискального чека ККМ |
| fCREATIONTYPEID | uniqueidentifier | да | Идентификатор типа создания документа |
| fSTATE | tinyint | да | Статус документа (2 = проведён/подтверждён; фильтр по умолчанию в отчётах) |
| fCONTACT | nvarchar(50) | нет | Контактное лицо |
| fCREATEMETHOD | varchar(1) | нет | Метод создания документа |
| fADDITIONALDISCOUNT | money | нет | Дополнительная скидка на документ |
| fUNDISCOUNTEDSUM | money | нет | Сумма до скидки |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус отправки во внешнюю систему |
| fSUBMITDATE | smalldatetime | да | Дата отправки/сабмита |
| fB2BID | uniqueidentifier | да | Идентификатор связанного B2B-заказа |
| fORGANIZATIONACCOUNT | nvarchar(22) | нет | Банковский счёт организации |
| fENVFEESUM | money | нет | Сумма экологического сбора |
| fASSIGNOR | nvarchar(6) | нет | Код комитента/поручителя (справочник ASSIGNORS) |
| fECRCHECKDATE | datetime | да | Дата фискального чека |
| fTAXCODEWASPRINTEDONECR | bit | нет | Признак печати налогового кода на чеке ККМ |
| fECRCASHSUM | money | нет | Сумма оплаты наличными по чеку ККМ |
| fECRNONCASHSUM | money | нет | Сумма безналичной оплаты по чеку ККМ |
| fCOSIGNDATE | smalldatetime | да | Дата со-подписания |
| fECRPREPAYMENTSUM | money | нет | Сумма предоплаты по чеку ККМ |
| fDELIVERYADDRESSID | int | нет | Адрес доставки → CUSTOMERDELIVERYADDRESSES |
| fECRCRN | nvarchar(12) | нет | CRN (регистрационный номер ККМ) |

- Ключи и связи: кластерный PK `PK_SALES` по `fDOCNUM`; уникальный индекс `I_SALES1` по `fISN`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fSALESAGENTID/fVANAGENTID→SALESAGENTS.fID`, `fSALESAREA/fDIVISION/fDOCGROUP→TREES` (коды справочников), `fISN` — родитель для всех дочерних таблиц домена.

---

## dbo.SALEDOCDETAILS  (2 520 629 строк)

- Назначение: строки (позиции) расходной накладной — товар, количество, цена, скидка и суммы по каждой позиции. Связывается с шапкой через `fISN`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа SALES.fISN |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество |
| fPRICE | money | нет | Цена без скидки |
| fDISCOUNT | money | нет | Скидка (в % или сумме — по данным) |
| fDISCOUNTEDPRICE | money | нет | Цена со скидкой |
| fSUM | money | нет | Сумма по строке (итог позиции) |
| fEXCISESUM | money | нет | Сумма акциза по строке |
| fVATSUM | money | нет | Сумма НДС по строке |
| fROWNUM | smallint | нет | Порядковый номер строки в документе |
| fISPARTIESSELECTEDMANUAL | bit | нет | Признак ручного выбора партий |
| fENVFEESUM | money | нет | Сумма экосбора по строке |
| fADDITIONALINFO | nvarchar(50) | нет | Доп. информация по строке |
| fPRICELISTPRICE | money | нет | Цена из прайс-листа |

- Ключи и связи: кластерный индекс `PK_SALEDOCDETAILS` по `fISN`; индекс `I_SALEDOCDETAILS1` по `(fPRODUCTID, fISN)`. Неявные связи: `fISN→SALES.fISN`, `fPRODUCTID→PRODUCTS.fID`. В приложении сумма скидки по документу считается как `SUM(fPRICE*fQUANTITY - fSUM)`.

---

## dbo.SALEDOCGIFTS  (195 510 строк)

- Назначение: бесплатные позиции-подарки в составе накладной (промо, бонусные единицы) — товар и количество без цены.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа SALES.fISN |
| fPRODUCTID | int | нет | Подарочный товар → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество подарка |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fADDITIONALINFO | nvarchar(50) | нет | Доп. информация по строке |

- Ключи и связи: кластерный индекс `PK_SALEDOCGIFTS` по `fISN`; индекс `I_SALEDOCGIFTS1` по `(fPRODUCTID, fISN)`. Неявные связи: `fISN→SALES.fISN`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.SALEDOCPRODUCTSONDEPOSIT  (64 425 строк)

- Назначение: позиции депозитной (возвратной) тары, отгруженной вместе с продажей (например, бутыли/ящики под залог). Товар и количество по документу.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа SALES.fISN |
| fPRODUCTID | int | нет | Депозитный товар/тара → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fISPARTIESSELECTEDMANUAL | bit | нет | Признак ручного выбора партий |
| fADDITIONALINFO | nvarchar(50) | нет | Доп. информация по строке |

- Ключи и связи: кластерный индекс `PK_SALEDOCPRODUCTSONDEPOSIT` по `fISN`; индекс по `(fPRODUCTID, fISN)`. Неявные связи: `fISN→SALES.fISN`, `fPRODUCTID→PRODUCTS.fID`. Движение по этой таре отражается в регистрах `HIDEPOSITPRODUCTS`/`HIRESTDEPOSITPRODUCTS`.

---

## dbo.SALEDOCEXCHANGES  (0 строк)

- Назначение: позиции обмена товара в рамках продажи (замена/обмен единиц). На момент выгрузки таблица пуста — функциональность в текущей БД не используется.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа SALES.fISN |
| fPRODUCTID | int | нет | Обмениваемый товар → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fADDITIONALINFO | nvarchar(50) | нет | Доп. информация по строке |

- Ключи и связи: кластерный индекс `PK_SALEDOCEXCHANGES` по `fISN`; индекс по `(fPRODUCTID, fISN)`. Неявные связи: `fISN→SALES.fISN`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.SALESAGENTCREATEDDOCUMENTS  (248 370 строк)

- Назначение: шапка документа, созданного торговым агентом на мобильном устройстве (реестр «сырых» документов агента до/наряду с проведением в продажи). Одна строка = один документ агента.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSALESAGENTID | int | нет | Агент-автор документа → SALESAGENTS.fID |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fDATE | datetime | нет | Дата создания документа |
| fISN | uniqueidentifier | нет | Ключ документа (совпадает с SALES.fISN для проведённых продаж) |
| fDOCTYPE | tinyint | нет | Тип документа агента (в samples = 1) |

- Ключи и связи: уникальный кластерный PK `PK_SALESAGENTCREATEDDOCUMENTS` по `fISN`. Неявные связи: `fISN→SALES.fISN` и → `SALESAGENTCREATEDDOCUMENTBODIES.fISN`; `fSALESAGENTID→SALESAGENTS.fID`, `fCUSTOMERID→CUSTOMERS.fID`.

---

## dbo.SALESAGENTCREATEDDOCUMENTBODIES  (248 370 строк)

- Назначение: тело (полезная нагрузка) документа агента в формате JSON — сериализованное содержимое документа с мобильного (`DocNumber`, `Division`, `DocDate`, позиции и т.д.). Отношение 1:1 к `SALESAGENTCREATEDDOCUMENTS` по `fISN`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на SALESAGENTCREATEDDOCUMENTS.fISN (и SALES.fISN) |
| fBODY | nvarchar(max) | нет | JSON-тело документа агента |

- Ключи и связи: уникальный кластерный PK `PK_SALESAGENTCREATEDDOCUMENTBODIES` по `fISN`. Неявная связь: `fISN→SALESAGENTCREATEDDOCUMENTS.fISN`.

---

## dbo.HISOLDPRODUCTS  (1 263 791 строка)

- Назначение: регистр движений проданных товаров (History) — количественно-суммовые проводки реализации по покупателю/товару/дивизиону. Операция `fOP='RLZ'` (реализация), направление `fDBCR` (D/C). Основа для аналитики отгруженного количества.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата проводки |
| fDIVISION | nvarchar(6) | нет | Дивизион |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество движения |
| fDISCOUNTEDPRICE | money | нет | Цена со скидкой на момент проводки |
| fOP | varchar(3) | нет | Код операции (RLZ = реализация) |
| fDBCR | varchar(1) | нет | Дебет/кредит движения (D/C) |
| fBASE | uniqueidentifier | нет | Ключ документа-основания |
| fSALEISN | uniqueidentifier | да | Ссылка на документ продажи SALES.fISN |
| fUSERID | int | нет | Пользователь, создавший проводку |

- Ключи и связи: кластерный индекс `PK_HISOLDPRODUCTS` по `(fDIVISION, fCUSTOMERID, fPRODUCTID, fDATE)`; индексы по `fBASE`, `fSALEISN`, `fOP`, `(fPRODUCTID, fCUSTOMERID)`, `fDATE`. Неявные связи: `fSALEISN/fBASE→SALES.fISN`, `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.HIRESTSOLDPRODUCTS  (1 339 481 строка)

- Назначение: регистр остатков (Rest) проданных товаров — текущий остаток количества по ключу дивизион/покупатель/товар/документ продажи (свёртка движений `HISOLDPRODUCTS`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDIVISION | nvarchar(6) | нет | Дивизион |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fQUANTITY | money | нет | Остаток количества |
| fSALEISN | uniqueidentifier | да | Ссылка на документ продажи SALES.fISN |

- Ключи и связи: уникальный кластерный PK `PK_HIRESTSOLDPRODUCTS` по `(fDIVISION, fCUSTOMERID, fPRODUCTID, fSALEISN)`; индексы по `fSALEISN`, `fCUSTOMERID`, `(fPRODUCTID, fSALEISN)`. Неявные связи: `fSALEISN→SALES.fISN`, `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.HIDEPOSITPRODUCTS  (46 622 строки)

- Назначение: регистр движений депозитной (возвратной) тары (History) — выдача (`fOP='DEP'`) и возврат (`fOP='RET'`) тары по покупателю/товару с направлением `fDBCR` (D — выдано, C — возвращено).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата проводки |
| fDIVISION | nvarchar(6) | нет | Дивизион |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fPRODUCTID | int | нет | Депозитный товар/тара → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество движения |
| fOP | varchar(3) | нет | Код операции (DEP = выдача тары, RET = возврат) |
| fDBCR | varchar(1) | нет | Дебет/кредит движения (D/C) |
| fBASE | uniqueidentifier | нет | Ключ документа-основания |
| fUSERID | int | нет | Пользователь, создавший проводку |
| fSALEISN | uniqueidentifier | да | Ссылка на документ продажи SALES.fISN (для возвратов может быть NULL) |

- Ключи и связи: кластерный индекс `PK_HIDEPOSITPRODUCTS` по `(fDIVISION, fCUSTOMERID, fPRODUCTID, fDATE)`; индексы по `fBASE`, `fDATE`, `fSALEISN`, `fOP`, `(fPRODUCTID, fCUSTOMERID)`. Неявные связи: `fSALEISN/fBASE→SALES.fISN`, `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.HIRESTDEPOSITPRODUCTS  (27 752 строки)

- Назначение: регистр остатков (Rest) депозитной тары — сколько возвратной тары числится за покупателем по товару/документу (свёртка движений `HIDEPOSITPRODUCTS`; отрицательные значения — возврат/списание).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDIVISION | nvarchar(6) | нет | Дивизион |
| fCUSTOMERID | int | нет | Покупатель → CUSTOMERS.fID |
| fPRODUCTID | int | нет | Депозитный товар/тара → PRODUCTS.fID |
| fQUANTITY | money | нет | Остаток количества тары |
| fSALEISN | uniqueidentifier | да | Ссылка на документ продажи SALES.fISN |

- Ключи и связи: уникальный кластерный PK `PK_HIRESTDEPOSITPRODUCTS` по `(fDIVISION, fCUSTOMERID, fPRODUCTID, fSALEISN)`; индексы по `fSALEISN`, `(fPRODUCTID, fSALEISN)`. Неявные связи: `fSALEISN→SALES.fISN`, `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## Связи домена

- **Шапка → строки**: `SALES.fISN` = `SALEDOCDETAILS.fISN` = `SALEDOCGIFTS.fISN` = `SALEDOCPRODUCTSONDEPOSIT.fISN` = `SALEDOCEXCHANGES.fISN`. Один документ продажи имеет множество товарных строк, а также опциональные подарки, депозитную тару и обмены.
- **Документ агента**: `SALESAGENTCREATEDDOCUMENTS.fISN` 1:1 `SALESAGENTCREATEDDOCUMENTBODIES.fISN`; при проведении тот же `fISN` появляется в `SALES` — это мостик от мобильного документа к проведённой накладной.
- **Регистры реализации**: `HISOLDPRODUCTS.fSALEISN` и `HIRESTSOLDPRODUCTS.fSALEISN` → `SALES.fISN`; движения (`HISOLDPRODUCTS`, `fOP='RLZ'`) сворачиваются в остатки (`HIRESTSOLDPRODUCTS`).
- **Регистры депозита**: `HIDEPOSITPRODUCTS`/`HIRESTDEPOSITPRODUCTS` по `fSALEISN` → `SALES.fISN`; связаны по смыслу с `SALEDOCPRODUCTSONDEPOSIT` (тара, отгруженная в продаже).
- **Соседние домены (справочники)**: `fCUSTOMERID→CUSTOMERS.fID`; `fPRODUCTID→PRODUCTS.fID`; `fSALESAGENTID/fVANAGENTID→SALESAGENTS.fID`; `fSALESAREA/fDIVISION/fDOCGROUP/fASSIGNOR` — коды справочников TREES/ASSIGNORS (территория ищется по `TREES` с `fTREEID='SArea'`).
- **Домен долга/платежей**: регистр `HICUSTOMERSDEBT` (соседний домен) содержит долговые движения (`fDBCR='D'/'C'`) и платежи (`fOP='PAY'`); он не входит в этот домен, но именно он вместе с `SALES` формирует расчёт задолженности (см. `DEBT_CALCULATION_FORMULA.md`).

---

## Примеры отчётных запросов

Все запросы — только чтение (SELECT). По умолчанию отбираются проведённые документы (`fSTATE = 2`).

**1. Выручка и число накладных по территориям за период:**

```sql
SELECT
    s.fSALESAREA                    AS AreaCode,
    COUNT(*)                        AS DocsCount,
    SUM(s.fTOTALSUM)                AS TotalRevenue,
    SUM(s.fVATSUM)                  AS TotalVat,
    AVG(s.fTOTALSUM)                AS AvgDoc
FROM SALES s
WHERE s.fSTATE = 2
    AND s.fDATE >= '2025-01-01'
    AND s.fDATE <= '2025-12-31'
GROUP BY s.fSALESAREA
ORDER BY TotalRevenue DESC;
```

**2. Топ-20 товаров по сумме продаж (строки накладных):**

```sql
SELECT TOP 20
    sd.fPRODUCTID                   AS ProductId,
    p.fNAME                         AS ProductName,
    SUM(sd.fQUANTITY)               AS Qty,
    SUM(sd.fSUM)                    AS LineRevenue,
    SUM(sd.fPRICE * sd.fQUANTITY - sd.fSUM) AS DiscountAmount
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON sd.fISN = s.fISN
INNER JOIN PRODUCTS p        ON p.fID = sd.fPRODUCTID
WHERE s.fSTATE = 2
    AND s.fDATE >= '2025-01-01'
    AND s.fDATE <= '2025-12-31'
GROUP BY sd.fPRODUCTID, p.fNAME
ORDER BY LineRevenue DESC;
```

**3. Продажи в кредит vs. без отсрочки по месяцам:**

```sql
SELECT
    FORMAT(s.fDATE, 'yyyy-MM')                                          AS Month,
    SUM(s.fTOTALSUM)                                                    AS TotalSales,
    SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END)           AS CreditSales,
    SUM(CASE WHEN ISNULL(s.fPAYTYPE, '') <> '2' THEN s.fTOTALSUM ELSE 0 END) AS NonCreditSales
FROM SALES s
WHERE s.fSTATE = 2
    AND s.fDATE >= '2025-01-01'
GROUP BY FORMAT(s.fDATE, 'yyyy-MM')
ORDER BY Month;
```

**4. Остаток депозитной (возвратной) тары за покупателем:**

```sql
SELECT
    r.fCUSTOMERID                   AS CustomerId,
    c.fNAME                         AS CustomerName,
    r.fPRODUCTID                    AS ProductId,
    p.fNAME                         AS ProductName,
    SUM(r.fQUANTITY)                AS DepositBalance
FROM HIRESTDEPOSITPRODUCTS r
INNER JOIN CUSTOMERS c ON c.fID = r.fCUSTOMERID
INNER JOIN PRODUCTS  p ON p.fID = r.fPRODUCTID
GROUP BY r.fCUSTOMERID, c.fNAME, r.fPRODUCTID, p.fNAME
HAVING SUM(r.fQUANTITY) <> 0
ORDER BY DepositBalance DESC;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
- [Формула расчёта долга](../../../DEBT_CALCULATION_FORMULA.md)
