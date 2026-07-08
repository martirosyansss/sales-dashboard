# Товары, цены, скидки и акции

Домен описывает справочник товаров ERP AS-Sales Management 7 и всю связанную ценовую и маркетинговую механику: карточку товара с логистическими и налоговыми атрибутами, штрихкоды и единицы измерения, тару и комплекты (киты), прайс-листы (общие и индивидуальные по клиентам/группам), скидки (простые и накопительные/шкальные), подарочные акции, лимиты цен и скидок, классификаторы (CPA-коды, акциз), а также схемы ABC-анализа, доступа к товарам и депозитной тары. Ключевой сущностью является `dbo.PRODUCTS` (товар, `fID`), на который через `fPRODUCTID` ссылаются практически все остальные таблицы домена, а также строки продаж (`SALEDOCDETAILS.fPRODUCTID → PRODUCTS.fID`).

Общая модель ценообразования построена на шаблоне «право/условие»: строки задаются комбинацией измерений «тип клиента + клиент/группа клиентов» × «тип товара + товар/группа товаров», действуют в интервале дат `fDATE … fVALIDUNTIL`, помечаются флагом закрытия `fCLOSE`. Значения `fCUSTOMERTYPE`/`fPRODUCTTYPE` кодируют, применяется ли правило ко всем (`0`), к группе (`1`) или к конкретному объекту (`2`).

> Примечание о коллизии регистра: файл `docs/database/schema/tables/dbo.PRODUCTS.json` содержит демо-таблицу `dbo.Products` (mixed-case, колонки `ProductID/ProductName/UnitPrice`, 0 строк) — это НЕ боевая таблица. Боевой справочник — `dbo.PRODUCTS` (UPPERCASE, 791 строка), описанный ниже из `schema_raw.json`.

---

