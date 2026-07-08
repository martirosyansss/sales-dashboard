# Финансы: долги, платежи, взаиморасчёты

Домен покрывает дебиторскую задолженность клиентов, поступающие платежи, погашения (разноску оплат по документам долга), акты сверки, а также вспомогательные справочники и регистры денежных сумм по клиентам и торговым агентам (van-sales). Центральный регистр движения долга — `HICUSTOMERSDEBT` (дебет/кредит по каждому документу-основанию), остатки по документам хранятся в `HIRESTCUSTOMERSDEBT`, а возвраты и предоплаты клиента — в `HICUSTOMERSSUM` / `HIRESTCUSTOMERSSUM`. Итоговая задолженность считается по формуле `ДОЛГ = (Σ Дебет − Σ Кредит) − |Type01(возвраты)| − |Type02(предоплаты)|` (см. `DEBT_CALCULATION_FORMULA.md`).

> Все таблицы домена читаются только на выборку (SELECT). Явных FK в БД почти нет — связи неявные, по соглашению об именах (`fCUSTOMERID → CUSTOMERS.fID`, `fISN`/`fDEBTDOCISN → шапка документа` и т.д.).

---

## dbo.HICUSTOMERSDEBT  (783 621 строк)

- Назначение: главный регистр движения (History) дебиторской задолженности — по одной строке на каждую операцию, увеличивающую (`D`) или уменьшающую (`C`) долг клиента по конкретному документу. Основа расчёта долга и фактических платежей.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции движения долга |
| fDEBTDOCISN | uniqueidentifier | нет | ISN документа-основания долга (шапка: `DOCUMENTS.fISN`, обычно документ продажи) |
| fSUM | money | нет | Сумма операции |
| fOP | varchar(3) | нет | Код операции: `RLZ` — реализация/продажа (дебет), `PAY` — платёж (кредит) и др. |
| fDBCR | varchar(1) | нет | `D` — дебет (увеличение долга), `C` — кредит (уменьшение долга / оплата) |
| fBASE | uniqueidentifier | нет | ISN операции-первопричины (для `PAY` — ISN платёжного документа `PAYMENTS.fISN`; для `RLZ` совпадает с fDEBTDOCISN) |
| fUSERID | int | нет | Идентификатор пользователя, создавшего движение |

- Ключи и связи: кластерный индекс по `fDEBTDOCISN` (PK не уникален — несколько движений на документ). Неявные связи: `fDEBTDOCISN → DOCUMENTS.fISN → DOCUMENTS.fCUSTOMERID → CUSTOMERS.fID`; `fBASE → PAYMENTS.fISN` (для платежей). Индексы: `fBASE`, `fDATE`.

## dbo.HIRESTCUSTOMERSDEBT  (368 804 строки)

- Назначение: регистр остатков (Rest) непогашенной задолженности по каждому документу долга — текущий незакрытый остаток по документу (`fSUM = 0` означает полностью погашённый документ).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDEBTDOCISN | uniqueidentifier | нет | ISN документа долга (`= HICUSTOMERSDEBT.fDEBTDOCISN` / `DOCUMENTS.fISN`) |
| fSUM | money | нет | Текущий непогашенный остаток по документу |

- Ключи и связи: кластерный индекс по `fDEBTDOCISN`. Неявная связь: `fDEBTDOCISN → HICUSTOMERSDEBT.fDEBTDOCISN` (детализация движений) и `→ DOCUMENTS.fISN`.

## dbo.HICUSTOMERSSUM  (15 427 строк)

- Назначение: регистр движения (History) сумм возвратов и предоплат клиента по типам (`fTYPE`) — история изменения показателей Type01/Type02, участвующих в формуле долга.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fCUSTOMERID | int | нет | Клиент (`→ CUSTOMERS.fID`) |
| fSUM | money | нет | Сумма операции |
| fTYPE | varchar(2) | нет | Тип суммы: `01` — возвраты, `02` — предоплата/аванс (ԿԱՆԽԱՎՃԱՐ) |
| fOP | varchar(3) | нет | Код операции: `RET` — возврат, `DSR`, и др. |
| fDBCR | varchar(1) | нет | `D` — дебет, `C` — кредит |
| fBASE | uniqueidentifier | нет | ISN документа-основания операции |
| fUSERID | int | нет | Пользователь, создавший движение |

- Ключи и связи: кластерный индекс по (`fTYPE, fCUSTOMERID, fDIVISION, fDATE`). Неявная связь: `fCUSTOMERID → CUSTOMERS.fID`.

## dbo.HIRESTCUSTOMERSSUM  (4 621 строка)

