# Заказы, возвраты и резервирование

Домен объединяет документы предварительного спроса и обеспечения товародвижения в системе van-sales AS-Sales Management 7: предзаказы клиентов (`ORDERS`), возвраты товара и заказы на возврат (`RETURNS`, `RETURNORDERS`), обеспечение поставок под заказы (`PROVIDINGDELIVERIES`), а также механику резервирования остатков — регистр движений и остатков резерва (`HIRESERVATION`, `HIRESTRESERVATION`), строки резервируемых товаров (`PRODUCTSRESERVATIONDETAILS`) и справочники схем резервирования по складам (`RESERVATIONSCHEMES`, `RESERVATIONSCHEMEDETAILS`). Это оперативный контур ERP: он фиксирует, что клиент заказал, что вернул и какой товар зарезервирован на складах под будущие отгрузки.

> Примечание: таблицы домена не используются в аналитическом слое `app_v2.py` (дашборд построен вокруг продаж и долга). Назначение колонок выведено из схемы, образцов данных (`samples`) и отраслевых соглашений об именовании ERP. Там, где смысл не подтверждается данными, указано «назначение не установлено».

Общие соглашения домена:
- `fISN` (uniqueidentifier) — суррогатный ключ шапки документа; строки и регистры ссылаются на шапку по `fISN`.
- Документы «шапочного» типа (`ORDERS`, `RETURNS`, `RETURNORDERS`) идентифицируются номером `fDOCNUM` (кластерный PK) в разрезе `fDIVISION`; `fISN` — уникальный вторичный ключ.
- Неявные связи: `fCUSTOMERID → CUSTOMERS.fID`, `fPRODUCTID → PRODUCTS.fID`, `fSALESAGENTID`/`fVANAGENTID → SALESAGENTS`, `fSALESAREA`/`fDIVISION`/`fDOCGROUP` — коды справочников (`TREES`/`TREEDEF`).
- `fDBCR`: `'D'` — дебет (приход/постановка в резерв), `'C'` — кредит (расход/снятие резерва).

---

## dbo.ORDERS  (328 587 строк)

- Назначение: шапки клиентских заказов (предзаказов) — что и на какую сумму клиент заказал, кем и когда оформлено, к какой территории и дате доставки относится.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ документа-заказа |
| fDATE | smalldatetime | нет | Дата заказа |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fDOCNUM | nvarchar(12) | нет | Номер документа-заказа (кластерный PK) |
| fCUSTOMERID | int | нет | Клиент, → CUSTOMERS.fID |
| fSALESAGENTID | int | нет | Торговый агент, → SALESAGENTS |
| fVANAGENTID | int | нет | Агент van-sales (экспедитор), → SALESAGENTS |
| fDELIVERYCAR | nvarchar(12) | нет | Код машины доставки |
| fDELIVERYDATE | smalldatetime | да | Планируемая дата доставки |
| fPRICELISTTYPE | nvarchar(2) | да | Тип прайс-листа (напр. '01') |
| fDELAYDAYS | smallint | нет | Отсрочка платежа, дней |
| fPRIORITY | smallint | нет | Приоритет заказа |
| fPAYTYPE | nvarchar(1) | да | Тип оплаты |
| fTOTALSUM | money | нет | Итоговая сумма заказа |
| fVATSUM | money | нет | Сумма НДС |
| fEXCISESUM | money | нет | Сумма акциза |
| fCOMMENT | nvarchar(255) | нет | Комментарий |
| fSALESAREA | nvarchar(6) | нет | Код территории/области сбыта (TREES) |
| fEXPTOTAXPROG | bit | нет | Признак экспорта в налоговую программу |
| fTAXSERIANUMBER | nvarchar(11) | да | Серия/номер налогового документа |
| fDOCGROUP | nvarchar(3) | нет | Группа документа |
| fCREATIONTYPEID | uniqueidentifier | да | Тип/источник создания документа |
| fSTATE | tinyint | да | Состояние документа (напр. 2 в образцах — проведён) |
| fCONTACT | nvarchar(50) | нет | Контактное лицо |
| fB2BID | uniqueidentifier | да | Идентификатор заказа из B2B-канала (если создан через B2B) |
| fCREATEMETHOD | varchar(1) | нет | Способ создания заказа |
| fADDITIONALDISCOUNT | money | нет | Дополнительная скидка |
| fUNDISCOUNTEDSUM | money | нет | Сумма до скидки |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус выгрузки во внешнюю систему |
| fSUBMITDATE | smalldatetime | да | Дата отправки/подтверждения |
| fORGANIZATIONACCOUNT | nvarchar(22) | нет | Банковский счёт организации |
| fENVFEESUM | money | нет | Сумма экологического сбора |
| fASSIGNOR | nvarchar(6) | нет | Код назначившего/поручителя |
| fCOSIGNDATE | smalldatetime | да | Дата со-подписания |
| fDELIVERYADDRESSID | int | нет | Адрес доставки, → адрес клиента |

