# Документооборот, ЭДО и налоговые

Домен описывает универсальный документооборот AS-Sales Management 7: единую таблицу шапок документов всех типов (`DOCUMENTS`), их иерархию «родитель→потомок» (`DOCPARENTS`), полный журнал изменений и операций (`DOCUMENTSLOG`), плагины расширения бизнес-логики документов (`DOCUMENTEXTENSIONS`) и шаблоны печатных форм (`TEMPLATES`). Отдельный контур — электронный документооборот и налоговые: реквизиты налоговых накладных купли-продажи (`SALETAXDOCUMENTKEYS`) и перемещения товара (`PRODUCTSMOVETAXDOCUMENTKEYS`), построчная детализация налоговых документов (`TAXDOCUMENTDETAILS`), электронные товарно-транспортные накладные (`GEWAYBILLS`), коды маркировки товара (`MARKINGS`), файловые вложения (`ATTACHMENTS`) и SMS-уведомления клиентам (`SMS`).

`DOCUMENTS` — центральная таблица всей ERP: именно к ней по `fISN` привязаны движения долга (`HICUSTOMERSDEBT.fDEBTDOCISN → DOCUMENTS.fISN`), поэтому в аналитическом слое `app_v2.py` она используется десятками JOIN'ов для получения клиента, территории и даты документа-основания долга (см. `DEBT_CALCULATION_FORMULA.md`). Остальные таблицы домена в дашборде не задействованы; назначение их колонок выведено из схемы, образцов данных (`samples`), соглашений об именовании ERP и, где возможно, из кода `app_v2.py`. Там, где смысл не подтверждается данными, указано «назначение не установлено».

Общие соглашения домена:
- `fISN` (uniqueidentifier) — суррогатный ключ шапки документа; строки, регистры и связанные таблицы ссылаются на документ по `fISN`.
- `fDOCTYPE` (tinyint) — тип документа. По образцам данных и `TEMPLATES`: `1` — Վաճառքի պատвեր (заказ на продажу / SalesOrder), `2` — Վаճառք (продажа / Sale), `10` — плановый маршрут (`PLANNEDROUTESLIST`, посещения). Полный справочник типов задаётся системой AS-Sales.
- `fDOCSTATE` / `fSTATE` (tinyint) — состояние документа (в образцах `2` — проведён/действующий).
- Неявные связи: `fCUSTOMERID → CUSTOMERS.fID`, `fPRODUCTID → PRODUCTS.fID`, `fSALESAGENTID → SALESAGENTS`, `fSALESAREA`/`fDIVISION` — коды справочников (`TREES`/`TREEDEF`), `fUSERID`/`fCREATORUSERID`/`fLASTMODIFIERID` — пользователи системы.

---

## dbo.DOCUMENTS  (1 232 337 строк)

- Назначение: единая таблица шапок документов всех типов ERP (заказы, продажи, плановые маршруты и т.д.). Хранит номер, дату, сумму, клиента, агента, территорию и сериализованное тело документа; служит основанием для движений долга и товародвижения.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ документа (кластерный PK) |
| fDOCTYPE | tinyint | нет | Тип документа (1 — заказ, 2 — продажа, 10 — маршрут и др.) |
| fDOCSTATE | tinyint | нет | Состояние документа (2 — проведён) |
| fDATE | smalldatetime | нет | Дата документа |
| fDOCNUM | nvarchar(12) | нет | Номер документа (уникален в разрезе fDOCTYPE) |
| fSUMM | money | нет | Итоговая сумма документа |
| fCOMMENT | nvarchar(255) | нет | Комментарий |
| fBODY | nvarchar(max) | нет | Сериализованное тело документа (ключ:значение — VANAGENTID, DELIVERYCAR, PRICELISTTYPE и т.д.) |
| fEXTBODY | nvarchar(3000) | да | Дополнительное тело/расширение документа (XML) |
| fSALESAGENTID | int | нет | Торговый агент, → SALESAGENTS |
| fCUSTOMERID | int | нет | Клиент, → CUSTOMERS.fID |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fSALESAREA | nvarchar(6) | нет | Код территории/области сбыта (TREES) |
| fSPEC | nvarchar(100) | нет | Спецификация/доп. признак документа |
| fLASTMODIFYDATE | datetime | нет | Дата последнего изменения |
| fLASTMODIFIERID | int | нет | Пользователь последнего изменения |
| fCREATIONDATE | datetime | нет | Дата создания записи |
| fCREATORUSERID | int | нет | Пользователь-создатель |
| fTS | timestamp | нет | Версия строки (rowversion) для контроля конкурентности |