- Назначение: регистр остатков (Rest) сумм возвратов (`fTYPE='01'`) и предоплат (`fTYPE='02'`) по клиенту и подразделению. Именно из этой таблицы берутся Type01/Type02, вычитаемые из долга по модулю (таблица без истории — снимок текущих остатков).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fCUSTOMERID | int | нет | Клиент (`→ CUSTOMERS.fID`) |
| fTYPE | varchar(2) | нет | `01` — возвраты, `02` — предоплата/аванс |
| fSUM | money | нет | Текущий остаток суммы по типу (может быть отрицательным) |

- Ключи и связи: уникальный кластерный индекс по (`fTYPE, fCUSTOMERID, fDIVISION`). Неявная связь: `fCUSTOMERID → CUSTOMERS.fID`.

## dbo.PAYMENTS  (325 993 строки)

- Назначение: шапки платёжных документов от клиентов (поступления денег). Используется для отчётов по оплатам; каждое поступление порождает движение `PAY/C` в `HICUSTOMERSDEBT`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | Суррогатный ключ платёжного документа (`→ HICUSTOMERSDEBT.fBASE`) |
| fDATE | smalldatetime | нет | Дата платежа |
| fDOCNUM | nvarchar(12) | нет | Номер документа (PK, кластерный) |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fCUSTOMERID | int | нет | Клиент-плательщик (`→ CUSTOMERS.fID`) |
| fSALESAGENTID | int | нет | Агент, принявший платёж (`→ SALESAGENTS`) |
| fPAYMENTTYPE | nvarchar(1) | да | Тип платежа (напр. `1` — наличные) |
| fSUM | money | нет | Сумма платежа |
| fPREPAYMENT | money | нет | Сумма, отнесённая на предоплату/аванс |
| fDOCGROUP | nvarchar(3) | нет | Группа документа |
| fCOMMENT | nvarchar(255) | нет | Комментарий (напр. «Կանխիկ վճարում» — наличная оплата) |
| fCREATIONTYPEID | uniqueidentifier | да | Тип/способ создания документа |
| fSTATE | tinyint | да | Состояние документа (`2` — проведён/подтверждён) |
| fCONTACT | nvarchar(50) | нет | Контактное лицо |
| fCREATEMETHOD | varchar(1) | нет | Способ создания |
| fSALESAREA | nvarchar(6) | нет | Территория продаж (`→ TREES/SArea`) |
| fOTHSYSSENDSTATUS | nvarchar(1) | нет | Статус выгрузки во внешнюю систему |
| fORGANIZATIONACCOUNT | nvarchar(22) | нет | Счёт организации-получателя (`→ ORGANIZATIONACCOUNTS.fACCOUNT`) |
| fCUSTOMERACCOUNT | nvarchar(22) | нет | Банковский счёт клиента |
| fECRCHECKNUM | nvarchar(12) | нет | Номер фискального чека (ЭКЛЗ/ECR) |
| fECRCHECKDATE | datetime | да | Дата фискального чека |
| fECRCRN | nvarchar(12) | нет | Регистрационный номер ККМ (CRN) |

- Ключи и связи: PK (кластерный) по `fDOCNUM`; уникальный индекс по `fISN`. Неявные связи: `fCUSTOMERID → CUSTOMERS.fID`; `fSALESAGENTID → SALESAGENTS`; `fISN → HICUSTOMERSDEBT.fBASE` (движение `PAY`).

## dbo.DISCHARGEDETAILS  (426 456 строк)

- Назначение: строки разноски/погашения — распределение сумм платежей и иных кредитовых операций по конкретным документам долга (какой документ долга и на какую сумму погашается).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | ISN документа-погашения (шапка операции, обычно платёж/кредит) |
| fSUM | money | нет | Сумма, отнесённая на погашение документа долга |
| fROWNUM | smallint | нет | Номер строки в документе |
| fDEBTDOCISN | uniqueidentifier | нет | ISN погашаемого документа долга (`→ HICUSTOMERSDEBT.fDEBTDOCISN`) |

- Ключи и связи: кластерный индекс по `fISN`, некластерный по `fDEBTDOCISN`. Неявные связи: `fDEBTDOCISN → HICUSTOMERSDEBT.fDEBTDOCISN` / `DOCUMENTS.fISN`; `fISN → DOCUMENTS.fISN` (документ-погаситель, напр. `PAYMENTS`).

## dbo.RECONCILATIONDETAILS  (1 327 строк)