- Ключи и связи: PK — `fDOCNUM` (кластерный), уникальный индекс по `fISN`. Связи: `fCUSTOMERID → CUSTOMERS.fID`; `fSALESAGENTID`/`fVANAGENTID → SALESAGENTS`; `fSALESAREA`/`fDIVISION`/`fDOCGROUP` — коды справочников. Строки обеспечения — `PROVIDINGDELIVERIES.fORDERNUM = ORDERS.fDOCNUM`; движения резерва — `HIRESERVATION.fORDERISN = ORDERS.fISN`.

---

## dbo.RETURNS  (21 211 строк)

- Назначение: шапки документов возврата товара от клиента; фиксируют сумму возврата, причину, ссылку на исходную продажу и фискальные (ЭККА/ECR) реквизиты.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ документа возврата |
| fDATE | smalldatetime | нет | Дата возврата |
| fDIVISION | nvarchar(6) | нет | Код подразделения |
| fDOCNUM | nvarchar(12) | нет | Номер документа возврата (кластерный PK) |
| fCUSTOMERID | int | нет | Клиент, → CUSTOMERS.fID |
| fSALESAGENTID | int | нет | Торговый агент, → SALESAGENTS |
| fVANAGENTID | int | нет | Агент van-sales, → SALESAGENTS |
| fSALENUM | nvarchar(12) | нет | Номер исходной продажи, → SALES.fDOCNUM |
| fPRICELISTTYPE | nvarchar(2) | да | Тип прайс-листа |
| fTOTALSUM | money | нет | Итоговая сумма возврата |
| fCOMMENT | nvarchar(255) | нет | Комментарий |
| fSALESAREA | nvarchar(6) | нет | Код территории сбыта (TREES) |
| fDOCGROUP | nvarchar(3) | нет | Группа документа |
| fRETURNREASON | nvarchar(3) | да | Код причины возврата (справочник) |
| fCREATIONTYPEID | uniqueidentifier | да | Тип/источник создания |
| fSTATE | tinyint | да | Состояние документа |
| fCONTACT | nvarchar(50) | нет | Контактное лицо |
| fCREATEMETHOD | varchar(1) | нет | Способ создания |
| fEXPTOTAXPROG | bit | нет | Признак экспорта в налоговую программу |
| fTAXSERIANUMBER | nvarchar(11) | да | Серия/номер налогового документа |
| fADDITIONALDISCOUNT | money | нет | Дополнительная скидка |
| fUNDISCOUNTEDSUM | money | нет | Сумма до скидки |
| fDELIVERYCAR | nvarchar(12) | нет | Код машины |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус выгрузки во внешнюю систему |
| fBASERETURNDOCNUMBER | nvarchar(12) | да | Номер базового документа возврата |
| fBASERETURNTOTALSUM | money | да | Сумма базового документа возврата |
| fBASERETURNDOCISN | uniqueidentifier | да | fISN базового документа возврата (заказа на возврат) |
| fSUBMITDATE | smalldatetime | да | Дата отправки/подтверждения |
| fORGANIZATIONACCOUNT | nvarchar(22) | нет | Банковский счёт организации |
| fASSIGNOR | nvarchar(6) | нет | Код назначившего/поручителя |
| fVATSUM | money | нет | Сумма НДС |
| fEXCISESUM | money | нет | Сумма акциза |
| fENVFEESUM | money | нет | Сумма экологического сбора |
| fECRCHECKNUM | nvarchar(12) | нет | Номер чека ЭККА (ECR) |
| fECRCHECKDATE | datetime | да | Дата чека ЭККА |
| fECRCASHSUM | money | нет | Наличная сумма по ЭККА |
| fECRNONCASHSUM | money | нет | Безналичная сумма по ЭККА |
| fCOSIGNDATE | smalldatetime | да | Дата со-подписания |
| fDELIVERYADDRESSID | int | нет | Адрес доставки/забора |
| fECRCRN | nvarchar(12) | нет | Регистрационный номер ЭККА (CRN) |