- Ключи и связи: PK кластерный `fISN`; уникальный индекс `(fDOCTYPE, fDOCNUM)`. Неявные: `fCUSTOMERID → CUSTOMERS.fID`, `fSALESAGENTID → SALESAGENTS`, `fSALESAREA`/`fDIVISION → TREES`. На `fISN` ссылаются `HICUSTOMERSDEBT.fDEBTDOCISN`, `DOCPARENTS.fISN`/`fPARENTISN`, `DOCUMENTSLOG.fISN`, `PLANNEDROUTESLIST.fISN`, налоговые таблицы (`fDOCISN`), `ATTACHMENTS`, `SMS.fBASEDOCISN`.

## dbo.DOCPARENTS  (671 390 строк)

- Назначение: иерархия/связь документов «потомок → родитель» (например, продажа `fDOCTYPE=2`, порождённая из заказа `fPARENTDOCTYPE=1`). Позволяет прослеживать цепочку создания документов.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Документ-потомок, → DOCUMENTS.fISN |
| fDOCTYPE | tinyint | нет | Тип документа-потомка |
| fPARENTISN | uniqueidentifier | нет | Документ-родитель, → DOCUMENTS.fISN |
| fPARENTDOCTYPE | tinyint | нет | Тип документа-родителя |

- Ключи и связи: кластерный уникальный индекс `(fISN, fPARENTISN)`. Обе колонки-ISN → `DOCUMENTS.fISN`. В образцах связка продажа(2)→заказ(1).

## dbo.DOCUMENTSLOG  (1 951 948 строк)

- Назначение: журнал операций и изменений над документами — кто, когда, с какого компьютера и какую операцию выполнил над документом; хранит текстовое описание изменений.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Документ, → DOCUMENTS.fISN |
| fDATE | datetime | нет | Дата/время операции (default getdate()) |
| fUSERID | int | нет | Пользователь, выполнивший операцию |
| fSTATE | tinyint | нет | Состояние документа на момент операции |
| fOP | tinyint | нет | Код операции (1 — создание, 2 — правка, 4 — проведение и т.п.) |
| fCOMMENT | nvarchar(255) | да | Комментарий к операции |
| fCOMPNAME | nvarchar(32) | нет | Имя компьютера/рабочей станции |
| fCHANGEDESCRIPTION | nvarchar(max) | да | Детальное описание изменений |
| fSOURCE | smallint | нет | Источник операции (канал/подсистема) |

- Ключи и связи: кластерный PK `(fISN, fDATE, fOP)`, индекс по `fUSERID`. `fISN → DOCUMENTS.fISN`, `fUSERID → пользователи системы`.

## dbo.DOCUMENTEXTENSIONS  (1 строка)