- Назначение: строки актов сверки взаиморасчётов — обороты по дебету/кредиту в разрезе базовых документов (продажи, оплаты) за период.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fISN | uniqueidentifier | нет | ISN акта сверки (шапка; несколько строк на один fISN) |
| fDATE | smalldatetime | нет | Дата операции в строке сверки |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fBASEDOCISN | uniqueidentifier | нет | ISN базового документа операции (`→ DOCUMENTS.fISN`) |
| fBASEDOCDESCRIPTION | nvarchar(255) | нет | Текстовое описание базового документа (напр. «Վաճառք N - …») |
| fTRANSACTIONCOMMENT | nvarchar(255) | нет | Комментарий к операции (напр. «Կրեդիտի մարում» — погашение кредита) |
| fSUMIN | money | нет | Приход/дебетовый оборот в строке сверки |
| fSUMOUT | money | нет | Расход/кредитовый оборот в строке сверки |
| fROWNUM | smallint | нет | Номер строки в документе |

- Ключи и связи: кластерный индекс по `fISN`. Неявная связь: `fBASEDOCISN → DOCUMENTS.fISN` (базовый документ — продажа/оплата).

## dbo.HIAGENTSSUM  (290 851 строка)

- Назначение: регистр движения (History) денежных сумм по торговым агентам (van-sales) — приход/расход наличности и взаиморасчёты агента с организацией по операциям (`fOP`).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата операции |
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fSALESAGENTID | int | нет | Агент (`→ SALESAGENTS`) |
| fSUM | money | нет | Сумма операции |
| fOP | varchar(3) | нет | Код операции: `SSR` — реализация, `PAC` — приём оплаты/инкассация и др. |
| fDBCR | varchar(1) | нет | `D` — дебет, `C` — кредит |
| fBASE | uniqueidentifier | нет | ISN документа-основания операции |
| fUSERID | int | нет | Пользователь, создавший движение |

- Ключи и связи: кластерный индекс по (`fSALESAGENTID, fDIVISION, fDATE`). Неявная связь: `fSALESAGENTID → SALESAGENTS`.

## dbo.HIRESTAGENTSSUM  (272 строки)

- Назначение: регистр остатков (Rest) денежных сумм по агенту и подразделению — текущий сальдо-остаток наличности/взаиморасчётов агента (`fSUM = 0` — расчёты закрыты).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDIVISION | nvarchar(6) | нет | Код подразделения/дивизиона |
| fSALESAGENTID | int | нет | Агент (`→ SALESAGENTS`) |
| fSUM | money | нет | Текущий остаток суммы по агенту |

- Ключи и связи: уникальный кластерный индекс по (`fSALESAGENTID, fDIVISION`). Неявная связь: `fSALESAGENTID → SALESAGENTS`.

## dbo.IBPAYMENTS  (0 строк)

- Назначение: платежи, полученные через интернет-банк (банковская интеграция); сопоставление банковской выписки с документами системы. В боевой БД пока пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDOCDATE | smalldatetime | нет | Дата банковского платежа |
| fACCOUNT | nvarchar(22) | нет | Счёт получателя (`→ ORGANIZATIONACCOUNTS.fACCOUNT`) |
| fSUM | money | нет | Сумма платежа |
| fPAYERACCOUNT | nvarchar(22) | нет | Счёт плательщика |
| fCOMMENT | nvarchar(max) | нет | Назначение платежа |
| fSMDOCISN | uniqueidentifier | нет | ISN связанного документа системы (`→ PAYMENTS.fISN` / `DOCUMENTS.fISN`), PK |

- Ключи и связи: уникальный кластерный индекс (PK) по `fSMDOCISN`; индекс по (`fDOCDATE, fACCOUNT`). Неявная связь: `fACCOUNT → ORGANIZATIONACCOUNTS.fACCOUNT`.

## dbo.BANKAM  (776 строк)

- Назначение: справочник банков Армении (наименование, SWIFT, IBAN-код банка) для реквизитов счетов и банковских операций.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(5) | нет | Код банка/филиала (PK) |
| fCAPTION | nvarchar(255) | нет | Наименование банка (арм.) |
| fIBANCODE | nchar(2) | нет | Код банка в структуре IBAN |
| fSWIFTCODE | nvarchar(11) | нет | SWIFT/BIC код |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный индекс (PK) по `fCODE`. Явных FK нет.

## dbo.CURR  (0 строк)

- Назначение: справочник валют (код, наименование, названия денежной единицы и разменной монеты). В боевой БД пусто (учёт в одной валюте — драм).
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUR | nchar(3) | нет | Код валюты (PK, напр. AMD/USD) |
| fCAPTION | nvarchar(50) | нет | Наименование валюты |
| fUNITNAME | nvarchar(10) | нет | Название денежной единицы |
| fCENTNAME | nvarchar(10) | нет | Название разменной монеты |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный индекс (PK) по `fCUR`. Referenced_by: `CURREXCHG.fCUR`.