- Ключи и связи: PK — `fDOCNUM` (кластерный), уникальный индекс по `fISN`. Связи: `fCUSTOMERID → CUSTOMERS.fID`; `fSALENUM → SALES.fDOCNUM` (исходная продажа, по которой оформлен возврат); `fBASERETURNDOCISN → RETURNORDERS.fISN` (заказ на возврат-основание).

---

## dbo.RETURNORDERS  (17 строк)

- Назначение: шапки заказов на возврат — предварительные документы-заявки на возврат товара, на основании которых затем формируется фактический возврат (`RETURNS.fBASERETURNDOCISN`). Малое число строк указывает на редко используемый или недавно введённый механизм.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ заказа на возврат |
| fDATE | smalldatetime | нет | Дата документа |
| fDIVISION | nvarchar(6) | нет | Код подразделения |
| fDOCNUM | nvarchar(12) | нет | Номер документа (кластерный PK) |
| fCUSTOMERID | int | нет | Клиент, → CUSTOMERS.fID |
| fSALESAGENTID | int | нет | Торговый агент, → SALESAGENTS |
| fVANAGENTID | int | нет | Агент van-sales, → SALESAGENTS |
| fDELIVERYCAR | nvarchar(12) | нет | Код машины |
| fDELIVERYDATE | smalldatetime | да | Планируемая дата забора/доставки |
| fSALENUM | nvarchar(12) | нет | Номер исходной продажи, → SALES.fDOCNUM |
| fPRICELISTTYPE | nvarchar(2) | нет | Тип прайс-листа |
| fADDITIONALDISCOUNT | money | нет | Дополнительная скидка |
| fUNDISCOUNTEDSUM | money | нет | Сумма до скидки |
| fTOTALSUM | money | нет | Итоговая сумма |
| fVATSUM | money | нет | Сумма НДС |
| fEXCISESUM | money | нет | Сумма акциза |
| fENVFEESUM | money | нет | Сумма экологического сбора |
| fCOMMENT | nvarchar(255) | нет | Комментарий |
| fSALESAREA | nvarchar(6) | нет | Код территории сбыта (TREES) |
| fDOCGROUP | nvarchar(3) | нет | Группа документа |
| fRETURNREASON | nvarchar(3) | нет | Код причины возврата |
| fCREATIONTYPEID | uniqueidentifier | да | Тип/источник создания |
| fSTATE | tinyint | нет | Состояние документа |
| fCONTACT | nvarchar(50) | нет | Контактное лицо |
| fORGANIZATIONACCOUNT | nvarchar(22) | нет | Банковский счёт организации |
| fCREATEMETHOD | varchar(1) | нет | Способ создания |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус выгрузки во внешнюю систему |
| fDELIVERYADDRESSID | int | нет | Адрес доставки/забора |

- Ключи и связи: PK — `fDOCNUM` (кластерный, `PK_RRETURNORDERS`), уникальный индекс по `fISN`. Связи: `fCUSTOMERID → CUSTOMERS.fID`; `fSALENUM → SALES.fDOCNUM`; на этот документ ссылается `RETURNS.fBASERETURNDOCISN = RETURNORDERS.fISN`.

---

## dbo.PROVIDINGDELIVERIES  (1 808 931 строк)

- Назначение: строки обеспечения поставок под заказы — по каждому заказу (`fORDERNUM`) фиксируют, каким товаром и в каком количестве закрывается позиция (в т.ч. замена товара через `fORDEREDPRODUCTID` → `fPRODUCTID`). Крупнейшая таблица домена — построчный регистр реализации/обеспечения заказов.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ строки (кластерный); группирует строки одного документа обеспечения |
| fORDERNUM | nvarchar(12) | нет | Номер обеспечиваемого заказа, → ORDERS.fDOCNUM |
| fPRODUCTID | int | нет | Фактически поставляемый товар, → PRODUCTS.fID |
| fOPERATION | varchar(3) | нет | Код операции (в образцах 'RLZ' — реализация/обеспечение) |
| fQUANTITY | money | нет | Количество |
| fROWNUM | smallint | нет | Номер строки в документе |
| fORDEREDPRODUCTID | int | да | Изначально заказанный товар (при замене отличается от fPRODUCTID), → PRODUCTS.fID |
| fDOCTYPE | tinyint | нет | Тип документа обеспечения |
| fADDITIONALINFO | nvarchar(50) | нет | Доп. информация по строке |