## dbo.PRODUCTS  (791 строка)
- Назначение: боевой справочник товаров — карточка товара с наименованием, кодом, единицами измерения, группами (учётной и скидочной), логистикой (вес/объём), налоговыми признаками (НДС, акциз, эко-сбор) и флагами поведения (продаётся, тара, кит, подарок, депозит и т. д.). Присоединяется к строкам продаж по `PRODUCTS.fID = SALEDOCDETAILS.fPRODUCTID`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | PK, суррогатный идентификатор товара; целевой ключ для fPRODUCTID во всём домене |
| fCODE | nvarchar(20) | нет | Уникальный код товара (артикул), уникальный индекс I_PRODUCTS2 |
| fNAME | nvarchar(250) | нет | Краткое наименование товара |
| fFULLNAME | nvarchar(250) | нет | Полное наименование товара |
| fMEASUREUNIT | nvarchar(6) | нет | Базовая единица измерения (код) |
| fGROUP | nvarchar(6) | нет | Код учётной/товарной группы (классификатор, индекс I_PRODUCTS3) |
| fDISCOUNTGROUP | nvarchar(6) | нет | Код скидочной группы товара (индекс I_PRODUCTS7); связывает с правилами скидок по группе товара |
| fHASFRACTION | bit | нет | Разрешено дробное количество |
| fFORSALE | bit | нет | Товар доступен к продаже |
| fCONTAINER | bit | нет | Товар является тарой |
| fGIFT | bit | нет | Товар является подарочным |
| fEXTERNALCODE | nvarchar(20) | нет | Внешний код (для интеграций) |
| fINCONTAINER | bit | нет | Товар поставляется в таре |
| fKIT | bit | нет | Товар является комплектом (китом) |
| fVATFREE | bit | нет | Освобождён от НДС |
| fTAXABLEBYEXCISE | bit | нет | Облагается акцизом |
| fEXCISECODE | nvarchar(6) | нет | Код акциза → EXCISETAXTARIFF.fEXCISECODE |
| fEXCISEFACTOR | money | нет | Коэффициент/фактор для расчёта акциза |
| fPRODUCER | nvarchar(6) | нет | Код производителя (справочник) |
| fCPACLASSIFIER | nvarchar(12) | нет | Код классификатора продукции → CPACODES.fCODE |
| fWEIGHT | money | нет | Вес единицы товара |
| fVOLUME | money | нет | Объём единицы товара |
| fVOLUMEINCAR | money | нет | Объём в транспортной единице |
| fADDITIONALUNITUSED | bit | нет | Используется дополнительная единица измерения |
| fADDITIONALUNIT | nvarchar(6) | нет | Код дополнительной единицы измерения |
| fADDITIONALUNITHASFRACTION | bit | нет | Дробность дополнительной единицы |
| fB2BPUBLISHED | bit | нет | Опубликован в B2B-канале |
| fCLOSED | bit | нет | Товар закрыт/архивирован (индекс I_PRODUCTS4); аналог «неактивен» |
| fISN | uniqueidentifier | нет | Альтернативный GUID-ключ товара (уникальный индекс I_PRODUCTS5) |
| fBODY | nvarchar(3000) | да | Описание/тело карточки |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fEXCHANGEABLEPRODUCTGROUP | nvarchar(6) | нет | Код группы взаимозаменяемых товаров |
| fBASEUNITQUANTITY | money | нет | Количество базовых единиц |
| fADDITIONALUNITQUANTITY | money | нет | Количество в дополнительной единице |
| fCONTAINERROUNDINGMETHOD | varchar(1) | да | Метод округления по таре |
| fTAXABLEBYENVFEE | bit | нет | Облагается экологическим сбором |
| fENVFEEPERCENT | money | нет | Процент эко-сбора |
| fSCALEDISCOUNTCONDITIONALPRODUCT | nvarchar(6) | нет | Код условного товара для шкальных скидок → PRODUCTSSCALEDISCOUNTS.fSCALEDISCOUNTCONDITIONALPRODUCT |
| fGIFTPROMOTIONCONDITIONALPRODUCT | nvarchar(6) | нет | Код условного товара для подарочных акций → GIFTPROMOTIONS.fGIFTPROMOTIONCONDITIONALPRODUCT |
| fRESERVABLE | bit | нет | Товар можно резервировать |
| fRETURNABLE | bit | нет | Товар возвратный |
| fDEPOSITABLE | bit | нет | Товар подлежит депозиту тары → DEPOSITSCHEMEDETAILS |
| fWEIGHTINCAR | money | нет | Вес в транспортной единице |
| fSPECIFICATION | nvarchar(1800) | нет | Спецификация/характеристики |
| fEXTERNALBODY | nvarchar(max) | да | Внешнее описание (для витрин/интеграций) |
| fRETURNSDISTRIBUTIONUSED | bit | нет | Используется распределение возвратов |
| fRETURNSDISTRIBUTIONMODE | nvarchar(4) | нет | Режим распределения возвратов |
| fDAYSQUANTITY | smallint | нет | Количество дней (срок/период, назначение уточняется в бизнес-настройках) |
| fSKIPDAYSQUANTITYUSED | bit | нет | Используется пропуск дней |
| fSKIPDAYSQUANTITY | smallint | нет | Количество пропускаемых дней |
| fMARKABLE | bit | нет | Товар подлежит маркировке |

- Ключи и связи: PK `fID` (кластерный PK_PRODUCTS1); уникальные ключи `fCODE`, `fISN`. Неявные связи: `fEXCISECODE→EXCISETAXTARIFF.fEXCISECODE`, `fCPACLASSIFIER→CPACODES.fCODE`, `fGROUP`/`fDISCOUNTGROUP` — коды справочников групп (TREES/TREEDEF); входящая ссылка `SALEDOCDETAILS.fPRODUCTID→fID`.

---

