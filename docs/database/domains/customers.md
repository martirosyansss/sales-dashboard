# Клиенты и торговые точки

Домен «Клиенты и торговые точки» — это ядро мастер-данных по контрагентам van-sales системы AS-Sales Management 7. Он описывает карточки клиентов (юр. и физ. лица, торговые точки), их территориальную привязку к участкам продаж (Sales Areas), контактных лиц, адреса доставки с геокоординатами, кредитные лимиты и лимиты сверхзадолженности, банковские счета, сферы деятельности, B2B-интеграцию (мобильное приложение/портал заказов) и предпочитаемые товары. Отдельная подсистема заявок на изменение карточек (`CUSTCHANGEREQUESTS*`) реализует модерацию правок, инициированных торговыми агентами с мобильных устройств, а таблица `CALLS` хранит журнал телефонного обзвона клиентов операторами.

> Примечание о коллизии регистра: реальная боевая таблица — `dbo.CUSTOMERS` (верхний регистр, 9688 строк, взята из `schema_raw.json`). Одноимённый файл `docs/database/schema/tables/dbo.CUSTOMERS.json` описывает демо/legacy-таблицу `dbo.Customers` (mixed-case, 0 строк, колонки `CustomerID/CustomerName/...`) — она к боевому домену не относится.

---

## dbo.CUSTOMERS  (9688 строк)

