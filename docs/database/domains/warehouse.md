# Склад: остатки, движения, активы

Домен охватывает товарный складской учёт (склады, движения товара и текущие остатки), продукцию на руках у ван-агентов (van-sales), а также учёт фирменных активов (торговое оборудование — холодильники и т. п.), переданных клиентам. Данные организованы по классической для AS-Sales Management схеме «регистр движений (HI...) → регистр остатков (HIREST...)»: журнальные таблицы фиксируют каждую операцию прихода/расхода, а таблицы остатков хранят предвычисленную текущую позицию. Строки первичных документов (`...DETAILS`) ссылаются на шапку документа по `fISN`.

Важно: ни одна таблица этого домена не используется в `app_v2.py` и прочем коде дашборда (Grep по всем именам — 0 совпадений). Домен является чисто операционным (складская подсистема ERP) и в веб-аналитику не выведен. Назначения колонок ниже выведены из имён, образцов данных (`samples`), индексов и общих соглашений ERP; там, где смысл не подтверждён файлами истины, стоит пометка «назначение не установлено».

---

## dbo.STORAGES  (16 строк)

- Назначение: справочник складов (пахест). Каждая запись — физический или логический склад с кодом, названием, кладовщиком и адресом.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(20) | нет | Код склада (PK). Пример: `000` — «Հիմնական պահեստ» (основной склад) |
| fNAME | nvarchar(50) | нет | Название склада |
| fSTOCKKEEPER | nvarchar(50) | нет | ФИО кладовщика / материально ответственного |
| fADDRESS | nvarchar(250) | нет | Адрес склада |
| fCLOSE | bit | нет | Признак закрытого/неактивного склада (0 — активен) |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |
| fEXTERNALCODE | nvarchar(20) | нет | Внешний код для интеграций |
| fREMCHECKMODE | varchar(1) | нет | Режим контроля остатков (напр. `2`); назначение кодов не установлено |
| fBODY | nvarchar(3000) | да | Произвольные дополнительные данные / примечания |

- Ключи и связи: PK — `fCODE` (кластерный `PK_STORAGES`). На код склада ссылаются `HISTORAGES.fSTORAGE`, `HIRESTSTORAGES.fSTORAGE`, `HITRANSFERREDASSETS.fSTORAGE`, `HIRESTTRANSFERREDASSETS.fSTORAGE`, `STORAGESACCESSBYUSERS.fSTORAGE`, `PRODUCTACCOUNTINGDETAILS.fSTORAGEIN/fSTORAGEOUT` (по значению кода, явных FK нет).

---

## dbo.HISTORAGES  (377 902 строки)

- Назначение: регистр движений товара по складам (History). Каждая строка — операция прихода или расхода определённого товара на конкретном складе с датой и ссылкой на документ-основание.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции движения |
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Количество в операции |
| fOP | varchar(3) | нет | Код операции: `INP` — приход/ввод, `PRV`, `RLZ` — реализация и др. (справочник операций) |
| fDBCR | varchar(1) | нет | Направление движения: `D` — дебет (приход), `C` — кредит (расход) |
| fBASE | uniqueidentifier | нет | Ссылка на `fISN` шапки документа-основания |
| fUSERID | int | нет | Пользователь, выполнивший операцию → `USERS` |
| fSTORAGE | nvarchar(20) | нет | Код склада → `STORAGES.fCODE` |

- Ключи и связи: кластерный `PK_HISTORAGES` по `(fPRODUCTID, fDATE, fSTORAGE)` — неуникальный. Индексы по `fBASE`, `fDATE`, `(fPRODUCTID, fSTORAGE, fDATE)`, `(fSTORAGE, fDATE)`. Неявные связи: `fPRODUCTID→PRODUCTS.fID`, `fSTORAGE→STORAGES.fCODE`, `fBASE→fISN` документа (напр. `PRODUCTACCOUNTINGDETAILS.fISN`).

---

## dbo.HIRESTSTORAGES  (1 170 строк)

- Назначение: регистр остатков товара по складам (Rest) — предвычисленный текущий остаток каждого товара на каждом складе.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Текущий остаток товара на складе |
| fSTORAGE | nvarchar(20) | нет | Код склада → `STORAGES.fCODE` |