## dbo.CURREXCHG  (0 строк)

- Назначение: курсы валют на дату (для пересчёта сумм). В боевой БД пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDATE | smalldatetime | нет | Дата курса |
| fCUR | nchar(3) | нет | Код валюты (`→ CURR.fCUR`) |
| fRATE | money | нет | Курс валюты |
| fBASE | money | нет | База/номинал курса |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный индекс по (`fCUR, fDATE`). FK: `fCUR → CURR.fCUR` (`FK_CURREXCHG_fCUR`).

## dbo.ORGANIZATIONACCOUNTS  (0 строк)

- Назначение: банковские счета собственной организации (получателя платежей), в т.ч. признак счёта для интернет-банка. В боевой БД пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fACCOUNT | nvarchar(22) | нет | Номер счёта организации (PK) |
| fNOTE | nvarchar(50) | нет | Примечание/наименование счёта |
| fDEFAULT | bit | нет | Признак счёта по умолчанию |
| fASINTERNETBANK | bit | нет | Признак счёта для интернет-банка |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: уникальный кластерный индекс (PK) по `fACCOUNT`. Неявно: `→ PAYMENTS.fORGANIZATIONACCOUNT`, `→ IBPAYMENTS.fACCOUNT`.

## dbo.ASSIGNORS  (0 строк)

- Назначение: справочник цедентов/принципалов (организаций-передающих) для операций уступки/факторинга — реквизиты и налоговый код. В боевой БД пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(6) | нет | Код цедента (PK) |
| fNAME | nvarchar(50) | нет | Наименование |
| fTAXCODE | nvarchar(20) | нет | Налоговый код (ИНН/ГРН) |
| fSETTLEMENTACCOUNT | nvarchar(22) | нет | Расчётный счёт |
| fDESCRINTAXSRV | nvarchar(50) | нет | Наименование в налоговой службе |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный индекс (PK) по `fCODE`. Явных FK нет.

## dbo.CUSTOMEROVERDEBTANALYSESCHEMES  (0 строк)

- Назначение: справочник схем анализа просроченной задолженности клиентов (шапка схемы «старения» долга). В боевой БД пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код схемы (PK) |
| fNAME | nvarchar(50) | нет | Наименование схемы |
| fTS | timestamp | нет | Служебная метка версии строки (rowversion) |

- Ключи и связи: уникальный кластерный индекс (PK) по `fCODE`. Referenced (неявно): `CUSTOMEROVERDEBTANALYSESCHEMEDETAILS.fSCHEMECODE`.

## dbo.CUSTOMEROVERDEBTANALYSESCHEMEDETAILS  (0 строк)

- Назначение: строки схемы анализа просрочки — интервалы дней просрочки («корзины старения», напр. 0–30, 31–60 дней). В боевой БД пусто.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fSCHEMECODE | nvarchar(3) | нет | Код схемы (`→ CUSTOMEROVERDEBTANALYSESCHEMES.fCODE`) |
| fCAPTION | nvarchar(50) | нет | Название интервала |
| fSTARTVALUE | smallint | нет | Начало интервала (дней просрочки) |
| fENDVALUE | smallint | нет | Конец интервала (дней просрочки) |
| fROWNUM | smallint | нет | Порядковый номер строки |

- Ключи и связи: уникальный кластерный индекс (PK) по (`fSCHEMECODE, fCAPTION`). Неявная связь: `fSCHEMECODE → CUSTOMEROVERDEBTANALYSESCHEMES.fCODE`.

---

## Связи домена