- Назначение: главный справочник клиентов и торговых точек. Одна строка = одна карточка контрагента; на неё по `fID` ссылаются практически все дочерние таблицы домена, а также документы продаж/долга (`SALES.fCUSTOMERID`, `DOCUMENTS.fCUSTOMERID`, регистры `HICUSTOMERSDEBT`/`HICUSTOMERSSUM`).
- В `app_v2.py` — центральная таблица почти всех отчётов: `INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID`, фильтрация активных точек по `fCLOSED`, группировка по `fGROUP` (справочник `TREES` c `fTREEID='CustGrp'`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | NOTNULL | Суррогатный первичный ключ клиента (цель всех `fCUSTOMERID`) |
| fCODE | nvarchar(12) | NOTNULL | Внешний/учётный код клиента (уникальный индекс `I_CUSTOMERS1`) |
| fNAME | nvarchar(50) | NOTNULL | Краткое наименование торговой точки/клиента |
| fFULLNAME | nvarchar(100) | NOTNULL | Полное юридическое наименование |
| fGROUP | nvarchar(6) | NOTNULL | Код группы клиентов → `TREES.fCODE` (`fTREEID='CustGrp'`) |
| fTAXCODE | nvarchar(20) | NOTNULL | ИНН/налоговый код |
| fPRICELIST | nvarchar(2) | NOTNULL | Код закреплённого прайс-листа |
| fPAYMENTTYPE | nvarchar(1) | NOTNULL | Тип оплаты (наличный/безналичный/в кредит) |
| fREGION | nvarchar(6) | NOTNULL | Код региона/территории (справочник `TREES`) |
| fDELAYDAYS | smallint | NOTNULL | Отсрочка платежа, дней |
| fPRIORITY | smallint | NOTNULL | Приоритет обслуживания клиента |
| fADDRESS | nvarchar(250) | NOTNULL | Основной адрес |
| fPHONE | nvarchar(50) | NOTNULL | Телефон (индекс `I_CUSTOMERS4`) |
| fEMAIL | nvarchar(50) | NOTNULL | Электронная почта |
| fMANAGERPOST | nvarchar(50) | NOTNULL | Должность руководителя |
| fMANAGER | nvarchar(50) | NOTNULL | ФИО руководителя |
| fACCOUNTANTPOST | nvarchar(50) | NOTNULL | Должность бухгалтера |
| fACCOUNTANT | nvarchar(50) | NOTNULL | ФИО бухгалтера |
| fCONTRACTNUMBER | nvarchar(50) | NOTNULL | Номер договора |
| fCONTRACTVALIDDATE | smalldatetime | NULL | Срок действия договора |
| fPASSPORT | nvarchar(50) | NOTNULL | Паспортные данные (для физлиц) |
| fSOCCARDNUM | nvarchar(10) | NOTNULL | Номер соц. карты |
| fCONTDEPOSIT | bit | NOTNULL | Признак работы по возвратной таре/депозиту |
| fCONTDEPSCHEME | nvarchar(3) | NOTNULL | Код схемы возвратной тары |
| fEXTERNALCODE | nvarchar(20) | NOTNULL | Код во внешней системе |
| fVATFREE | bit | NOTNULL | Освобождён от НДС |
| fCLOSED | bit | NOTNULL | Точка закрыта/неактивна (индекс `I_CUSTOMERS2`; фильтр активных клиентов) |
| fISN | uniqueidentifier | NOTNULL | Глобальный идентификатор карточки (уникальный индекс `I_CUSTOMERS5`) |
| fISFOREIGN | bit | NOTNULL | Признак иностранного контрагента |
| fBODY | nvarchar(3000) | NULL | Произвольные данные/комментарий карточки |
| fTS | timestamp | NOTNULL | Метка версии строки (rowversion) |
| fDELIVERYSTARTTIME | time | NULL | Начало окна доставки |
| fDELIVERYENDTIME | time | NULL | Конец окна доставки |
| fPRODUCTSACCESSSCHEME | nvarchar(3) | NOTNULL | Код схемы доступа к номенклатуре |
| fUSEAUTORESERVATION | bit | NOTNULL | Использовать авторезервирование товара |
| fORGANIZATIONACCOUNT | nvarchar(22) | NOTNULL | Расчётный счёт организации |
| fPREFERREDPRODUCTS | nvarchar(6) | NOTNULL | Код набора предпочитаемых товаров → `CUSTOMERPREFERREDPRODUCTS.fCODE` |
| fDISCOUNTGROUP | nvarchar(6) | NOTNULL | Группа скидок (индекс `I_CUSTOMERS6`) |
| fSCALEDISCOUNTGROUP | nvarchar(6) | NOTNULL | Группа шкальных скидок |
| fGIFTPROMOTIONGROUP | nvarchar(6) | NOTNULL | Группа подарочных промо-акций |
| fIBCOMMENT | nvarchar(50) | NOTNULL | Служебный комментарий |
| fEXTERNALBODY | nvarchar(max) | NULL | Данные внешней интеграции (JSON/текст) |
| fIDENTITYDOC | tinyint | NULL | Тип документа, удостоверяющего личность |

- Ключи и связи: PK `fID` (`PK_CUSTOMERS`). Явных FK нет. Referenced_by (по `fCUSTOMERID → fID`): `CUSTOMERSPHERES`, `CUSTOMERB2BDATA`, `CUSTOMERCONTACTS`, `CUSTOMERSALESAREAS`, `CUSTOMERLIMITS`, `CUSTOMERACCOUNTSINBANK`, `CUSTOMERDELIVERYADDRESSES`. Неявные связи: `fGROUP→TREES('CustGrp')`, `fSALESAREA` (через `CUSTOMERSALESAREAS`) `→TREES('SArea')`, `fPREFERREDPRODUCTS→CUSTOMERPREFERREDPRODUCTS.fCODE`, `fISN` — общий ключ карточки для внешних систем.

## dbo.CUSTOMERB2BDATA  (9688 строк)

- Назначение: параметры интеграции клиента с B2B-каналом (мобильное приложение/портал самостоятельных заказов) — активация QR, синхронизация данных, регистрация и последний вход в мобильном B2B. Строк ровно столько же, сколько клиентов (1:1 к `CUSTOMERS`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` (PK, 1:1) |
| fB2BID | int | NOTNULL | Идентификатор клиента в B2B-системе |
| fACTORID | int | NOTNULL | Идентификатор актора/учётной записи B2B |
| fB2BQRACTIVATED | bit | NOTNULL | QR-код активирован |
| fSYNCDATAWITHB2B | bit | NOTNULL | Синхронизировать данные с B2B |
| fAVAILABLEFORB2B | bit | NOTNULL | Клиент доступен в B2B-канале |
| fB2BACSBDATAPR | varchar(1) | NOTNULL | Признак периода доступности данных (код) |
| fB2BACSBDATAPRQTY | smallint | NOTNULL | Количество периодов доступности данных |
| fB2BACSBDATASINCEDATE | smalldatetime | NULL | Дата, с которой доступны данные в B2B |
| fREGISTEREDINB2BMOBILE | bit | NOTNULL | Зарегистрирован в мобильном B2B |
| fLASTLOGINTIMEINB2BMOBILE | datetime | NULL | Время последнего входа в мобильный B2B |
| fB2BSTATEID | smallint | NULL | Статус клиента в B2B |
| fB2BSYNCALLDATA | bit | NOTNULL | Синхронизировать все данные |

- Ключи и связи: PK `fCUSTOMERID` (`PK_CUSTOMERB2BDATA`, кластерный). FK `fCUSTOMERID→CUSTOMERS.fID`.

## dbo.CUSTOMERDELIVERYADDRESSES  (9323 строки)

- Назначение: список адресов доставки клиента с геокоординатами и точностью; один адрес помечается как основной (`fDEFAULT`). Используется для планирования доставки и построения маршрутов.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fADDRESS | nvarchar(250) | NOTNULL | Текст адреса доставки |
| fLATITUDE | decimal(10,6) | NULL | Широта |
| fLONGITUDE | decimal(10,6) | NULL | Долгота |
| fACCURACY | money | NULL | Точность геопозиции (метры) |
| fDEFAULT | bit | NOTNULL | Основной адрес доставки |
| fADDITIONALINFO | nvarchar(50) | NOTNULL | Доп. информация к адресу |
| fCLOSED | bit | NOTNULL | Адрес закрыт/не используется (по умолчанию 0) |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |
| fID | int | NOTNULL | PK адреса (`PK_CUSTOMERDELIVERYADDRESSES`) |

- Ключи и связи: PK `fID`. FK `fCUSTOMERID→CUSTOMERS.fID`. Уникальный индекс по `(fCUSTOMERID, fADDRESS, fLATITUDE, fLONGITUDE)`.

## dbo.CUSTOMERCONTACTS  (1816 строк)

- Назначение: контактные лица клиента (имя, телефон, e-mail) с признаком основного контакта и настройками B2B-уведомлений.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fNAME | nvarchar(50) | NOTNULL | ФИО контактного лица (часть PK) |
| fPHONE | nvarchar(50) | NOTNULL | Телефон (индекс `I_CUSTOMERCONTACTS1`) |
| fEMAIL | nvarchar(50) | NOTNULL | E-mail |
| fDEFAULT | bit | NOTNULL | Основной контакт |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |
| fB2BSENDNOTIFICATION | bit | NOTNULL | Отправлять уведомления в B2B |
| fISDATAAVAILABLEFORB2B | bit | NOTNULL | Данные контакта доступны в B2B |
| fNOTES | nvarchar(50) | NOTNULL | Примечания |

- Ключи и связи: PK `(fCUSTOMERID, fNAME)`. FK `fCUSTOMERID→CUSTOMERS.fID`.

## dbo.CUSTOMERLIMITS  (6892 строки)

- Назначение: кредитные лимиты клиента в разрезе подразделения (division): предельная сумма задолженности и лимит сверхзадолженности. Служит основой контроля превышения долга.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fDIVISION | nvarchar(6) | NOTNULL | Код подразделения → `TREES.fCODE` (`fTREEID='Division'`); часть PK |
| fSUMLIMIT | money | NOTNULL | Лимит суммы задолженности |
| fOVERDEBTLIMIT | money | NOTNULL | Лимит сверхзадолженности |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |

- Ключи и связи: PK `(fCUSTOMERID, fDIVISION)`. FK `fCUSTOMERID→CUSTOMERS.fID`. `fDIVISION→TREES('Division')`.

## dbo.CUSTOMERSALESAREAS  (9691 строка)

- Назначение: связь «клиент ↔ участок продаж (Sales Area)». Ключевая таблица для территориальной аналитики: почти все отчёты в `app_v2.py` присоединяют её (`INNER/LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID`) и джойнят участок к `TREES(fTREEID='SArea')`. Клиент может входить в несколько участков; один помечен как основной (`fDEFAULT`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fSALESAREA | nvarchar(6) | NOTNULL | Код участка продаж → `TREES.fCODE` (`fTREEID='SArea'`); часть PK, индекс `I_CUSTOMERSALESAREAS1` |
| fDEFAULT | bit | NOTNULL | Основной участок для клиента |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |

- Ключи и связи: PK `(fCUSTOMERID, fSALESAREA)`. FK `fCUSTOMERID→CUSTOMERS.fID`. `fSALESAREA→TREES('SArea')`.

## dbo.CUSTOMERSPHERES  (0 строк)

- Назначение: сферы деятельности, присвоенные клиенту (справочник кодов). На момент выгрузки таблица пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fCODE | nvarchar(6) | NOTNULL | Код сферы деятельности (часть PK) |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |
| fDEFAULT | bit | NOTNULL | Основная сфера |

- Ключи и связи: PK `(fCUSTOMERID, fCODE)`. FK `fCUSTOMERID→CUSTOMERS.fID`.

## dbo.CUSTOMERACCOUNTSINBANK  (5 строк)

- Назначение: банковские счета клиента (название банка/филиала и номер счёта) с признаком основного счёта.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCUSTOMERID | int | NOTNULL | Клиент → `CUSTOMERS.fID` |
| fNAME | nvarchar(50) | NOTNULL | Наименование банка/филиала |
| fACCOUNT | nvarchar(22) | NOTNULL | Номер счёта (часть PK, индекс `I_CUSTOMERACCOUNT`) |
| fDEFAULT | bit | NOTNULL | Основной счёт |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fTS | timestamp | NOTNULL | Метка версии строки |

- Ключи и связи: PK `(fCUSTOMERID, fACCOUNT)`. FK `fCUSTOMERID→CUSTOMERS.fID`.

## dbo.CUSTOMERPREFERREDPRODUCTS  (0 строк)

- Назначение: справочник наборов предпочитаемых товаров (шапка). Код набора привязывается к клиенту через `CUSTOMERS.fPREFERREDPRODUCTS`. На момент выгрузки пуст.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(6) | NOTNULL | Код набора (PK) |
| fNAME | nvarchar(50) | NOTNULL | Наименование набора |
| fTS | timestamp | NOTNULL | Метка версии строки |

- Ключи и связи: PK `fCODE`. Связь: `CUSTOMERS.fPREFERREDPRODUCTS→fCODE`; строки набора — в `CUSTOMERPREFERREDPRODUCTSDETAILS`.

## dbo.CUSTOMERPREFERREDPRODUCTSDETAILS  (0 строк)

- Назначение: строки набора предпочитаемых товаров — конкретные товары и их порядок в наборе. На момент выгрузки пуст.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(6) | NOTNULL | Код набора → `CUSTOMERPREFERREDPRODUCTS.fCODE` (часть PK) |
| fPRODUCTID | int | NOTNULL | Товар → `PRODUCTS.fID` (часть PK) |
| fORDER | smallint | NOTNULL | Порядок товара в наборе |
| fTS | timestamp | NOTNULL | Метка версии строки |

- Ключи и связи: PK `(fCODE, fPRODUCTID)`. Неявные связи: `fCODE→CUSTOMERPREFERREDPRODUCTS.fCODE`, `fPRODUCTID→PRODUCTS.fID`.

## dbo.CUSTCHANGEREQUESTS  (4373 строки)

- Назначение: заявки на изменение карточки клиента (или создание новой точки), поданные торговым агентом с мобильного устройства. Шапка заявки содержит предлагаемые новые значения полей карточки и статус модерации; `fISNEW=1` — заявка на нового клиента.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | NOTNULL | PK заявки (`PK_CUSTCHANGEREQUESTS`) |
| fISN | uniqueidentifier | NOTNULL | Глобальный идентификатор заявки |
| fCREATIONDATE | datetime | NOTNULL | Дата/время создания заявки |
| fSALESAGENTID | int | NOTNULL | Автор заявки → `SALESAGENTS.fID` (индекс `I1_CUSTCHANGEREQUESTS`) |
| fSTATE | smallint | NOTNULL | Статус заявки (0 — новая/на рассмотрении и т.д.) |
| fNAME | nvarchar(50) | NULL | Предлагаемое краткое наименование |
| fFULLNAME | nvarchar(100) | NULL | Предлагаемое полное наименование |
| fTAXCODE | nvarchar(20) | NULL | Предлагаемый налоговый код |
| fPRICELIST | nvarchar(2) | NULL | Предлагаемый прайс-лист |
| fGROUP | nvarchar(6) | NULL | Предлагаемая группа клиента (`TREES('CustGrp')`) |
| fADDRESS | nvarchar(250) | NULL | Предлагаемый адрес |
| fBUSINESSADDRESS | nvarchar(250) | NULL | Предлагаемый бизнес-адрес |
| fREGION | nvarchar(6) | NULL | Предлагаемый регион |
| fLATITUDE | decimal(10,6) | NULL | Предлагаемая широта |
| fLONGITUDE | decimal(10,6) | NULL | Предлагаемая долгота |
| fACCURACY | money | NULL | Точность геопозиции |
| fPAYMENTTYPE | nvarchar(1) | NULL | Предлагаемый тип оплаты |
| fDELAYDAYS | smallint | NULL | Предлагаемая отсрочка платежа |
| fCONTDEPOSIT | bit | NULL | Предлагаемый признак возвратной тары |
| fCONTDEPSCHEME | nvarchar(3) | NULL | Предлагаемая схема тары |
| fVATFREE | bit | NULL | Предлагаемый признак «без НДС» |
| fCLOSED | bit | NULL | Предложение закрыть точку |
| fPASSPORT | nvarchar(50) | NULL | Предлагаемые паспортные данные |
| fSOCCARDNUM | nvarchar(10) | NULL | Предлагаемый номер соц. карты |
| fPHONE | nvarchar(50) | NULL | Предлагаемый телефон |
| fEMAIL | nvarchar(129) | NULL | Предлагаемый e-mail |
| fMANAGER | nvarchar(50) | NULL | Предлагаемое ФИО руководителя |
| fMANAGERPOST | nvarchar(50) | NULL | Предлагаемая должность руководителя |
| fCONTACTSCHANGED | bit | NOTNULL | Флаг: изменены контактные лица (см. `CUSTCHANGEREQCONTACTS`) |
| fDELIVERYADDRESSESCHANGED | bit | NOTNULL | Флаг: изменены адреса доставки (см. `CUSTCHANGEREQDELIVERYADDRESSES`) |
| fISNEW | bit | NOTNULL | Заявка на нового клиента |

- Ключи и связи: PK `fID`. Неявные связи: `fSALESAGENTID→SALESAGENTS.fID`; дочерние таблицы `CUSTCHANGEREQUESTSCONFIRMATIONS` (1:1 по `fID`), `CUSTCHANGEREQCONTACTS`/`CUSTCHANGEREQDELIVERYADDRESSES` (по `fREQUESTID→fID`). После утверждения значения применяются к `CUSTOMERS`.

## dbo.CUSTCHANGEREQUESTSCONFIRMATIONS  (4373 строки)

- Назначение: поколоночные признаки подтверждения/модерации заявки (1:1 к `CUSTCHANGEREQUESTS` по `fID`). Каждое поле — маркер (smallint) того, какая часть заявки подтверждена (в примерах `fCOORDINATES=1`, `fCONTACTS=1`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | NOTNULL | Заявка → `CUSTCHANGEREQUESTS.fID` (PK, 1:1) |
| fNAME | smallint | NULL | Подтверждение изменения наименования |
| fFULLNAME | smallint | NULL | Подтверждение полного наименования |
| fTAXCODE | smallint | NULL | Подтверждение налогового кода |
| fGROUP | smallint | NULL | Подтверждение группы |
| fADDRESS | smallint | NULL | Подтверждение адреса |
| fBUSINESSADDRESS | smallint | NULL | Подтверждение бизнес-адреса |
| fREGION | smallint | NULL | Подтверждение региона |
| fCOORDINATES | smallint | NULL | Подтверждение геокоординат |
| fCONTACTS | smallint | NULL | Подтверждение контактов |
| fISCLOSED | smallint | NULL | Подтверждение закрытия точки |
| fDELIVERYADDRESSES | smallint | NULL | Подтверждение адресов доставки |

- Ключи и связи: PK `fID` (`PK_CUSTCHANGEREQUESTSCONFIRMATIONS`). Неявная связь `fID→CUSTCHANGEREQUESTS.fID`.

## dbo.CUSTCHANGEREQCONTACTS  (135 строк)

- Назначение: предлагаемые контактные лица в составе заявки на изменение карточки (аналог `CUSTOMERCONTACTS`, но для заявки).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fREQUESTID | int | NOTNULL | Заявка → `CUSTCHANGEREQUESTS.fID` (часть PK) |
| fNAME | nvarchar(50) | NOTNULL | ФИО контактного лица (часть PK) |
| fPHONE | nvarchar(50) | NOTNULL | Телефон |
| fEMAIL | nvarchar(50) | NOTNULL | E-mail |
| fDEFAULT | bit | NOTNULL | Основной контакт |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |
| fNOTES | nvarchar(50) | NOTNULL | Примечания |

- Ключи и связи: PK `(fREQUESTID, fNAME)`. Неявная связь `fREQUESTID→CUSTCHANGEREQUESTS.fID`.

## dbo.CUSTCHANGEREQDELIVERYADDRESSES  (0 строк)

- Назначение: предлагаемые адреса доставки в составе заявки на изменение карточки (аналог `CUSTOMERDELIVERYADDRESSES`). На момент выгрузки пуст.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fREQUESTID | int | NOTNULL | Заявка → `CUSTCHANGEREQUESTS.fID` (часть PK) |
| fID | int | NOTNULL | Номер адреса в заявке (часть PK) |
| fADDRESS | nvarchar(250) | NOTNULL | Текст адреса |
| fLATITUDE | decimal(10,6) | NULL | Широта |
| fLONGITUDE | decimal(10,6) | NULL | Долгота |
| fACCURACY | money | NULL | Точность геопозиции |
| fDEFAULT | bit | NOTNULL | Основной адрес |
| fADDITIONALINFO | nvarchar(50) | NOTNULL | Доп. информация |
| fCLOSED | bit | NOTNULL | Адрес закрыт |
| fROWNUM | smallint | NOTNULL | Порядковый номер строки |

- Ключи и связи: PK `(fREQUESTID, fID, fADDRESS, fLATITUDE, fLONGITUDE)`. Неявная связь `fREQUESTID→CUSTCHANGEREQUESTS.fID`.

## dbo.CALLS  (21343 строки)

- Назначение: журнал телефонных звонков колл-центра/операторов (обзвон клиентов): направление, длительность, оператор, статус и цель звонка. Связь с клиентом — неявная, по номеру телефона (`fCUSTOMERPHONENUMBER` сопоставляется с `CUSTOMERS.fPHONE`/`CUSTOMERCONTACTS.fPHONE`), жёсткого FK нет.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | uniqueidentifier | NOTNULL | PK записи звонка (`PK_CALLS`) |
| fLINKEDID | nvarchar(100) | NOTNULL | Идентификатор звонка в телефонии/АТС (уникальный индекс `I_CALLS1`) |
| fSTARTDATE | datetime | NOTNULL | Дата/время начала звонка (индекс `I_CALLS2`) |
| fCUSTOMERPHONENUMBER | nvarchar(50) | NOTNULL | Номер телефона клиента |
| fOPERATORPHONENUMBER | nvarchar(50) | NOTNULL | Внутренний номер/телефон оператора |
| fISINCOMING | bit | NOTNULL | Входящий (1) / исходящий (0) звонок |
| fDURATIONINSECONDS | int | NULL | Длительность разговора, сек |
| fNOTES | nvarchar(1000) | NULL | Заметки по звонку |
| fOPERATORUSERID | int | NULL | Оператор → `USERS.fID` |
| fISREPEATEDCUSTOMER | bit | NOTNULL | Повторное обращение клиента |
| fCALLSTATE | tinyint | NOTNULL | Статус звонка (напр. 2 — завершён/отвечен) |
| fCALLPURPOSE | nvarchar(3) | NOTNULL | Код цели звонка (справочник) |

- Ключи и связи: PK `fID`. Неявные связи: `fCUSTOMERPHONENUMBER→CUSTOMERS.fPHONE`/`CUSTOMERCONTACTS.fPHONE` (по номеру), `fOPERATORUSERID→USERS.fID`.

## dbo.NOTIDENTIFIEDB2BCUSTOMERS  (0 строк)

- Назначение: очередь неопознанных B2B-клиентов — записи из B2B-канала, которым ещё не сопоставлена карточка `CUSTOMERS` (требуют идентификации агентом). На момент выгрузки пуста.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | int | NOTNULL | PK записи (`PK_NOTIDENTIFIEDB2BCUSTOMERS`) |
| fB2BID | int | NOTNULL | Идентификатор в B2B-системе (индекс `I1_NOTIDENTIFIEDB2BCUSTOMERS`) |
| fSALESAGENTID | int | NOTNULL | Ответственный агент → `SALESAGENTS.fID` |
| fSTATE | smallint | NOTNULL | Статус обработки (по умолчанию 0) |
| fCREATIONDATE | datetime | NOTNULL | Дата поступления записи |

- Ключи и связи: PK `fID`. Неявные связи: `fB2BID→CUSTOMERB2BDATA.fB2BID` (после идентификации), `fSALESAGENTID→SALESAGENTS.fID`.

---

## Связи домена

- **Центр домена — `CUSTOMERS.fID`.** По нему через колонку `fCUSTOMERID` присоединяются: `CUSTOMERB2BDATA` (1:1), `CUSTOMERDELIVERYADDRESSES`, `CUSTOMERCONTACTS`, `CUSTOMERLIMITS`, `CUSTOMERSALESAREAS`, `CUSTOMERSPHERES`, `CUSTOMERACCOUNTSINBANK` (все 1:N).
- **Территория:** `CUSTOMERS → CUSTOMERSALESAREAS.fSALESAREA → TREES.fCODE (fTREEID='SArea')`. Это основной путь территориальной аналитики в `app_v2.py`.
- **Классификация:** `CUSTOMERS.fGROUP → TREES('CustGrp')` (группа клиентов); `CUSTOMERLIMITS.fDIVISION → TREES('Division')`; `CUSTOMERS.fREGION → TREES` (регион).
- **Предпочитаемые товары:** `CUSTOMERS.fPREFERREDPRODUCTS → CUSTOMERPREFERREDPRODUCTS.fCODE → CUSTOMERPREFERREDPRODUCTSDETAILS (fCODE) → PRODUCTS.fID`.
- **Заявки на изменение:** `CUSTCHANGEREQUESTS.fID` — шапка; `CUSTCHANGEREQUESTSCONFIRMATIONS` (1:1 по `fID`), `CUSTCHANGEREQCONTACTS` и `CUSTCHANGEREQDELIVERYADDRESSES` (по `fREQUESTID`). Автор — `fSALESAGENTID → SALESAGENTS.fID`. После утверждения изменения применяются к `CUSTOMERS` и её дочерним таблицам.
- **Соседние домены:** продажи/документы (`SALES.fCUSTOMERID`, `DOCUMENTS.fCUSTOMERID`) и финансы/долг (регистры `HICUSTOMERSDEBT`, `HICUSTOMERSSUM` и остатки `HIREST...`, см. `DEBT_CALCULATION_FORMULA.md`) ссылаются на `CUSTOMERS.fID`. Обзвон (`CALLS`) связан с клиентом только по номеру телефона. Агенты (`SALESAGENTS`) и пользователи (`USERS`) — источники `fSALESAGENTID`/`fOPERATORUSERID`.

## Примеры отчётных запросов

Активные торговые точки с наименованием участка продаж и группы клиента:

```sql
SELECT c.fID, c.fCODE, c.fNAME,
       csa.fSALESAREA,
       ISNULL(sa.fCAPTION, N'Не указана') AS AreaName,
       ISNULL(g.fCAPTION, c.fGROUP)        AS GroupName
FROM CUSTOMERS c
LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID AND csa.fDEFAULT = 1
LEFT JOIN TREES sa ON sa.fCODE = csa.fSALESAREA AND sa.fTREEID = 'SArea'
LEFT JOIN TREES g  ON g.fCODE  = c.fGROUP       AND g.fTREEID = 'CustGrp'
WHERE c.fCLOSED = 0
ORDER BY c.fNAME;
```

Кредитные лимиты клиентов по подразделениям:

```sql
SELECT c.fID, c.fNAME,
       cl.fDIVISION,
       ISNULL(d.fCAPTION, cl.fDIVISION) AS DivisionName,
       cl.fSUMLIMIT, cl.fOVERDEBTLIMIT
FROM CUSTOMERS c
INNER JOIN CUSTOMERLIMITS cl ON c.fID = cl.fCUSTOMERID
LEFT JOIN TREES d ON d.fCODE = cl.fDIVISION AND d.fTREEID = 'Division'
WHERE c.fCLOSED = 0 AND cl.fSUMLIMIT > 0
ORDER BY cl.fSUMLIMIT DESC;
```

Заявки на изменение карточек по агентам (незакрытые/новые):

```sql
SELECT r.fID, r.fCREATIONDATE, r.fSALESAGENTID,
       r.fSTATE, r.fISNEW,
       r.fNAME, r.fFULLNAME, r.fTAXCODE,
       r.fCONTACTSCHANGED, r.fDELIVERYADDRESSESCHANGED
FROM CUSTCHANGEREQUESTS r
WHERE r.fCREATIONDATE >= DATEADD(MONTH, -3, GETDATE())
ORDER BY r.fCREATIONDATE DESC;
```

Статистика обзвона операторов за период (кол-во и средняя длительность):

```sql
SELECT CAST(c.fSTARTDATE AS date) AS CallDate,
       c.fOPERATORUSERID,
       SUM(CASE WHEN c.fISINCOMING = 1 THEN 1 ELSE 0 END) AS IncomingCnt,
       SUM(CASE WHEN c.fISINCOMING = 0 THEN 1 ELSE 0 END) AS OutgoingCnt,
       AVG(CAST(c.fDURATIONINSECONDS AS float))            AS AvgDurationSec
FROM CALLS c
WHERE c.fSTARTDATE >= DATEADD(DAY, -30, GETDATE())
GROUP BY CAST(c.fSTARTDATE AS date), c.fOPERATORUSERID
ORDER BY CallDate DESC, c.fOPERATORUSERID;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
- [Формула расчёта долга](../../../DEBT_CALCULATION_FORMULA.md)