## dbo.BARCODES  (349 строк)
- Назначение: штрихкоды товаров с привязкой к единице измерения и коэффициентами пересчёта в базовую единицу; один товар может иметь несколько штрихкодов (для разных упаковок/единиц).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fMEASUREUNIT | nvarchar(6) | нет | Единица измерения данного штрихкода |
| fBARCODE | nvarchar(20) | нет | Значение штрихкода (индекс I_BARCODES2) |
| fBASEUNITQUANTITY | money | нет | Количество базовых единиц в этом штрихкоде |
| fMEASUREUNITQUANTITY | money | нет | Количество в указанной единице измерения |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK (кластерный) `fPRODUCTID, fBARCODE`. Неявная связь `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.PRODUCTCONTAINERS  (17 строк)
- Назначение: соответствие товара и его тары с коэффициентами вложенности (сколько товара в таре и сколько единиц тары).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fCONTAINERID | int | нет | Тара (товар-тара) → PRODUCTS.fID (индекс I_PRODUCTCONTAINERS2) |
| fPRODUCTQUANTITY | money | нет | Количество товара на единицу тары |
| fCONTAINERQUANTITY | money | нет | Количество единиц тары |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK (кластерный) `fPRODUCTID, fCONTAINERID`. Обе колонки-идентификатора ссылаются на PRODUCTS.fID (товар и его тара).

---

## dbo.PRODUCTIMAGES  (70 строк)
- Назначение: изображения товаров (хранятся в БД как varbinary), с признаком изображения по умолчанию.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fIMAGEGUID | uniqueidentifier | нет | GUID изображения |
| fIMAGE | varbinary(max) | нет | Бинарное содержимое изображения |
| fDEFAULT | bit | нет | Изображение по умолчанию для товара |

- Ключи и связи: кластерный индекс по `fPRODUCTID`; уникальный `fPRODUCTID, fDEFAULT`. Неявная связь `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.KITCOMPONENTS  (0 строк)
- Назначение: состав комплекта (кита) — из каких товаров-компонентов и в каком количестве собирается товар-кит; `fPRICEPART` задаёт долю цены компонента. Таблица пустая (функционал не используется в этой БД).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPRODUCTID | int | нет | Товар-кит → PRODUCTS.fID (fKIT=1) |
| fCOMPONENTID | int | нет | Товар-компонент → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество компонента в ките |
| fPRICEPART | money | нет | Доля цены, приходящаяся на компонент |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK (кластерный) `fPRODUCTID, fCOMPONENTID`. Обе колонки → PRODUCTS.fID.

---

## dbo.COMPLECTATIONDETAILS  (0 строк)
- Назначение: строки документов комплектации/разукомплектации — фактическая сборка кита из компонентов по документу (`fISN` — шапка документа). Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на шапку документа комплектации |
| fKITPRODUCTID | int | нет | Собираемый кит → PRODUCTS.fID |
| fCOMPONENTID | int | нет | Компонент → PRODUCTS.fID |
| fQUANTITY | money | нет | Количество компонента |
| fADDITIONALINFO | nvarchar(50) | нет | Дополнительная информация по строке |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи: `fISN→шапка документа`, `fKITPRODUCTID`/`fCOMPONENTID→PRODUCTS.fID`.

---

## dbo.PRICELISTS  (971 строка)
- Назначение: общие прайс-листы — цены товаров по типу прайс-листа на интервале дат; строки одного прайс-листа объединены общим `fISN`. Тип `fPRICELISTTYPE='01'` в данных — основной прайс.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор прайс-листа (шапка), индекс I_PRICELISTS2 |
| fPRICELISTTYPE | nvarchar(2) | нет | Тип прайс-листа (напр. '01') |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fPRICE | money | нет | Цена товара |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный ключ `fPRICELISTTYPE, fPRODUCTID, fDATE`. Неявная связь `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.PRICELISTDETAILS  (65 строк)
- Назначение: детальные строки конкретного прайс-листа (цена товара в рамках `fISN`); используется для наполнения/редактирования прайс-листа без интервала дат в самой строке.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ссылка на прайс-лист → PRICELISTS.fISN |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fPRICE | money | нет | Цена товара |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи: `fISN→PRICELISTS.fISN`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.CUSTOMERPRICELISTS  (64 557 строк)
- Назначение: индивидуальные (клиентские) прайс-листы — специальные цены товара для конкретного клиента или группы клиентов на интервале дат; крупнейшая ценовая таблица домена. Признак `fCLOSE` закрывает строку.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор строки/пакета прайса (индекс i_CUSTOMERPRICELISTS1) |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fCUSTOMERTYPE | varchar(1) | нет | Тип получателя: '1' — группа клиентов, '2' — конкретный клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID (при fCUSTOMERTYPE='2') |
| fCUSTOMERGROUP | nvarchar(6) | да | Код группы клиентов (при fCUSTOMERTYPE='1') |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fPRICE | money | нет | Специальная цена |
| fCLOSE | bit | нет | Строка закрыта/неактивна (индекс i_CUSTOMERPRICELISTS2) |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный ключ `fCUSTOMERTYPE, fCUSTOMERID, fCUSTOMERGROUP, fPRODUCTID, fDATE`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fCUSTOMERGROUP→CUSTOMERS.fGROUP`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.CUSTOMERPRICELISTDETAILS  (224 строки)
- Назначение: детальные строки клиентских прайс-листов (без интервала дат в строке); та же матрица «тип клиента × товар», что и CUSTOMERPRICELISTS, для ведения/редактирования.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор строки |
| fCUSTOMERTYPE | varchar(1) | нет | Тип получателя: '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERGROUP | nvarchar(6) | да | Код группы клиентов |
| fPRODUCTID | int | нет | Товар → PRODUCTS.fID |
| fPRICE | money | да | Специальная цена |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.PRODUCTSDISCOUNTS  (6 663 строки)
- Назначение: правила простых скидок в процентах, заданные матрицей «клиент/группа клиентов» × «товар/группа товаров» на интервале дат; `fSALESAGENTID=-1` означает системное правило. Крупнейшая скидочная таблица.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор правила (индекс I_PRODUCTSDISCOUNTS) |
| fSALESAGENTID | int | нет | Автор правила → SALESAGENTS.fID (-1 — система) |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа клиента |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара: '1' — группа товаров, '2' — товар |
| fPRODUCTID | int | да | Товар → PRODUCTS.fID (при fPRODUCTTYPE='2') |
| fPRODUCTDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа товара → PRODUCTS.fDISCOUNTGROUP (при fPRODUCTTYPE='1') |
| fDISCOUNT | money | нет | Размер скидки в процентах |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fCLOSE | bit | нет | Правило закрыто/неактивно (индекс I_PRODUCTSDISCOUNTS2) |