- Ключи и связи: уникальный кластерный `PK_HIRESTSTORAGES` по `(fPRODUCTID, fSTORAGE)`; индекс по `fSTORAGE`. Это «свёртка» движений `HISTORAGES` по паре товар+склад.

---

## dbo.PRODUCTACCOUNTINGDETAILS  (515 491 строка)

- Назначение: строки первичных документов товарного учёта (приход/списание/перемещение товара между складами). Одна строка — позиция товара в документе с ценой, суммой и складами источника/приёмника.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа (суррогатный ключ документа) |
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Количество по позиции |
| fROWNUM | smallint | нет | Порядковый номер строки в документе |
| fPRICE | money | нет | Цена за единицу |
| fSUM | money | нет | Сумма по позиции (fQUANTITY × fPRICE) |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация по строке |
| fSTORAGEIN | nvarchar(20) | нет | Код склада-приёмника (куда) → `STORAGES.fCODE`; пусто, если неприменимо |
| fSTORAGEOUT | nvarchar(20) | нет | Код склада-источника (откуда) → `STORAGES.fCODE`; пусто, если неприменимо |

- Ключи и связи: кластерный `PK_PRODUCTACCOUNTINGDETAILS` по `fISN` (неуникальный — несколько строк на документ). Уникальный индекс `(fSTORAGEIN, fSTORAGEOUT, fPRODUCTID, fISN)`; индекс `(fPRODUCTID, fISN)`. Связи: `fISN→` шапка документа, движения по этому документу лежат в `HISTORAGES.fBASE = fISN`.

---

## dbo.HIAGENTPRODUCTS  (1 766 029 строк)

- Назначение: регистр движений товара на руках у ван-агентов (History). Фиксирует загрузку товара на агента и его реализацию/возврат «с борта» — товарная позиция мобильного продавца.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции |
| fAGENTID | int | нет | Код ван-агента → `SALESAGENTS.fID` |
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Количество в операции |
| fOP | varchar(3) | нет | Код операции: `RLZ` — реализация и др. |
| fDBCR | varchar(1) | нет | Направление: `D` — приход к агенту (загрузка), `C` — расход у агента (продажа/возврат) |
| fBASE | uniqueidentifier | нет | Ссылка на `fISN` документа-основания |
| fUSERID | int | нет | Пользователь операции → `USERS` |

- Ключи и связи: кластерный `PK_HIAGENTPRODUCTS` по `(fAGENTID, fPRODUCTID, fDATE)` — неуникальный. Индексы по `fBASE`, `fDATE`, `(fAGENTID, fDATE, fPRODUCTID)`, `(fPRODUCTID, fDATE)`. Связи: `fAGENTID→SALESAGENTS.fID`, `fPRODUCTID→PRODUCTS.fID`, `fBASE→fISN` документа.

---

## dbo.HIRESTAGENTPRODUCTS  (8 447 строк)

- Назначение: регистр остатков товара у ван-агентов (Rest) — текущее количество каждого товара на руках у каждого агента.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fAGENTID | int | нет | Код ван-агента → `SALESAGENTS.fID` |
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Текущий остаток товара у агента |

- Ключи и связи: уникальный кластерный `PK_HIRESTAGENTPRODUCTS` по `(fAGENTID, fPRODUCTID)`. Свёртка движений `HIAGENTPRODUCTS` по паре агент+товар.

---

## dbo.ASSETS  (23 строки)

- Назначение: справочник фирменных активов (торгового оборудования), передаваемых клиентам, — например, холодильники («Սառնարան»). Каталог типов/единиц оборудования.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Код актива (PK) |
| fCODE | nvarchar(50) | нет | Код/артикул актива (напр. `1003`) |
| fNAME | nvarchar(250) | нет | Название актива |
| fFULLNAME | nvarchar(250) | нет | Полное наименование |
| fMEASUREUNIT | nvarchar(6) | нет | Единица измерения (напр. `հատ` — штука) |
| fGROUP | nvarchar(6) | нет | Код группы актива (справочник групп, напр. `001`); вероятно `TREES/TREEDEF` |
| fBODY | nvarchar(3000) | да | Произвольные дополнительные данные |
| fCLOSED | bit | нет | Признак закрытого/архивного актива |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный `PK_ASSETS1` по `fID`; индексы по `fCODE`, `fGROUP`, `fCLOSED`. На `fID` ссылаются `ASSETNUMBERS.fASSETID`, `ASSETACCOUNTINGDETAILS.fASSETID`, `ASSETSINVENTORYDETAILS.fASSETID`, `HITRANSFERREDASSETS.fASSETID`, `HIRESTTRANSFERREDASSETS.fASSETID`.