- **Долг клиента**: `HICUSTOMERSDEBT` (движения `D`/`C`) детализирует остаток в `HIRESTCUSTOMERSDEBT` по `fDEBTDOCISN`. Оба ссылаются на шапку документа `DOCUMENTS.fISN`, а через неё — на `CUSTOMERS.fID` (в коде `app_v2.py` соединение идёт `HICUSTOMERSDEBT.fDEBTDOCISN → DOCUMENTS.fISN → DOCUMENTS.fCUSTOMERID → CUSTOMERS.fID`; в упрощённом варианте формулы — напрямую по `fCUSTOMERID`).
- **Платёж**: `PAYMENTS` (шапка) → движение `HICUSTOMERSDEBT` c `fOP='PAY'`, `fDBCR='C'`, где `HICUSTOMERSDEBT.fBASE = PAYMENTS.fISN`. Разноска оплаты по документам долга хранится в `DISCHARGEDETAILS` (`fISN` = платёж, `fDEBTDOCISN` = гасимый документ).
- **Корректировки долга**: возвраты (`fTYPE='01'`) и предоплаты (`fTYPE='02'`) ведутся в `HICUSTOMERSSUM` (история) и `HIRESTCUSTOMERSSUM` (остатки) по `fCUSTOMERID`. Именно остатки Type01/Type02 вычитаются из долга по модулю (см. `DEBT_CALCULATION_FORMULA.md`).
- **Взаиморасчёты агентов**: `HIAGENTSSUM` (движения) и `HIRESTAGENTSSUM` (остатки) по `fSALESAGENTID` описывают денежные обороты van-sales-агентов; `PAYMENTS.fSALESAGENTID` связывает поступление с агентом.
- **Сверка**: `RECONCILATIONDETAILS` группирует обороты по базовым документам `fBASEDOCISN → DOCUMENTS.fISN`.
- **Банк/счета/валюта**: `PAYMENTS.fORGANIZATIONACCOUNT` и `IBPAYMENTS.fACCOUNT → ORGANIZATIONACCOUNTS.fACCOUNT`; банки — `BANKAM.fCODE`; валюты и курсы — `CURR.fCUR → CURREXCHG.fCUR`.
- **Соседние домены**: клиенты (`CUSTOMERS`, `CUSTOMERSALESAREAS` — территория/группа клиента), агенты (`SALESAGENTS`, `SALESAGENTDIVISIONS`), документы и продажи (`DOCUMENTS`, `SALES`), справочники территорий (`TREES`).

## Примеры отчётных запросов

### 1. Текущая задолженность по клиенту (полная формула)
```sql
-- ДОЛГ = (Σ Дебет − Σ Кредит) − |Type01| − |Type02| на дату
SELECT
    c.fID AS customer_id,
    ISNULL(deb.DebtFromDocs, 0)
      - ABS(ISNULL(rst.Type01, 0))
      - ABS(ISNULL(rst.Type02, 0)) AS current_debt
FROM CUSTOMERS c
OUTER APPLY (
    SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) AS DebtFromDocs
    FROM HICUSTOMERSDEBT d WITH (NOLOCK)
    INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fCUSTOMERID = c.fID
      AND d.fDATE < '2026-01-01'
) deb
OUTER APPLY (
    SELECT
        SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) AS Type01,
        SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) AS Type02
    FROM HIRESTCUSTOMERSSUM r WITH (NOLOCK)
    WHERE r.fCUSTOMERID = c.fID
) rst
WHERE c.fID = 595;
```

### 2. Фактические платежи по территориям продаж за период
```sql
-- Платежи = кредитовые движения PAY из HICUSTOMERSDEBT
SELECT
    doc.fSALESAREA AS area_code,
    ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS total_payments
FROM HICUSTOMERSDEBT h WITH (NOLOCK)
INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON h.fDEBTDOCISN = doc.fISN
WHERE h.fOP = 'PAY'
  AND h.fDATE BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY doc.fSALESAREA
ORDER BY total_payments DESC;
```

### 3. Реестр поступлений от клиентов (из PAYMENTS) по агентам
```sql
SELECT
    p.fSALESAREA        AS area_code,
    p.fSALESAGENTID     AS agent_id,
    COUNT(*)            AS payments_count,
    SUM(p.fSUM)         AS total_sum,
    SUM(p.fPREPAYMENT)  AS prepayment_sum
FROM PAYMENTS p WITH (NOLOCK)
WHERE p.fDATE BETWEEN '2025-10-01' AND '2025-10-31'
  AND p.fSTATE = 2
GROUP BY p.fSALESAREA, p.fSALESAGENTID
ORDER BY total_sum DESC;
```

### 4. Разноска оплат по непогашенным документам долга
```sql
-- Сколько списано (DISCHARGEDETAILS) против остатка по документу (HIRESTCUSTOMERSDEBT)
SELECT TOP 100
    rd.fDEBTDOCISN,
    rd.fSUM              AS remaining_debt,
    ISNULL(dd.discharged, 0) AS discharged_sum
FROM HIRESTCUSTOMERSDEBT rd WITH (NOLOCK)
OUTER APPLY (
    SELECT SUM(d.fSUM) AS discharged
    FROM DISCHARGEDETAILS d WITH (NOLOCK)
    WHERE d.fDEBTDOCISN = rd.fDEBTDOCISN
) dd
WHERE rd.fSUM > 0
ORDER BY rd.fSUM DESC;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
- [Формула расчёта долга](../../../DEBT_CALCULATION_FORMULA.md)