- Ключи и связи: кластерный ключ `fCUSTOMERTYPE, fCUSTOMERID, fCUSTOMERDISCOUNTGROUP, fPRODUCTTYPE, fPRODUCTID, fPRODUCTDISCOUNTGROUP, fDATE`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`, `fPRODUCTDISCOUNTGROUP→PRODUCTS.fDISCOUNTGROUP`, `fSALESAGENTID→SALESAGENTS`.

---

## dbo.PRODUCTSDISCOUNTSDETAILS  (204 строки)
- Назначение: детальные строки правил скидок (без интервала дат), задаваемые той же матрицей «клиент × товар/группа»; используется для ведения набора скидок в рамках одного `fISN`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор набора скидок |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа клиента |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара: '1' — группа, '2' — товар |
| fPRODUCTID | int | да | Товар → PRODUCTS.fID |
| fPRODUCTDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа товара |
| fDISCOUNT | money | нет | Размер скидки в процентах |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fCLOSE | bit | нет | Строка закрыта/неактивна |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи аналогичны PRODUCTSDISCOUNTS.

---

## dbo.PRODUCTSSCALEDISCOUNTS  (33 строки)
- Назначение: накопительные (шкальные) скидки — размер скидки зависит от достигнутого объёма по условному товару; порог задаётся базой расчёта (`fCALCULATIONBASE`: 'Sum' — по сумме, 'Qnt' — по количеству) и значением-порогом `fCALCULATIONVALUE`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор правила (индекс I_PRODUCTSSCALEDISCOUNTS2) |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERSCALEDISCOUNTGROUP | nvarchar(6) | да | Группа клиентов для шкальной скидки |
| fSCALEDISCOUNTCONDITIONALPRODUCT | nvarchar(6) | нет | Код условного товара → PRODUCTS.fSCALEDISCOUNTCONDITIONALPRODUCT |
| fCALCULATIONBASE | varchar(3) | нет | База расчёта порога: 'Sum' (сумма) / 'Qnt' (количество) |
| fCALCULATIONVALUE | money | нет | Порог, начиная с которого действует скидка |
| fDISCOUNT | money | нет | Размер скидки в процентах |
| fROWNUM | smallint | нет | Номер ступени шкалы |
| fCLOSE | bit | нет | Правило закрыто/неактивно (индекс I_PRODUCTSSCALEDISCOUNTS3) |
| fDISCOUNTTYPE | varchar(1) | нет | Тип скидки: '1' — процент (при fDISCOUNTAMOUNT=0) |
| fDISCOUNTAMOUNT | money | нет | Скидка суммой (альтернатива проценту) |

- Ключи и связи: кластерный ключ `fCUSTOMERTYPE, fCUSTOMERID, fCUSTOMERSCALEDISCOUNTGROUP, fSCALEDISCOUNTCONDITIONALPRODUCT, fDATE`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fSCALEDISCOUNTCONDITIONALPRODUCT→PRODUCTS.fSCALEDISCOUNTCONDITIONALPRODUCT`.

---

