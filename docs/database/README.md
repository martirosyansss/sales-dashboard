# База данных SalesManagement — документация

> **ERP:** AS-Sales Management 7 (van-sales/дистрибуция) · **СУБД:** Microsoft SQL Server · **Сервер:** 192.168.1.4

> ⛔ **Режим доступа — СТРОГО ТОЛЬКО ЧТЕНИЕ.** Все запросы — `SELECT ... WITH (NOLOCK)`. Никаких INSERT/UPDATE/DELETE/DDL и запуска `asp_*` процедур.


Полная карта боевой БД: **169 таблиц**, **1342 колонки**, **417 процедур/функций**. Явных внешних ключей почти нет (12) — связи **неявные**, по колонкам с префиксом `f…` (см. «Соглашения»). Документация сгенерирована из живой схемы (`schema/schema_raw.json`) и реальной логики `app_v2.py`, проверена адверсариально и на живых запросах.


## 🚀 С чего начать
- Хотите **сразу писать отчёты** → [REPORTING_GUIDE.md](REPORTING_GUIDE.md) — готовые SELECT-рецепты метрик, обязательные фильтры, формула долга, подводные камни.
- Нужна **конкретная таблица** → найдите её в [алфавитном указателе](#алфавитный-указатель-таблица--домен) и перейдите в файл домена.
- Нужен **обзор области** → выберите домен в таблице ниже.
- **Хранимые процедуры/функции** → [ROUTINES.md](ROUTINES.md). **Формула долга** → [DEBT_CALCULATION_FORMULA.md](../../DEBT_CALCULATION_FORMULA.md).


## 📚 Домены (12)

| Домен | Таблиц | Назначение | Ключевые таблицы |
|---|--:|---|---|
| [Клиенты и торговые точки](domains/customers.md) | 16 | Карточки клиентов/ТТ, контакты, адреса доставки, лимиты, B2B-данные, заявки на изменение и обзвон. | `CALLS`, `CUSTOMERSALESAREAS`, `CUSTOMERS` |
| [Продажи (расходные накладные)](domains/sales.md) | 11 | Шапки и строки документов реализации — главный источник выручки. | `SALEDOCDETAILS`, `HIRESTSOLDPRODUCTS`, `HISOLDPRODUCTS` |
| [Заказы, возвраты и резервирование](domains/orders.md) | 9 | Заказы клиентов, возвраты (RETURNS/RETURNORDERS), резервирование товара под заказ. | `PROVIDINGDELIVERIES`, `ORDERS`, `HIRESERVATION` |
| [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) | 17 | Регистры долгов и платежей, взаиморасчёты, банковские счета, валюты, сверки. | `HICUSTOMERSDEBT`, `DISCHARGEDETAILS`, `HIRESTCUSTOMERSDEBT` |
| [Товары, цены, скидки и акции](domains/products.md) | 26 | Справочник товаров, прайс-листы, скидки/акции, штрихкоды, маркировка, комплектация. | `CUSTOMERPRICELISTS`, `PRODUCTSDISCOUNTS`, `CPACODES` |
| [Склад: остатки, движения, активы](domains/warehouse.md) | 14 | Складские регистры остатков/движений (HI/HIREST), инвентаризация, поставки, активы. | `HIAGENTPRODUCTS`, `PRODUCTACCOUNTINGDETAILS`, `HISTORAGES` |
| [Маршруты, визиты и GPS](domains/routes.md) | 11 | Плановые и фактические маршруты, визиты, GPS-координаты агентов, звонки. | `AGENTLOCATIONS`, `PLANNEDROUTESLIST`, `ACTUALROUTES` |
| [Документооборот, ЭДО и налоговые](domains/documents.md) | 12 | Общая таблица документов и лог, ЭДО, налоговые документы, вложения, доступы к документам. | `DOCUMENTSLOG`, `DOCUMENTS`, `DOCPARENTS` |
| [Агенты, территории и оргструктура](domains/agents.md) | 9 | Торговые агенты, привязка к территориям/дивизионам/машинам, иерархия TREES. | `SALESAGENTPREFEREDPRODUCTS`, `SALESAGENTPRODUCTSACCESS`, `SALESAGENTDIVISIONS` |
| [Анкеты, опросы и задачи](domains/surveys.md) | 12 | Анкеты, вопросы, результаты визитов/опросов, задачи и их наблюдатели. | `PRODUCTSINVENTORIES`, `QUESTIONS`, `QUESTIONNAIREDETAILS` |
| [Система, доступы и служебные справочники](domains/system.md) | 28 | Пользователи, роли, доступы, параметры, шаблоны, скрипты, служебные справочники. | `DIRSLOG`, `DIALOGVALUES`, `PARAMS` |
| [Legacy/демо-таблицы](domains/legacy_demo.md) | 4 | Демо-таблицы mixed-case (Customers/Products/Sales/SaleDetails) — НЕ боевые, не использовать. | `Customers`, `Products`, `Sales` |
| **Итого** | **169** | | |

## 🔑 Соглашения об именовании и неявные связи

- Колонки с префиксом `f…` (`fISN`, `fID`, `fCUSTOMERID` …).
- `fISN` (uniqueidentifier) — суррогатный ключ документа; строки ссылаются на шапку по `fISN` (напр. `SALEDOCDETAILS.fISN → SALES.fISN`).
- `fCUSTOMERID → CUSTOMERS.fID`; `fPRODUCTID → PRODUCTS.fID`; `fSALESAGENTID/fVANAGENTID → SALESAGENTS.fID`.
- `fSALESAREA`, `fDIVISION`, `fGROUP`, `fREGION` — коды справочников; названия территорий/дивизионов — в `TREES` (`fTREEID='SArea'` / `'Division'`).
- Путь «клиент → территория» для аналитики — через `CUSTOMERSALESAREAS`, а не `SALES.fSALESAREA`.
- Регистры: `HI…` — движения (история), `HIREST…` — остатки. Долг/остатки считаются **кумулятивно**.
- Обязательный фильтр проведённых документов: `fSTATE = 2` (SALES, RETURNS и др.).

## ✅ Контроль качества

- **Покрытие:** 169/169 таблиц (100%), 0 неохваченных.
- **Адверсариальная проверка:** каждый доменный док сверен отдельным ревизором с эталонной схемой на выдуманные колонки; расхождения в `orders.md` (несуществующая таблица `SALEDOCS` → `SALES.fDOCNUM`) устранены.
- **Живая проверка (read-only):** 7 репрезентативных запросов из REPORTING_GUIDE выполнены на боевой БД и вернули корректные данные (напр. выручка Q1-2025 ≈ 121.65 млн; долг территории 101: дебет 16.39 млн − |Type01| 0.29 млн − |Type02| 0.26 млн).
- **Честные маркеры:** где смысл колонки не подтверждён данными/кодом — стоит «назначение не установлено» (не выдумано).

## ⚠️ Известные ограничения

- Домены **surveys** (~16 колонок) и **warehouse** (~4) содержат поля «назначение не установлено» — низкая достоверность, уточнить у владельца ERP.
- В **documents.md** уникальность ряда индексов (`DOCUMENTS`, `DOCPARENTS`, `SMS`, `TAXDOCUMENTDETAILS`) заявлена по смыслу, но флаг `is_unique` в схеме не подтверждён — на отчётные запросы не влияет.
- В **routes.md**: `fSALESAREA` — код территории (сам справочник — `TREES`; `CUSTOMERSALESAREAS` — таблица привязки, не справочник).
- Коллизия регистра ФС: файлы `schema/tables/dbo.SALES.json`, `dbo.CUSTOMERS.json`, `dbo.PRODUCTS.json` исправлены на боевые версии; демо-дубли — в `*__legacydemo.json`. Эталон — `schema/schema_raw.json`.

## 🗂️ Машиночитаемая схема

- `schema/schema_raw.json` — полная схема (колонки, типы, PK, FK, индексы).
- `schema/samples.json` — по 3 строки-примера на таблицу.
- `schema/tables/<dbo.ИМЯ>.json` — схема+примеры по одной таблице.
- `schema/tables_catalog.md` — каталог таблиц с числом строк; `schema/routines_list.md` — список 417 процедур/функций.

## Алфавитный указатель (таблица → домен)

| Таблица | Строк | Домен |
|---|--:|---|
| `dbo.ABCSCHEMEDETAILS` | 3 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.ABCSCHEMES` | 1 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.ACCESSIBLESALESAREAS` | 0 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.ACTUALROUTES` | 460,333 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.AGENTLOCATIONS` | 4,064,596 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.ASSETACCOUNTINGDETAILS` | 75 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.ASSETNUMBERS` | 23 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.ASSETS` | 23 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.ASSETSINVENTORYDETAILS` | 0 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.ASSIGNORS` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.ATTACHMENTS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.AWP` | 2 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.BANKAM` | 776 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.BARCODES` | 349 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.CALLS` | 21,343 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CARS` | 10 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.COMPILEDSCRIPTS` | 1 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.COMPLECTATIONDETAILS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.CONTEXTFUNCTIONDEF` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.CPACODES` | 1,536 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.CURR` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.CURREXCHG` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.CUSTCHANGEREQCONTACTS` | 135 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTCHANGEREQDELIVERYADDRESSES` | 0 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTCHANGEREQUESTS` | 4,373 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTCHANGEREQUESTSCONFIRMATIONS` | 4,373 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERACCOUNTSINBANK` | 5 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERB2BDATA` | 9,688 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERCONTACTS` | 1,816 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERDELIVERYADDRESSES` | 9,323 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERLIMITS` | 6,892 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMEROVERDEBTANALYSESCHEMEDETAILS` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.CUSTOMEROVERDEBTANALYSESCHEMES` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.CUSTOMERPREFERREDPRODUCTS` | 0 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERPREFERREDPRODUCTSDETAILS` | 0 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERPRICELISTDETAILS` | 224 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.CUSTOMERPRICELISTS` | 64,557 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.CUSTOMERS` | 9,688 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.Customers` | 0 | [Legacy/демо-таблицы](domains/legacy_demo.md) |
| `dbo.CUSTOMERSALESAREAS` | 9,691 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.CUSTOMERSPHERES` | 0 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.DATAVIEWDEF` | 2 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DATAVIEWSETTINGS` | 33 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DEPOSITSCHEMEDETAILS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.DEPOSITSCHEMES` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.DEVICES` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DIALOGVALUES` | 823 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DICTIONARYTS` | 9 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DIRECTORYACCESS` | 87 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DIRSLOG` | 119,544 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DISCHARGEDETAILS` | 426,456 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.DOCPARENTS` | 671,390 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.DOCUMENTACCESS` | 145 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.DOCUMENTEXTENSIONS` | 1 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.DOCUMENTS` | 1,232,337 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.DOCUMENTSLOG` | 1,951,948 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.EXCISETAXTARIFF` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.FOLDERACCESS` | 4 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.GEWAYBILLS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.GIFTPROMOTIONDETAILS` | 12 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.GIFTPROMOTIONS` | 982 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.HIAGENTPRODUCTS` | 1,766,029 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.HIAGENTSSUM` | 290,851 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HICUSTOMERSDEBT` | 783,621 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HICUSTOMERSSUM` | 15,427 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HIDEPOSITPRODUCTS` | 46,622 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.HIRESERVATION` | 233,161 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.HIRESTAGENTPRODUCTS` | 8,447 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.HIRESTAGENTSSUM` | 272 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HIRESTCUSTOMERSDEBT` | 368,804 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HIRESTCUSTOMERSSUM` | 4,621 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.HIRESTDEPOSITPRODUCTS` | 27,752 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.HIRESTRESERVATION` | 332 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.HIRESTSOLDPRODUCTS` | 1,339,481 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.HIRESTSTORAGES` | 1,170 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.HIRESTTRANSFERREDASSETS` | 31 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.HISOLDPRODUCTS` | 1,263,791 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.HISTORAGES` | 377,902 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.HITRANSFERREDASSETS` | 42 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.IBPAYMENTS` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.KITCOMPONENTS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.LAYOUTVALUES` | 5 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.MARKINGS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.NOTIDENTIFIEDB2BCUSTOMERS` | 0 | [Клиенты и торговые точки](domains/customers.md) |
| `dbo.ONETIMEAUTHENTICATIONDATA` | 13 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.ONLINEREPORTACCESS` | 44 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.ONLINEREPORTPARAMCONFIGS` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.ORDERS` | 328,587 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.ORGANIZATIONACCOUNTS` | 0 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.PARAMS` | 367 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.PAYMENTS` | 325,993 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.PLANNEDROUTEDOCS` | 7 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.PLANNEDROUTESLIST` | 1,081,449 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.PLANNEDROUTESLISTMOBILE` | 176,145 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.PRICELISTDETAILS` | 65 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRICELISTS` | 971 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRICESORDISCOUNTSLIMITS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODDISCCHANGEREQUESTS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTACCOUNTINGDETAILS` | 515,491 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.PRODUCTCONTAINERS` | 17 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTIMAGES` | 70 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTMOVEBETWEENVANAGENTSDETAILS` | 0 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.PRODUCTS` | 791 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.Products` | 0 | [Legacy/демо-таблицы](domains/legacy_demo.md) |
| `dbo.PRODUCTSACCESSSCHEMEDETAILS` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTSACCESSSCHEMES` | 0 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTSDISCOUNTS` | 6,663 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTSDISCOUNTSDETAILS` | 204 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTSINVENTORIES` | 4 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.PRODUCTSINVENTORYDETAILS` | 0 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.PRODUCTSMOVETAXDOCUMENTKEYS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.PRODUCTSRESERVATIONDETAILS` | 143,673 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.PRODUCTSSCALEDISCOUNTS` | 33 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PRODUCTSSCALEDISCOUNTSDETAILS` | 13 | [Товары, цены, скидки и акции](domains/products.md) |
| `dbo.PROVIDINGDELIVERIES` | 1,808,931 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.QUESTIONNAIREDETAILS` | 3 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.QUESTIONNAIRES` | 1 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.QUESTIONNAIRESACCESSSCHEMEDETAILS` | 1 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.QUESTIONNAIRESACCESSSCHEMES` | 1 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.QUESTIONS` | 3 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.RECONCILATIONDETAILS` | 1,327 | [Финансы: долги, платежи, взаиморасчёты](domains/finance.md) |
| `dbo.RESERVATIONSCHEMEDETAILS` | 1 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.RESERVATIONSCHEMES` | 1 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.RETURNORDERS` | 17 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.RETURNS` | 21,211 | [Заказы, возвраты и резервирование](domains/orders.md) |
| `dbo.ROLEACCESS` | 5 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.ROLEPARAMS` | 25 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.ROUTECHANGEREQUESTS` | 1 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.ROUTECHANGEREQUESTSDETAILS` | 32 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.ROUTETEMPLATES` | 50 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.ROUTETEMPLATESLIST` | 1,572 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.SaleDetails` | 0 | [Legacy/демо-таблицы](domains/legacy_demo.md) |
| `dbo.SALEDOCDETAILS` | 2,520,629 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALEDOCEXCHANGES` | 0 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALEDOCGIFTS` | 195,510 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALEDOCPRODUCTSONDEPOSIT` | 64,425 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALES` | 370,572 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.Sales` | 0 | [Legacy/демо-таблицы](domains/legacy_demo.md) |
| `dbo.SALESAGENTAREAS` | 205 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALESAGENTCARS` | 51 | [Маршруты, визиты и GPS](domains/routes.md) |
| `dbo.SALESAGENTCREATEDDOCUMENTBODIES` | 248,370 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALESAGENTCREATEDDOCUMENTS` | 248,370 | [Продажи (расходные накладные)](domains/sales.md) |
| `dbo.SALESAGENTDIVISIONS` | 787 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALESAGENTPREFEREDPRODUCTS` | 1,155 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALESAGENTPRODUCTSACCESS` | 846 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALESAGENTS` | 181 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALESAGENTVANAGENTSACCESS` | 298 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.SALETAXDOCUMENTKEYS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.SMS` | 0 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.STORAGES` | 16 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.STORAGESACCESSBYUSERS` | 114 | [Склад: остатки, движения, активы](domains/warehouse.md) |
| `dbo.STRINGRESOURCES` | 77 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.SUBSYSTEMACCESS` | 36 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.SURVEYDETAILS` | 3 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.TASKFOLLOWERSSCHEMEDETAILS` | 0 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.TASKFOLLOWERSSCHEMES` | 0 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.TAXDOCUMENTDETAILS` | 35 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.TEMPLATES` | 66 | [Документооборот, ЭДО и налоговые](domains/documents.md) |
| `dbo.TREEDEF` | 31 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.TREES` | 265 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.TREESLOG` | 52 | [Агенты, территории и оргструктура](domains/agents.md) |
| `dbo.USERACCESS` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.USERPARAMS` | 219 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.USERREPORTS` | 3 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.USERSCRIPTS` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.USERSMAPPING` | 0 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.USERSROLES` | 65 | [Система, доступы и служебные справочники](domains/system.md) |
| `dbo.VISITRESULTSACCESSSCHEMEDETAILS` | 1 | [Анкеты, опросы и задачи](domains/surveys.md) |
| `dbo.VISITRESULTSACCESSSCHEMES` | 1 | [Анкеты, опросы и задачи](domains/surveys.md) |

---
*Сгенерировано автоматически из живой схемы БД и `app_v2.py`. Обновление: перезапустить выгрузку схемы и мультиагентный воркфлоу документирования.*