---

## dbo.ASSETNUMBERS  (23 строки)

- Назначение: инвентарные/серийные номера конкретных экземпляров активов. Связывает тип актива с его физическими номерами.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fASSETID | int | нет | Код актива → `ASSETS.fID` |
| fASSETNUMBER | nvarchar(50) | нет | Инвентарный/серийный номер экземпляра |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация по экземпляру |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: уникальный кластерный `PK_ASSETNUMBERS` по `(fASSETID, fASSETNUMBER)`; индекс по `fASSETNUMBER`. Связь: `fASSETID→ASSETS.fID`.

---

## dbo.ASSETACCOUNTINGDETAILS  (75 строк)

- Назначение: строки первичных документов учёта активов (приём/выдача оборудования). Позиция актива в документе с количеством и, при наличии, конкретным инвентарным номером.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа |
| fASSETID | int | нет | Код актива → `ASSETS.fID` |
| fQUANTITY | money | нет | Количество по позиции |
| fROWNUM | smallint | нет | Порядковый номер строки в документе |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация по строке |
| fASSETNUMBER | nvarchar(50) | нет | Инвентарный номер → `ASSETNUMBERS.fASSETNUMBER`; пусто, если не по номерам |

- Ключи и связи: кластерный `PK_ASSETACCOUNTINGDETAILS` по `fISN` (неуникальный); индекс `(fASSETID, fISN)`. Связи: `fISN→` шапка документа, `fASSETID→ASSETS.fID`, `(fASSETID, fASSETNUMBER)→ASSETNUMBERS`.

---

## dbo.ASSETSINVENTORYDETAILS  (0 строк)

- Назначение: строки документов инвентаризации активов (сверка фактического наличия оборудования). На момент выгрузки таблица пустая — функциональность заведена, но не используется.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа инвентаризации |
| fASSETID | int | нет | Код актива → `ASSETS.fID` |
| fASSETNUMBER | nvarchar(50) | нет | Инвентарный номер экземпляра → `ASSETNUMBERS` |
| fAVAILABLE | bit | нет | Признак фактического наличия экземпляра при инвентаризации |
| fQUANTITY | money | нет | Количество (факт) |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный `PK_ASSETSINVENTORYDETAILS` по `fISN` (неуникальный); индекс по `fASSETID`. Связи: `fISN→` шапка документа, `fASSETID→ASSETS.fID`.

---

## dbo.HITRANSFERREDASSETS  (42 строки)

- Назначение: регистр движений переданных активов (History) — журнал выдачи/возврата фирменного оборудования клиентам. Каждая строка фиксирует передачу актива клиенту (или со склада) с датой и документом-основанием.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции передачи/возврата |
| fSTORAGE | nvarchar(20) | нет | Код склада → `STORAGES.fCODE` (может быть пустым при передаче клиенту) |
| fCUSTOMERID | int | да | Код клиента-получателя → `CUSTOMERS.fID` |
| fASSETID | int | нет | Код актива → `ASSETS.fID` |
| fQUANTITY | money | нет | Количество |
| fTYPE | varchar(2) | нет | Тип движения/регистра (в образцах `01`); назначение кодов не установлено |
| fOP | varchar(3) | нет | Код операции: `TSR` — передача (transfer) и др. |
| fDBCR | varchar(1) | нет | Направление: `D` — выдача клиенту, `C` — возврат |
| fBASE | uniqueidentifier | нет | Ссылка на `fISN` документа-основания |
| fUSERID | int | нет | Пользователь операции → `USERS` |
| fASSETNUMBER | nvarchar(50) | нет | Инвентарный номер → `ASSETNUMBERS.fASSETNUMBER`; пусто, если не по номерам |