## dbo.PRODUCTSSCALEDISCOUNTSDETAILS  (13 строк)
- Назначение: детальные ступени шкальных скидок (без интервала дат), задают пары «порог → скидка» в рамках одного `fISN`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор набора ступеней |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERSCALEDISCOUNTGROUP | nvarchar(6) | да | Группа клиентов для шкальной скидки |
| fSCALEDISCOUNTCONDITIONALPRODUCT | nvarchar(6) | нет | Код условного товара |
| fCALCULATIONBASE | varchar(3) | нет | База расчёта: 'Sum' / 'Qnt' |
| fCALCULATIONVALUE | money | нет | Порог ступени |
| fDISCOUNT | money | нет | Размер скидки в процентах |
| fROWNUM | smallint | нет | Номер ступени |
| fDISCOUNTTYPE | varchar(1) | нет | Тип скидки ('1' — процент) |
| fDISCOUNTAMOUNT | money | нет | Скидка суммой |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи аналогичны PRODUCTSSCALEDISCOUNTS.

---

## dbo.GIFTPROMOTIONS  (982 строки)
- Назначение: подарочные акции — при достижении условия по объёму (`fCALCULATIONBASE`/`fCALCULATIONVALUE`) клиенту начисляется подарочный товар `fGIFTID` в количестве `fGIFTQUANTITY`. Условие задаётся матрицей «клиент/группа × товар/условный товар».

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор акции (индекс I_GIFTPROMOTIONS2) |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '0' — все, '1' — группа, '2' — клиент |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERGIFTPROMOTIONGROUP | nvarchar(6) | да | Группа клиентов для акции |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара-условия: '1' — группа/условный товар, '2' — товар |
| fPRODUCTID | int | да | Товар-условие → PRODUCTS.fID (при fPRODUCTTYPE='2') |
| fGIFTPROMOTIONCONDITIONALPRODUCT | nvarchar(6) | да | Код условного товара → PRODUCTS.fGIFTPROMOTIONCONDITIONALPRODUCT |
| fCALCULATIONBASE | varchar(3) | нет | База условия: 'Qnt' (количество) / 'Sum' (сумма) |
| fCALCULATIONVALUE | money | нет | Порог срабатывания акции |
| fGIFTID | int | нет | Подарочный товар → PRODUCTS.fID (обычно fGIFT=1) |
| fGIFTQUANTITY | money | нет | Количество подарка |
| fROWNUM | smallint | нет | Порядковый номер строки |
| fCLOSE | bit | нет | Акция закрыта/неактивна |

- Ключи и связи: кластерный ключ `fCUSTOMERTYPE, fCUSTOMERID, fCUSTOMERGIFTPROMOTIONGROUP, fPRODUCTTYPE, fPRODUCTID, fGIFTPROMOTIONCONDITIONALPRODUCT, fDATE, fCALCULATIONBASE, fCALCULATIONVALUE`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID`/`fGIFTID→PRODUCTS.fID`. Фактически выданные подарки фиксируются в SALEDOCGIFTS.

---

## dbo.GIFTPROMOTIONDETAILS  (12 строк)
- Назначение: детальные строки подарочных акций (без интервала дат), задают набор условий «объём → подарок» в рамках одного `fISN`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор набора условий |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента: '0'/'1'/'2' |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERGIFTPROMOTIONGROUP | nvarchar(6) | да | Группа клиентов для акции |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара-условия: '1' / '2' |
| fPRODUCTID | int | да | Товар-условие → PRODUCTS.fID |
| fGIFTPROMOTIONCONDITIONALPRODUCT | nvarchar(6) | да | Код условного товара |
| fCALCULATIONBASE | varchar(3) | нет | База условия: 'Qnt' / 'Sum' |
| fCALCULATIONVALUE | money | нет | Порог срабатывания |
| fGIFTID | int | нет | Подарочный товар → PRODUCTS.fID |
| fGIFTQUANTITY | money | нет | Количество подарка |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный индекс по `fISN`. Неявные связи аналогичны GIFTPROMOTIONS.

---

## dbo.PRICESORDISCOUNTSLIMITS  (0 строк)
- Назначение: лимиты (мин/макс) на цены или скидки по матрице «клиент/группа × товар/группа»; ограничивают допустимый диапазон при продаже. Таблица пустая (лимиты не заданы).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Идентификатор правила (индекс I_PRICESORDISCOUNTSLIMITS1) |
| fDATE | smalldatetime | нет | Дата начала действия |
| fVALIDUNTIL | smalldatetime | нет | Дата окончания действия |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERGROUP | nvarchar(6) | да | Группа клиентов |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара |
| fPRODUCTID | int | да | Товар → PRODUCTS.fID |
| fPRODUCTGROUP | nvarchar(6) | да | Группа товаров |
| fLIMITTYPE | varchar(1) | нет | Тип лимита (цена/скидка, мин/макс) |
| fLIMIT | money | нет | Значение лимита |
| fSTATE | tinyint | нет | Состояние правила |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный ключ `fISN, fROWNUM`. Неявные связи: `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.PRODDISCCHANGEREQUESTS  (0 строк)
- Назначение: заявки торговых агентов на изменение скидки по товару/группе для клиента/группы; проходят согласование (`fSTATE`, `fCLOSED`). Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | PK заявки |
| fCREATIONDATE | datetime | нет | Дата создания заявки |
| fSALESAGENTID | int | нет | Автор → SALESAGENTS.fID (индекс I1) |
| fDATE | datetime | нет | Дата начала запрашиваемого действия |
| fVALIDUNTIL | datetime | нет | Дата окончания запрашиваемого действия |
| fSTATE | smallint | нет | Состояние согласования заявки |
| fCUSTOMERTYPE | varchar(1) | нет | Уровень клиента |
| fCUSTOMERID | int | да | Клиент → CUSTOMERS.fID |
| fCUSTOMERDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа клиента |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара |
| fPRODUCTID | int | да | Товар → PRODUCTS.fID |
| fPRODUCTDISCOUNTGROUP | nvarchar(6) | да | Скидочная группа товара |
| fDISCOUNT | money | нет | Запрашиваемый размер скидки (%) |
| fCLOSED | bit | нет | Заявка закрыта |