- Ключи и связи: кластерный индекс по `fISN` (не уникальный — группирует строки). Связи: `fORDERNUM → ORDERS.fDOCNUM`; `fPRODUCTID`/`fORDEREDPRODUCTID → PRODUCTS.fID`. Индекс `(fDOCTYPE, fORDERNUM, fORDEREDPRODUCTID, fOPERATION)` — для сопоставления заказанного и обеспеченного.

---

## dbo.HIRESERVATION  (233 161 строк)

- Назначение: регистр движений резервирования товара (History) — каждое событие постановки/снятия резерва на складе. Операция `RSV` с `fDBCR='D'` ставит товар в резерв, `PRV` с `fDBCR='C'` снимает резерв (обеспечение/отпуск). Является источником для остатков `HIRESTRESERVATION`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fORDERISN | uniqueidentifier | да | Заказ, под который резервируется товар, → ORDERS.fISN |
| fSTORAGE | nvarchar(20) | нет | Код склада/хранилища |
| fPRODUCTID | int | нет | Товар, → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество в движении |
| fOP | varchar(3) | нет | Код операции ('RSV' — резерв, 'PRV' — обеспечение/снятие) |
| fDBCR | varchar(1) | нет | 'D' — постановка в резерв, 'C' — снятие резерва |
| fDATE | smalldatetime | нет | Дата движения |
| fBASEISN | uniqueidentifier | нет | fISN документа-основания движения |
| fUSERID | int | нет | Пользователь, выполнивший операцию |
| fRESERVATIONISN | uniqueidentifier | нет | fISN документа резервирования (группирует движения одного резерва) |

- Ключи и связи: кластерный индекс по `(fPRODUCTID, fDATE, fSTORAGE)`. Связи: `fORDERISN → ORDERS.fISN`; `fPRODUCTID → PRODUCTS.fID`; `fRESERVATIONISN → PRODUCTSRESERVATIONDETAILS.fISN` (шапка документа резервирования); `fUSERID → пользователи системы`.

---

## dbo.HIRESTRESERVATION  (332 строки)

- Назначение: остатки регистра резервирования (Rest) — текущее суммарное зарезервированное количество по каждой паре товар+склад. Свёртка движений `HIRESERVATION`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSTORAGE | nvarchar(20) | нет | Код склада/хранилища |
| fPRODUCTID | int | нет | Товар, → PRODUCTS.fID |
| fQUANTITY | money | нет | Текущий зарезервированный остаток |

- Ключи и связи: PK (уникальный кластерный) — `(fPRODUCTID, fSTORAGE)`. Связь: `fPRODUCTID → PRODUCTS.fID`. Остаток соответствует сальдо движений `HIRESERVATION` по тому же товару и складу.

---

## dbo.PRODUCTSRESERVATIONDETAILS  (143 673 строки)