- Ключи и связи: кластерный `PK_HITRANSFERREDASSETS` по `(fTYPE, fCUSTOMERID, fASSETID, fASSETNUMBER, fDATE, fSTORAGE, fBASE)` — неуникальный. Индексы `(fTYPE, fBASE)`, `(fTYPE, fASSETID, fASSETNUMBER, fCUSTOMERID)`, `(fTYPE, fDATE, fSTORAGE)`. Связи: `fCUSTOMERID→CUSTOMERS.fID`, `fASSETID→ASSETS.fID`, `fSTORAGE→STORAGES.fCODE`, `fBASE→fISN` документа.

---

## dbo.HIRESTTRANSFERREDASSETS  (31 строка)

- Назначение: регистр остатков переданных активов (Rest) — текущее количество единиц оборудования, находящихся у каждого клиента (какое оборудование сейчас «на руках» у клиента).
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSTORAGE | nvarchar(20) | нет | Код склада → `STORAGES.fCODE` (обычно пусто для позиции у клиента) |
| fCUSTOMERID | int | да | Код клиента → `CUSTOMERS.fID` |
| fASSETID | int | нет | Код актива → `ASSETS.fID` |
| fTYPE | varchar(2) | нет | Тип регистра (в образцах `01`); назначение кодов не установлено |
| fQUANTITY | money | нет | Текущий остаток актива у клиента |
| fASSETNUMBER | nvarchar(50) | нет | Инвентарный номер экземпляра → `ASSETNUMBERS` |

- Ключи и связи: уникальный кластерный `PK_HIRESTTRANSFERREDASSETS` по `(fTYPE, fCUSTOMERID, fASSETID, fASSETNUMBER, fSTORAGE)`. Свёртка движений `HITRANSFERREDASSETS` по клиенту/активу/номеру.

---

## dbo.STORAGESACCESSBYUSERS  (114 строк)

- Назначение: матрица прав доступа пользователей к складам — какие операции (ввод, вывод, просмотр остатков, приём из перемещения) разрешены пользователю по каждому складу.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSTORAGE | nvarchar(20) | нет | Код склада → `STORAGES.fCODE` |
| fUSERID | int | нет | Код пользователя → `USERS` |
| fINPUT | bit | нет | Право прихода/ввода на склад |
| fOUTPUT | bit | нет | Право расхода/вывода со склада |
| fREMAINDER | bit | нет | Право просмотра остатков склада |
| fINPUTFROMMOVE | bit | нет | Право приёма товара из документов перемещения |

- Ключи и связи: уникальный кластерный `PK_STORAGESACCESSBYUSERS` по `(fSTORAGE, fUSERID)`; индекс по `fUSERID`. Связи: `fSTORAGE→STORAGES.fCODE`, `fUSERID→USERS`.

---

## dbo.PRODUCTMOVEBETWEENVANAGENTSDETAILS  (0 строк)

- Назначение: строки документов перемещения товара между ван-агентами (передача остатка «с борта на борт»). На момент выгрузки таблица пустая — функциональность заведена, но не используется.
- Колонки:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа перемещения |
| fPRODUCTID | int | нет | Код товара → `PRODUCTS.fID` |
| fQUANTITY | money | нет | Количество по позиции |
| fROWNUM | smallint | нет | Порядковый номер строки в документе |

- Ключи и связи: кластерный `PK_PRODUCTMOVEBETWEENVANAGENTSDETAILS` по `fISN` (неуникальный). Связи: `fISN→` шапка документа перемещения, `fPRODUCTID→PRODUCTS.fID`. Движения по такому документу отражаются в `HIAGENTPRODUCTS.fBASE = fISN` для агентов-источника и приёмника.

---

## Связи домена

Домен строится вокруг трёх пар «журнал → остаток» и справочников:

- Товар на складах: `PRODUCTACCOUNTINGDETAILS` (строки документов) → движения `HISTORAGES` (`fBASE = fISN`) → остатки `HIRESTSTORAGES`. Все три связаны с `STORAGES` по коду склада и с `PRODUCTS` по `fPRODUCTID`.
- Товар у ван-агентов: `PRODUCTMOVEBETWEENVANAGENTSDETAILS` и другие документы → движения `HIAGENTPRODUCTS` (`fBASE = fISN`) → остатки `HIRESTAGENTPRODUCTS`. Связаны с `SALESAGENTS` по `fAGENTID` и с `PRODUCTS` по `fPRODUCTID`.
- Активы (оборудование): справочник `ASSETS` + серийные номера `ASSETNUMBERS`; документы `ASSETACCOUNTINGDETAILS` / `ASSETSINVENTORYDETAILS`; передача клиентам — движения `HITRANSFERREDASSETS` (`fBASE = fISN`) → остатки `HIRESTTRANSFERREDASSETS`. Связаны с `CUSTOMERS` по `fCUSTOMERID`, с `ASSETS` по `fASSETID`, со `STORAGES` по коду склада.

Связи с соседними доменами (по общим ключам, явных FK нет):
- `PRODUCTS.fID` — товар (домены продаж и товарного каталога) ← `fPRODUCTID` во всех товарных таблицах.
- `SALESAGENTS.fID` — ван-агент/торговый представитель (домен продаж) ← `HIAGENTPRODUCTS.fAGENTID`, `HIRESTAGENTPRODUCTS.fAGENTID`.
- `CUSTOMERS.fID` — клиент (домен клиентов/долгов) ← `HITRANSFERREDASSETS.fCUSTOMERID`, `HIRESTTRANSFERREDASSETS.fCUSTOMERID`.
- `USERS` — оператор ← `fUSERID` в журналах и `STORAGESACCESSBYUSERS`.
- `fISN` во всех `...DETAILS` — ключ шапки первичного документа (шапки хранятся в общих документных таблицах ERP).

Общий инвариант регистров: остаток в `HIREST...` = сумма движений `HI...` по соответствующему ключу с учётом знака `fDBCR` (`D` — прибавляет, `C` — вычитает).

## Примеры отчётных запросов

Все запросы — только чтение (SELECT), по реально существующим колонкам.

1) Текущие остатки товаров по складам (с названиями склада и товара):

```sql
SELECT s.fNAME AS storage_name,
       p.fNAME AS product_name,
       r.fQUANTITY AS qty
FROM HIRESTSTORAGES r
INNER JOIN STORAGES s ON s.fCODE = r.fSTORAGE
INNER JOIN PRODUCTS p ON p.fID = r.fPRODUCTID
WHERE r.fQUANTITY <> 0
ORDER BY s.fNAME, p.fNAME;
```

2) Товар на руках у ван-агентов (ненулевые остатки):

```sql
SELECT ag.fNAME AS agent_name,
       p.fNAME  AS product_name,
       r.fQUANTITY AS qty
FROM HIRESTAGENTPRODUCTS r
INNER JOIN SALESAGENTS ag ON ag.fID = r.fAGENTID
INNER JOIN PRODUCTS   p  ON p.fID  = r.fPRODUCTID
WHERE r.fQUANTITY <> 0
ORDER BY ag.fNAME, p.fNAME;
```

3) Какое фирменное оборудование сейчас находится у клиентов:

```sql
SELECT c.fNAME AS customer_name,
       a.fNAME AS asset_name,
       r.fASSETNUMBER,
       r.fQUANTITY
FROM HIRESTTRANSFERREDASSETS r
INNER JOIN CUSTOMERS c ON c.fID = r.fCUSTOMERID
INNER JOIN ASSETS    a ON a.fID = r.fASSETID
WHERE r.fQUANTITY > 0
ORDER BY c.fNAME, a.fNAME;
```

4) Оборот товара по складу за период из журнала движений (приход/расход):

```sql
SELECT h.fSTORAGE,
       p.fNAME AS product_name,
       SUM(CASE WHEN h.fDBCR = 'D' THEN h.fQUANTITY ELSE 0 END) AS qty_in,
       SUM(CASE WHEN h.fDBCR = 'C' THEN h.fQUANTITY ELSE 0 END) AS qty_out
FROM HISTORAGES h
INNER JOIN PRODUCTS p ON p.fID = h.fPRODUCTID
WHERE h.fDATE >= '2024-01-01' AND h.fDATE < '2025-01-01'
GROUP BY h.fSTORAGE, p.fNAME
ORDER BY h.fSTORAGE, p.fNAME;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