- Ключи и связи: PK `fID`. Неявные связи: `fSALESAGENTID→SALESAGENTS`, `fCUSTOMERID→CUSTOMERS.fID`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.CPACODES  (1 536 строк)
- Назначение: классификатор продукции по видам деятельности (CPA/КВЭД-подобный справочник) — код и наименование; на него ссылается PRODUCTS.fCPACLASSIFIER.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(12) | нет | PK — код классификатора (напр. '01.61') |
| fCAPTION | nvarchar(255) | нет | Наименование классификатора |
| fTYPE | char(1) | нет | Тип записи классификатора |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fMARKINITDATE | smalldatetime | да | Дата инициализации маркировки |

- Ключи и связи: PK `fCODE`. Входящая связь `PRODUCTS.fCPACLASSIFIER→fCODE`.

---

## dbo.EXCISETAXTARIFF  (0 строк)
- Назначение: тарифы акцизного налога по коду акциза на интервале дат (процент и/или минимальная сумма). Таблица пустая (тарифы не заведены).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата начала действия тарифа |
| fEXCISECODE | nvarchar(6) | нет | Код акциза → PRODUCTS.fEXCISECODE |
| fTARIFFPERCENT | money | нет | Ставка акциза в процентах |
| fTARIFFMINSUM | money | нет | Минимальная сумма акциза |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fEXCISECODE, fDATE`. Входящая связь `PRODUCTS.fEXCISECODE→fEXCISECODE`.

---

## dbo.ABCSCHEMES  (1 строка)
- Назначение: схемы ABC-анализа товаров (шапка) — код и наименование схемы классификации по вкладу.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | PK — код схемы (напр. '000') |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fCODE`. Связь с ABCSCHEMEDETAILS по `fCODE→fSCHEMECODE`.

---

## dbo.ABCSCHEMEDETAILS  (3 строки)
- Назначение: пороги классов ABC внутри схемы — какой процент вклада относит товар к классу A/B/C (напр. A=80%, B=15%, C=5%).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Схема → ABCSCHEMES.fCODE |
| fCLASS | nvarchar(1) | нет | Класс: 'A' / 'B' / 'C' |
| fPERCENT | money | нет | Пороговый процент вклада для класса |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK `fSCHEMECODE, fCLASS`. Неявная связь `fSCHEMECODE→ABCSCHEMES.fCODE`.

---