- Назначение: реестр программных расширений (плагинов) бизнес-логики документов — именованный скрипт (C#-класс), выполняемый для типа документа. В образце запись `SalesOrder` с CDATA-скриптом класса `DocumentExtention`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(32) | нет | Имя расширения (кластерный PK), напр. 'SalesOrder' |
| fEXTENSION | nvarchar(max) | да | Тело расширения (XML со встроенным скриптом) |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: кластерный PK `fNAME`. Явных FK нет; имя соотносится с типом документа по бизнес-логике приложения.

## dbo.TEMPLATES  (66 строк)

- Назначение: шаблоны печатных форм и отчётов документов (XML/DOCX), хранимые как двоичный BLOB, с привязкой к типу документа и путём к исходному файлу. Определяют, как печатается каждый тип документа.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(20) | нет | Код шаблона (часть PK), напр. '01', '02' |
| fCAPTION | nvarchar(50) | нет | Наименование шаблона (напр. «Վаճառքի պատвեր») |
| fTEMPLATE | varbinary(max) | нет | Двоичное тело шаблона (XML/DOCX) |
| fFILE | nvarchar(512) | нет | Путь к исходному файлу шаблона |
| fDOCTYPE | tinyint | нет | Тип документа, к которому применим шаблон |
| fENABLED | bit | нет | Признак активности шаблона |
| fSYSTEM | bit | нет | Системный (поставляемый) шаблон |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fTYPE | char(1) | нет | Тип шаблона (часть PK; 'D' — документ) |

- Ключи и связи: кластерный PK `(fTYPE, fNAME)`. `fDOCTYPE` соотносится с `DOCUMENTS.fDOCTYPE`.

## dbo.TAXDOCUMENTDETAILS  (35 строк)

- Назначение: построчная детализация налоговых документов — соответствие строки товара налогового документа исходному документу-основанию и товару, с кодом налоговой операции (напр. 'RLZ' — реализация).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fTAXDOCISN | uniqueidentifier | нет | Налоговый документ, → DOCUMENTS.fISN |
| fTAXPRODUCTROWISN | uniqueidentifier | нет | ISN строки товара налогового документа (кластерный PK) |
| fDOCISN | uniqueidentifier | нет | Документ-основание, → DOCUMENTS.fISN |
| fPRODUCTID | int | нет | Товар, → PRODUCTS.fID |
| fOPERATION | varchar(3) | нет | Код налоговой операции (напр. 'RLZ' — реализация) |

- Ключи и связи: кластерный PK `fTAXPRODUCTROWISN`; уникальный индекс `(fDOCISN, fPRODUCTID, fOPERATION)`. `fTAXDOCISN`/`fDOCISN → DOCUMENTS.fISN`, `fPRODUCTID → PRODUCTS.fID`.

## dbo.SALETAXDOCUMENTKEYS  (0 строк)

- Назначение: реквизиты (ключи) налоговых накладных купли-продажи для ЭДО — итоговая сумма, налоговый код и паспорт контрагента, адрес деятельности, номер родительской накладной. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ записи (кластерный PK), → DOCUMENTS.fISN |
| fTOTALSUM | money | нет | Итоговая сумма налоговой накладной |
| fTAXCODE | nvarchar(20) | нет | Налоговый код (ИНН/ՀՎՀՀ) контрагента |
| fPASSPORT | nvarchar(50) | нет | Паспортные данные контрагента |
| fBUSINESSADDRESS | nvarchar(4000) | нет | Адрес места деятельности |
| fPARENTINVOICENUMBER | nvarchar(11) | нет | Номер родительской налоговой накладной |
| fDATE | smalldatetime | нет | Дата налоговой накладной |

- Ключи и связи: кластерный PK `fISN` (→ `DOCUMENTS.fISN`).

## dbo.PRODUCTSMOVETAXDOCUMENTKEYS  (0 строк)

- Назначение: реквизиты налоговых документов перемещения товара (внутренние перемещения/поставки) — адреса поставки и доставки, общее количество и число строк. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Ключ записи (кластерный PK), → DOCUMENTS.fISN |
| fDATE | smalldatetime | нет | Дата документа перемещения |
| fSUPPLYLOCATION | nvarchar(4000) | нет | Адрес/место поставки (откуда) |
| fCONVEYLOCATION | nvarchar(4000) | нет | Адрес/место доставки (куда) |
| fTOTALQUANTITY | money | нет | Общее количество перемещаемого товара |
| fROWSCOUNT | int | нет | Количество строк в документе |

- Ключи и связи: кластерный PK `fISN` (→ `DOCUMENTS.fISN`).

## dbo.GEWAYBILLS  (0 строк)

- Назначение: электронные товарно-транспортные накладные (e-waybill) — привязка документа к номеру и идентификатору электронной накладной в государственной системе ЭДО. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDOCISN | uniqueidentifier | нет | Документ, → DOCUMENTS.fISN (кластерный PK) |
| fWAYBILLNUMBER | nvarchar(13) | нет | Номер электронной накладной |
| fWAYBILLID | nvarchar(10) | нет | Идентификатор электронной накладной в системе ЭДО |

- Ключи и связи: кластерный PK `fDOCISN` (→ `DOCUMENTS.fISN`).

## dbo.MARKINGS  (0 строк)

- Назначение: коды обязательной маркировки товара (напр. Data Matrix / GS1) в разрезе документа и товарной позиции. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDOCISN | uniqueidentifier | нет | Документ, → DOCUMENTS.fISN |
| fPRODUCTID | int | нет | Товар, → PRODUCTS.fID |
| fMARK | nvarchar(110) | нет | Код маркировки товарной единицы |

- Ключи и связи: некластерный индекс `(fDOCISN, fPRODUCTID)`. `fDOCISN → DOCUMENTS.fISN`, `fPRODUCTID → PRODUCTS.fID`.

## dbo.ATTACHMENTS  (0 строк)

- Назначение: файловые вложения и ссылки, прикреплённые к документам/объектам системы — сам файл (BLOB) либо ссылка, с типом, описанием и метаданными изменения. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Объект-владелец вложения (часть PK), → DOCUMENTS.fISN |
| fTYPE | tinyint | нет | Тип вложения (часть PK) |
| fLINK | nvarchar(256) | нет | Ссылка/путь вложения (часть PK) |
| fFILE | varbinary(max) | да | Двоичное содержимое файла |
| fPERSISTOBJECTTYPE | tinyint | нет | Тип объекта-владельца (класс сущности) |
| fDESCR | nvarchar(50) | нет | Описание вложения |
| fLASTMODIFYDATE | datetime | нет | Дата изменения (default getdate()) |
| fUSERID | int | нет | Пользователь, изменивший вложение |
| fCOMPNAME | nvarchar(32) | нет | Имя компьютера/рабочей станции |
| fROWNUM | smallint | нет | Порядковый номер строки вложения |

- Ключи и связи: кластерный PK `(fISN, fTYPE, fLINK)`. `fISN` соотносится с документом/объектом (тип — по `fPERSISTOBJECTTYPE`).

## dbo.SMS  (0 строк)

- Назначение: очередь и журнал SMS-уведомлений клиентам на основании документов (напр. подтверждение продажи) — номер, текст, статус отправки и ссылка на документ-основание. На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | нет | Идентификатор SMS (кластерный PK) |
| fISN | uniqueidentifier | нет | Уникальный ISN записи SMS |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fDATE | smalldatetime | нет | Дата постановки в очередь |
| fSENTDATE | smalldatetime | да | Дата фактической отправки |
| fPHONENUMBER | nvarchar(16) | нет | Номер телефона получателя |
| fBASEDOCISN | uniqueidentifier | да | Документ-основание, → DOCUMENTS.fISN |
| fBASEDOCTYPE | tinyint | да | Тип документа-основания |
| fBASEDOCNUM | nvarchar(12) | нет | Номер документа-основания |
| fCUSTOMERID | int | нет | Клиент-получатель, → CUSTOMERS.fID |
| fISSENT | bit | нет | Признак отправки |
| fMESSAGE | nvarchar(1000) | нет | Текст сообщения |
| fCOMMENT | nvarchar(50) | нет | Комментарий |

- Ключи и связи: кластерный PK `fID`, уникальный индекс по `fISN`. `fBASEDOCISN → DOCUMENTS.fISN`, `fCUSTOMERID → CUSTOMERS.fID`.

## Связи домена

- **Ядро — `DOCUMENTS.fISN`.** Все остальные таблицы домена и соседних доменов ссылаются на шапку документа именно по `fISN`:
  - Иерархия: `DOCPARENTS.fISN`/`fPARENTISN → DOCUMENTS.fISN` (цепочки заказ→продажа).
  - Аудит: `DOCUMENTSLOG.fISN → DOCUMENTS.fISN`.
  - Налоговые/ЭДО: `TAXDOCUMENTDETAILS.fTAXDOCISN`/`fDOCISN`, `SALETAXDOCUMENTKEYS.fISN`, `PRODUCTSMOVETAXDOCUMENTKEYS.fISN`, `GEWAYBILLS.fDOCISN`, `MARKINGS.fDOCISN → DOCUMENTS.fISN`.
  - Вложения/уведомления: `ATTACHMENTS.fISN`, `SMS.fBASEDOCISN → DOCUMENTS.fISN`.
- **Связь с финансами (долг).** Регистр `HICUSTOMERSDEBT.fDEBTDOCISN → DOCUMENTS.fISN` — именно так каждое движение долга получает клиента (`DOCUMENTS.fCUSTOMERID`), территорию (`fSALESAREA`) и дату. Это основной путь использования домена в `app_v2.py` (см. `DEBT_CALCULATION_FORMULA.md`).
- **Связь с посещениями.** `DOCUMENTS` с `fDOCTYPE=10` (плановые маршруты) соединяется с `PLANNEDROUTESLIST.fISN` для расчёта план/факт посещений клиентов.
- **Справочники.** `fCUSTOMERID → CUSTOMERS.fID`, `fPRODUCTID → PRODUCTS.fID`, `fSALESAGENTID → SALESAGENTS`, `fSALESAREA`/`fDIVISION → TREES/TREEDEF`, `fDOCTYPE → TEMPLATES.fDOCTYPE` (печатные формы).

## Примеры отчётных запросов

Документооборот по типам и месяцам за период (объём и сумма документов):

```sql
SELECT
    d.fDOCTYPE,
    YEAR(d.fDATE)  AS Год,
    MONTH(d.fDATE) AS Месяц,
    COUNT(*)       AS КолвоДокументов,
    SUM(d.fSUMM)   AS СуммаДокументов
FROM DOCUMENTS d WITH (NOLOCK)
WHERE d.fDATE >= '2025-01-01' AND d.fDATE < '2026-01-01'
GROUP BY d.fDOCTYPE, YEAR(d.fDATE), MONTH(d.fDATE)
ORDER BY d.fDOCTYPE, Год, Месяц;
```

Долг клиентов в разрезе территорий через документ-основание (`HICUSTOMERSDEBT → DOCUMENTS`):

```sql
SELECT
    doc.fSALESAREA AS Территория,
    SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) AS ТекущийДолг
FROM HICUSTOMERSDEBT d WITH (NOLOCK)
INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
GROUP BY doc.fSALESAREA
ORDER BY ТекущийДолг DESC;
```

Прослеживание цепочки «заказ → порождённые продажи» за период:

```sql
SELECT
    parent.fDOCNUM AS НомерЗаказа,
    parent.fDATE   AS ДатаЗаказа,
    child.fDOCNUM  AS НомерПродажи,
    child.fDATE    AS ДатаПродажи,
    child.fSUMM    AS СуммаПродажи
FROM DOCPARENTS dp
INNER JOIN DOCUMENTS parent WITH (NOLOCK) ON dp.fPARENTISN = parent.fISN
INNER JOIN DOCUMENTS child  WITH (NOLOCK) ON dp.fISN       = child.fISN
WHERE dp.fPARENTDOCTYPE = 1 AND dp.fDOCTYPE = 2
  AND parent.fDATE >= '2025-12-01' AND parent.fDATE < '2026-01-01'
ORDER BY parent.fDATE;
```

Активность пользователей по журналу документов (кто сколько операций выполнил):

```sql
SELECT
    l.fUSERID       AS Пользователь,
    l.fOP           AS КодОперации,
    COUNT(*)        AS КолвоОпераций,
    MIN(l.fDATE)    AS ПерваяОперация,
    MAX(l.fDATE)    AS ПоследняяОперация
FROM DOCUMENTSLOG l WITH (NOLOCK)
WHERE l.fDATE >= '2025-12-01' AND l.fDATE < '2026-01-01'
GROUP BY l.fUSERID, l.fOP
ORDER BY КолвоОпераций DESC;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