- Назначение: строки документов резервирования товаров — какие товары и в каком количестве резервируются на складе в рамках одного документа резерва (`fISN`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ документа резервирования (кластерный); группирует строки |
| fSTORAGE | nvarchar(20) | нет | Код склада/хранилища |
| fPRODUCTID | int | нет | Резервируемый товар, → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество к резервированию |
| fROWNUM | smallint | нет | Номер строки в документе |

- Ключи и связи: кластерный индекс по `fISN` (не уникальный — группирует строки одного документа). Связи: `fPRODUCTID → PRODUCTS.fID`; `fISN → HIRESERVATION.fRESERVATIONISN` (движения по этому документу резервирования).

---

## dbo.RESERVATIONSCHEMES  (1 строка)

- Назначение: справочник схем резервирования — именованные наборы правил, определяющие, с каких складов выполняется резервирование.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код схемы (PK) |
| fNAME | nvarchar(50) | нет | Наименование схемы (в образце — «պահետի պահուստավորում») |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: PK — `fCODE`. Детализация состава складов — `RESERVATIONSCHEMEDETAILS.fSCHEMECODE = RESERVATIONSCHEMES.fCODE`.

---

## dbo.RESERVATIONSCHEMEDETAILS  (1 строка)

- Назначение: строки схемы резервирования — перечень складов, входящих в схему, с порядком приоритета.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Код схемы, → RESERVATIONSCHEMES.fCODE |
| fSTORAGECODE | nvarchar(20) | нет | Код склада, входящего в схему |
| fROWNUM | smallint | нет | Порядок/приоритет склада в схеме |

- Ключи и связи: PK — `(fSCHEMECODE, fSTORAGECODE)`. Связь: `fSCHEMECODE → RESERVATIONSCHEMES.fCODE`; `fSTORAGECODE` соотносится с кодом склада `fSTORAGE` в регистрах резервирования.

---

## Связи домена

Внутридоменные связи:
- `ORDERS.fDOCNUM` → `PROVIDINGDELIVERIES.fORDERNUM`: заказ и строки его обеспечения (какой товар фактически закрывает позиции заказа).
- `ORDERS.fISN` → `HIRESERVATION.fORDERISN`: заказ и движения резерва, поставленные под него.
- `PRODUCTSRESERVATIONDETAILS.fISN` → `HIRESERVATION.fRESERVATIONISN`: документ резервирования (строки) и его движения в регистре.
- `HIRESERVATION` (движения, разрез товар+склад) → `HIRESTRESERVATION` (остаток по тому же `fPRODUCTID`+`fSTORAGE`).
- `RESERVATIONSCHEMES.fCODE` → `RESERVATIONSCHEMEDETAILS.fSCHEMECODE` → набор складов (`fSTORAGECODE`), с которыми работают резервы.
- `RETURNORDERS.fISN` → `RETURNS.fBASERETURNDOCISN`: заказ на возврат → фактический возврат.

Связи с соседними доменами:
- Клиенты: `fCUSTOMERID → CUSTOMERS.fID` (во всех шапочных таблицах).
- Товары: `fPRODUCTID`/`fORDEREDPRODUCTID → PRODUCTS.fID` (в строках и регистрах).
- Агенты: `fSALESAGENTID`/`fVANAGENTID → SALESAGENTS`.
- Продажи: `RETURNS.fSALENUM` и `RETURNORDERS.fSALENUM → SALES.fDOCNUM` (исходная продажа, по которой оформлен возврат).
- Справочники территорий/групп: `fSALESAREA`, `fDIVISION`, `fDOCGROUP` — коды `TREES`/`TREEDEF`.

---

## Примеры отчётных запросов

Только чтение (SELECT). Все колонки существуют в таблицах домена.

1) Топ клиентов по сумме заказов за период:

```sql
SELECT TOP 20
    o.fCUSTOMERID,
    COUNT(*)            AS orders_cnt,
    SUM(o.fTOTALSUM)    AS total_amount,
    SUM(o.fVATSUM)      AS vat_amount
FROM dbo.ORDERS o
WHERE o.fDATE >= '2025-01-01' AND o.fDATE < '2026-01-01'
GROUP BY o.fCUSTOMERID
ORDER BY total_amount DESC;
```

2) Возвраты по причинам за период (сумма и количество документов):

```sql
SELECT
    r.fRETURNREASON,
    COUNT(*)          AS returns_cnt,
    SUM(r.fTOTALSUM)  AS return_amount
FROM dbo.RETURNS r
WHERE r.fDATE >= '2025-01-01' AND r.fDATE < '2026-01-01'
GROUP BY r.fRETURNREASON
ORDER BY return_amount DESC;
```

3) Текущие остатки резерва по складам и товарам (только ненулевые):

```sql
SELECT
    hr.fSTORAGE,
    hr.fPRODUCTID,
    hr.fQUANTITY AS reserved_qty
FROM dbo.HIRESTRESERVATION hr
WHERE hr.fQUANTITY <> 0
ORDER BY hr.fSTORAGE, reserved_qty DESC;
```

4) Обеспечение конкретного заказа: заказанный товар против фактически поставленного (замены):

```sql
SELECT
    pd.fORDERNUM,
    pd.fROWNUM,
    pd.fORDEREDPRODUCTID,
    pd.fPRODUCTID,
    pd.fQUANTITY,
    pd.fOPERATION
FROM dbo.PROVIDINGDELIVERIES pd
WHERE pd.fORDERNUM = '000000300422'
ORDER BY pd.fROWNUM;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
- [Формула расчёта долга](../../../DEBT_CALCULATION_FORMULA.md)