## dbo.PRODUCTSACCESSSCHEMES  (0 строк)
- Назначение: схемы доступа к товарам (шапка) — код и наименование схемы, определяющей, какие товары/группы доступны. Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | PK — код схемы доступа |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fCODE`. Связь с PRODUCTSACCESSSCHEMEDETAILS по `fCODE→fSCHEMECODE`.

---

## dbo.PRODUCTSACCESSSCHEMEDETAILS  (0 строк)
- Назначение: детализация схемы доступа — разрешён (`fACCESS`) или запрещён доступ к товару/группе товаров в рамках схемы. Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Схема → PRODUCTSACCESSSCHEMES.fCODE |
| fPRODUCTTYPE | varchar(1) | нет | Уровень товара: группа/товар |
| fPRODUCTID | int | да | Товар → PRODUCTS.fID |
| fPRODUCTGROUP | nvarchar(6) | да | Группа товаров |
| fACCESS | bit | нет | Признак доступа (разрешён/запрещён) |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: PK `fSCHEMECODE, fPRODUCTTYPE, fPRODUCTID, fPRODUCTGROUP`. Неявные связи: `fSCHEMECODE→PRODUCTSACCESSSCHEMES.fCODE`, `fPRODUCTID→PRODUCTS.fID`.

---

## dbo.DEPOSITSCHEMES  (0 строк)
- Назначение: схемы депозита тары (шапка) — код и наименование схемы депозитных товаров. Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | PK — код схемы депозита |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fCODE`. Связь с DEPOSITSCHEMEDETAILS по `fCODE→fSCHEMECODE`.

---

## dbo.DEPOSITSCHEMEDETAILS  (0 строк)
- Назначение: состав схемы депозита — перечень депозитных товаров (тары), входящих в схему. Таблица пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Схема → DEPOSITSCHEMES.fCODE |
| fPRODUCTID | int | нет | Депозитный товар → PRODUCTS.fID (обычно fDEPOSITABLE=1) |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: кластерный ключ `fSCHEMECODE, fPRODUCTID`. Неявные связи: `fSCHEMECODE→DEPOSITSCHEMES.fCODE`, `fPRODUCTID→PRODUCTS.fID`.

---

## Связи домена

Центр домена — `dbo.PRODUCTS.fID`. Все дочерние и вспомогательные таблицы соединяются с ним через `fPRODUCTID` (либо `fGIFTID`, `fCOMPONENTID`, `fCONTAINERID` — тоже PRODUCTS.fID):

- Атрибуты товара: `BARCODES`, `PRODUCTCONTAINERS`, `PRODUCTIMAGES`, `KITCOMPONENTS`, `COMPLECTATIONDETAILS` — все по `fPRODUCTID→PRODUCTS.fID` (тара и компоненты — вторые ссылки на PRODUCTS).
- Ценообразование: `PRICELISTS`/`PRICELISTDETAILS` (общие прайсы, связаны между собой по `fISN`), `CUSTOMERPRICELISTS`/`CUSTOMERPRICELISTDETAILS` (индивидуальные прайсы). Все по `fPRODUCTID→PRODUCTS.fID`; клиентские дополнительно по `fCUSTOMERID→CUSTOMERS.fID` и `fCUSTOMERGROUP→CUSTOMERS.fGROUP`.
- Скидки: `PRODUCTSDISCOUNTS`/`PRODUCTSDISCOUNTSDETAILS` (простые %), `PRODUCTSSCALEDISCOUNTS`/`...DETAILS` (шкальные), лимиты `PRICESORDISCOUNTSLIMITS`, заявки `PRODDISCCHANGEREQUESTS`. Матрица измерений: `fCUSTOMERTYPE`+(`fCUSTOMERID`|`fCUSTOMERDISCOUNTGROUP`) × `fPRODUCTTYPE`+(`fPRODUCTID`|`fPRODUCTDISCOUNTGROUP`). `fPRODUCTDISCOUNTGROUP` соединяется с `PRODUCTS.fDISCOUNTGROUP`.
- Акции: `GIFTPROMOTIONS`/`GIFTPROMOTIONDETAILS` — условие по `fPRODUCTID`/условному товару, подарок по `fGIFTID→PRODUCTS.fID`. Фактически выданные подарки в продажах — таблица `SALEDOCGIFTS` (соседний домен продаж), товар-условие для шкал/акций — коды `PRODUCTS.fSCALEDISCOUNTCONDITIONALPRODUCT` / `fGIFTPROMOTIONCONDITIONALPRODUCT`.
- Классификаторы и налоги: `PRODUCTS.fCPACLASSIFIER→CPACODES.fCODE`; `PRODUCTS.fEXCISECODE→EXCISETAXTARIFF.fEXCISECODE`.
- Схемы: `ABCSCHEMES`→`ABCSCHEMEDETAILS` (по `fCODE=fSCHEMECODE`), `PRODUCTSACCESSSCHEMES`→`PRODUCTSACCESSSCHEMEDETAILS`, `DEPOSITSCHEMES`→`DEPOSITSCHEMEDETAILS`; детали схем ссылаются на товары по `fPRODUCTID→PRODUCTS.fID`.

Связь с соседними доменами: строки продаж `SALEDOCDETAILS.fPRODUCTID→PRODUCTS.fID` (в app_v2.py: `LEFT/INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID`, где `sd.fDISCOUNT` хранит процент скидки, а `sd.fDISCOUNTEDPRICE`/`sd.fSUM` — цену со скидкой и сумму строки). Товарные регистры движения/остатков: `HISOLDPRODUCTS`/`HIRESTSOLDPRODUCTS`, `HIAGENTPRODUCTS`/`HIRESTAGENTPRODUCTS`, `HIDEPOSITPRODUCTS`/`HIRESTDEPOSITPRODUCTS` — по `fPRODUCTID→PRODUCTS.fID`. Группа товара `PRODUCTS.fGROUP` и группа клиента `CUSTOMERS.fGROUP` расшифровываются через справочник `TREES` (например `TREES.fCODE = fGROUP AND fTREEID = 'CustGrp'`).

---

## Примеры отчётных запросов

Все запросы — только чтение (SELECT).

### 1. Действующие общие цены товаров на заданную дату
```sql
SELECT p.fCODE, p.fNAME, pl.fPRICELISTTYPE, pl.fPRICE,
       pl.fDATE, pl.fVALIDUNTIL
FROM PRICELISTS pl
INNER JOIN PRODUCTS p ON p.fID = pl.fPRODUCTID
WHERE '2025-06-01' BETWEEN pl.fDATE AND pl.fVALIDUNTIL
  AND p.fCLOSED = 0
ORDER BY p.fNAME;
```

### 2. Активные простые скидки по товарам с расшифровкой товара
```sql
SELECT p.fCODE, p.fNAME,
       d.fCUSTOMERTYPE, d.fCUSTOMERDISCOUNTGROUP, d.fCUSTOMERID,
       d.fPRODUCTTYPE, d.fPRODUCTDISCOUNTGROUP,
       d.fDISCOUNT AS DiscountPercent,
       d.fDATE, d.fVALIDUNTIL
FROM PRODUCTSDISCOUNTS d
LEFT JOIN PRODUCTS p ON p.fID = d.fPRODUCTID
WHERE d.fCLOSE = 0
  AND GETDATE() BETWEEN d.fDATE AND d.fVALIDUNTIL
ORDER BY d.fDISCOUNT DESC;
```

### 3. Топ товаров по продажам за период с товарной группой
```sql
SELECT p.fCODE, p.fNAME, p.fGROUP,
       SUM(sd.fQUANTITY) AS Qty,
       SUM(sd.fSUM)      AS Amount
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON sd.fISN = s.fISN
INNER JOIN PRODUCTS p        ON p.fID = sd.fPRODUCTID
WHERE s.fSTATE = 2
  AND s.fDATE >= '2025-01-01' AND s.fDATE < '2025-02-01'
GROUP BY p.fCODE, p.fNAME, p.fGROUP
ORDER BY Amount DESC;
```

### 4. Действующие подарочные акции с товаром-условием и подарком
```sql
SELECT cond.fCODE  AS ConditionProductCode,
       cond.fNAME  AS ConditionProductName,
       g.fCALCULATIONBASE, g.fCALCULATIONVALUE,
       gift.fCODE  AS GiftProductCode,
       gift.fNAME  AS GiftProductName,
       g.fGIFTQUANTITY, g.fDATE, g.fVALIDUNTIL
FROM GIFTPROMOTIONS g
LEFT JOIN PRODUCTS cond ON cond.fID = g.fPRODUCTID
INNER JOIN PRODUCTS gift ON gift.fID = g.fGIFTID
WHERE g.fCLOSE = 0
  AND GETDATE() BETWEEN g.fDATE AND g.fVALIDUNTIL
ORDER BY g.fVALIDUNTIL;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
