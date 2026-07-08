# Справочник хранимых процедур и функций (ROUTINES)

База данных: **SalesManagement** (ERP «AS-Sales Management 7», MS SQL Server).
Всего программируемых объектов: **417** — сгруппированы по двум префиксам имён.

Источник имён: `docs/database/schema/routines_list.md`. Сигнатуры (параметры/возвращаемые
типы) в этом файле **не приводятся** — классификация выполнена по именам и по бизнес-логике,
восстановленной из `app_v2.py` и схемы БД. Там, где смысл однозначно не выводится из имени,
он помечен как предположительный.

> ⚠️ **READ-ONLY контекст проекта.** Дашборд работает только на `SELECT`. Процедуры группы
> `asp_*` изменяют данные боевой ERP (INSERT/UPDATE/DELETE) — **их запускать нельзя**.
> Практический интерес для аналитики представляют только функции `asf_*` (чтение/расчёт),
> и то как справочный материал: дашборд считает метрики собственными SQL-запросами, а не
> вызовом этих функций.

## Соглашение об именах

| Префикс | Тип объекта | Кол-во | Назначение |
|---|---|---|---|
| `asp_` | PROCEDURE | 349 | **AS-Sales Procedure** — операции записи: создание, редактирование, удаление сущностей и проводки регистров движений. Модифицируют данные. |
| `asf_` | FUNCTION | 68 | **AS-Sales Function** — расчётные/выборочные функции: остатки, цены, скидки, долги, доступы. Только чтение. |

Внутри `asp_` смысл кодируется вторым словом-глаголом: `add*` (создать), `edit*`
(изменить), `delete*` (удалить), `Store*` (провести в регистр), `Correct*`
(пересчитать/скорректировать остатки регистра), `set*` (установить флаг), `Update*`
(служебное обновление), `Get*` (единичные процедуры-выборки в наборе `asp_`).

---

## 1. Процедуры `asp_` (349) — операции записи

> Все процедуры этой группы изменяют состояние ERP. В проекте не используются и
> использоваться не должны. Ниже — карта назначения по подгруппам, чтобы понимать,
> какие таблицы и сущности они обслуживают.

### 1.1. CRUD справочников и документов (`add* / edit* / delete*`)

Основная масса процедур — стандартный CRUD над сущностями системы. Триплеты
`asp_add<Entity>` / `asp_edit<Entity>` / `asp_delete<Entity>` покрывают:

- **Клиенты и их атрибуты**: `addCustomer`, `editCustomer`, `deleteCustomer`,
  а также контакты (`CustomerContact`), адреса доставки (`CustomerDeliveryAddress`),
  банковские счета (`CustomerAccInBank`), лимиты (`CustomerLimit`), сферы
  (`CustomerSphere`), территории (`CustomerSalesArea`), прайс-листы
  (`CustomerPriceList`), предпочтительные товары (`CustomerPreferredProducts`),
  B2B-данные (`CustomerB2BData`), запросы на изменение (`CustomerChangeRequest`,
  `CustChangeReqContacts`, `CustChangeReqDeliveryAddresses`).
- **Товары и цены**: `addProduct`/`editProduct`/`deleteProduct`, штрихкоды
  (`BarCode`), контейнеры (`ProductContainer`), комплекты (`KitComponent`),
  прайс-листы (`PriceList`, `PriceListDetail`), скидки (`ProductsDiscounts`,
  `ProductsScaleDiscount`, `PricesOrDiscountsLimits`), схемы доступа к товарам
  (`ProductsAccessScheme`), бухгалтерские реквизиты (`ProductAccountingDetails`),
  учёт движения/налоговые ключи (`ProductsMoveTaxDocumentKey`), маркировка
  (`Marking`), акцизные тарифы (`ExciseTaxTariff`).
- **Торговые агенты**: `addSalesAgent`/`editSalesAgent`/`deleteSalesAgent`,
  их территории (`SalesAgentArea`, `SalesAgentAccessibleSalesArea`), дивизионы
  (`SalesAgentDivision`), машины (`SalesAgentCars`), доступ к товарам
  (`SalesAgentProductsAccess`), доступ к ван-агентам (`SalesAgentVanAgentsAccess`),
  предпочтительные товары (`SalesAgentPreferedProducts`).
- **Документы продаж и движения**: `addSale`/`deleteSale`, строки документа
  (`SaleDocDetail`), подарки (`SaleDocGift`), обмены (`SaleDocExchange`), товары
  на депозите (`SaleDocProductOnDeposit`), налоговые ключи документа
  (`SaleTaxDocumentKey`), заказы (`SalesOrder`), возвраты (`SalesReturn`,
  `SalesReturnOrder`), платежи (`addPayment`, `deletePayment`, `PaymentsFromIB`).
- **Маршруты и визиты**: шаблоны маршрутов (`RouteTemplates`,
  `RouteTemplatesList`, `CustomerToRouteTemplate`), плановые маршруты
  (`PlannedRoutesList`), документы маршрутов (`RouteDocuments`), запросы на
  изменение маршрута (`RouteChangeRequest`), звонки (`Calls`), SMS (`addSMS`).
- **Опросы и анкеты**: `Questionnaire`, `Question`, `QuestionnaireDetail`,
  `SurveyDetail`, схемы доступа к анкетам и результатам визитов
  (`QuestionnairesAccessScheme`, `VisitResultsAccessScheme`).
- **Логистика/склад/активы**: склады (`Storage`, `StoragesAccessByUsers`),
  инвентаризация (`ProductsInventory`, `AssetsInventoryDetail`), активы
  (`Asset`, `AssetAccountingDetails`, `AssetNumber`), резервирование
  (`ProductsReservationDetail`, `ReservationScheme`), депозиты
  (`DepositScheme`), комплектация (`ComplectationDetails`), обеспечение
  поставки (`ProvidingDelivery`).
- **Финансы/справочники**: валюты (`Curr`, `CurrRate`), банки (`Bank`),
  цессионарии (`Assignor`), CPA-коды (`CPACode`), счета организации
  (`OrganizationAccounts`), сверки (`ReconcilationDetail`, `DischargeDetails`),
  налоговые документы (`TaxDocumentDetails`).
- **Промоакции**: подарочные акции (`GiftPromotion`, `GiftPromotionDetail`),
  шкальные скидки (`ProductsScaleDiscount`).
- **Схемы анализа**: ABC-схемы (`ABCScheme`), схемы анализа перезадолженности
  клиентов (`CustomerOverDebtAnalyseScheme`), схемы исполнителей/подписчиков
  задач (`TaskExecutorsScheme`, `TaskFollowersScheme`).

Массовые удаления оформлены отдельными процедурами вида
`asp_deleteAll<Entity>Details` / `asp_delete<Entity>All<Child>` (например
`deleteCustomerAllContacts`, `deleteSaleDocAllDetails`,
`deleteSalesAgentAllCars`) — очистка всех дочерних строк родителя за один вызов.

### 1.2. Проводка и коррекция регистров движений (`Store* / Correct* / deleteHi*`)

Ядро учётной механики. Работает с регистрами `HI*` (History — движения) и
`HIREST*` (Rest — остатки). Именно эти регистры дашборд читает напрямую при
расчёте продаж и долга (см. `DEBT_CALCULATION_FORMULA.md`).

| Процедура | Регистр | Назначение (предположительно по имени) |
|---|---|---|
| `asp_StoreCustomersDebtOp` | HICUSTOMERSDEBT | Провести операцию долга клиента (дебет/кредит) |
| `asp_StoreCustomersSumOp` | HIRESTCUSTOMERSSUM | Провести сумму по клиенту (возвраты/предоплаты) |
| `asp_StoreAgentsSumOp` | HIAGENTSSUM | Провести денежный остаток агента |
| `asp_StoreAgentProductsOp` | HIAGENTPRODUCTS | Провести товарный остаток агента (ван) |
| `asp_StoreSoldProductsOp` | HISOLDPRODUCTS | Провести проданные товары |
| `asp_StoreStorageOp` | HISTORAGES | Провести движение по складу |
| `asp_StoreReservationOp` | HIRESERVATION | Провести резервирование |
| `asp_StoreDepositProductsOp` | HIDEPOSITPRODUCTS | Провести товары на депозите |
| `asp_StoreTransferAssetsOp` | HITRANSFERASSETS | Провести перемещение активов |

Каждому `Store*` соответствуют:
- `asp_Correct<Register>` (`CorrectHiRestCustomersDebt`, `CorrectHiRestCustomersSum`,
  `CorrectHiRestAgentsSum`, `CorrectHiRestAgentProducts`, `CorrectHiRestSoldProducts`,
  `CorrectHiRestStorages`, `CorrectHiRestReservation`, `CorrectHiRestDepositProducts`,
  `CorrectHiRestTransferAssets`) — пересчёт/сведение таблицы остатков `HIREST*`.
- `asp_deleteHi<Register>Rows` / `...Ops` (`deleteHiCustomersDebtRows`,
  `deleteHiCustomersSumRows`, `deleteHiAgentsSumRows`, `deleteHiAgentProductsOps`,
  `deleteHiSoldProductsOps`, `deleteHiStoragesRows`, `deleteHiReservationRows`,
  `deleteHiDepositProductsOps`, `deleteHiTransferAssetsOps`) — откат движений при
  удалении/переоформлении документа.

> 📌 Для аналитики это самый важный смысловой блок: он подтверждает, что баланс
> долга и товарные/денежные остатки формируются как проводки в `HI*`, а `HIREST*`
> хранит свёрнутые остатки. Наши отчёты читают эти таблицы напрямую.

### 1.3. Установка флагов «по умолчанию» (`set*`)

Управление признаком «значение по умолчанию» в дочерних коллекциях клиента —
процедуры обнуляют/переставляют флаг default:
`setCustomerDefaultAccountNotDefault`, `setCustomerDefaultContactNotDefault`,
`setCustomerDefaultSalesAreaNotDefault`, `setCustomerDefaultSphereNotDefault`,
`setCustomerDeliveryAddressDefault`.

### 1.4. Системные / конфигурационные процедуры

Обслуживают инфраструктуру приложения, а не бизнес-данные:

- **Блокировки/служебное**: `asp_AppLock` (прикладная блокировка),
  `asp_databaseSystemInfo` (сведения о БД), `asp_uncheckKit`.
- **Метаданные и справочник деревьев**: `AddTREEDEF`, `GetTreeDefTS`,
  `addTreeNode`, `editTreeNode`, `editTreeNodeLeaf`, `deleteTreeNode`,
  `CreateParentLink`, `addTreesLog`, `deleteTreesLog` — ведение иерархических
  справочников `TREES`/`TREEDEF` (территории, дивизионы, группы, регионы).
- **Представления данных/отчёты**: `AddDATAVIEWDEF`, `ChangeDefaultDATAVIEWDEF`,
  `ChangeEnabledDATAVIEWDEF`, `updateLastExecDateDATAVIEWDEF`, `deleteDATAVIEWDEF`,
  `AddUserReport`, `deleteUserReport`, `updateLastExecDateUserReport`,
  `addOnlineReportAccesses`, `addOnlineReportParamConfig` — настройка
  пользовательских отчётов и онлайн-отчётов.
- **Скрипты/функции контекста**: `AddUserScript`, `deleteUserScript`,
  `AddContextFunctionDefinition`, `ChangeEnabledContextFunctionDefinition`,
  `deleteContextFunctionDefinition`.
- **Локализация**: `AddStringResource` — строковые ресурсы.
- **Синхронизация мобильных устройств (van-sales)**: `UpdateDeviceSyncData`,
  `UpdateCloudDeviceSyncData`, `EditSalesAgentDeviceId`,
  `EditRegisteredInB2BMobile` — обмен с планшетами торговых агентов.
- **Права доступа**: `addRoleAccesses`/`editRoleAccesses`/`deleteRoleAccesses`,
  `addUsersRoles`/`deleteUsersRoles`, `addUserAccessItem`, `addDirectoryAccesses`,
  `addFolderAccesses`, `addDocumentAccesses`, `addSubsystemAccesses`,
  `addUserParam`, `AddAWP`/`deleteAWP` (рабочие места).
- **Точечные правки кодов/идентификаторов**: `EditActorID`, `EditB2BID`,
  `EditOnlyBankCode`, `EditOnlyCurrencyCode`, `EditOnlyCustomerCode`,
  `editOnlyProductCode`, `editSalesAgentCode`, `editQuestionCode` — изменение
  только кода/ID без остальных полей.
- **Служебные getter-процедуры** (возвращают набор, но лежат в `asp_`):
  `GetConditionalProductScaleDiscount`, `GetDictionatyTS` (TS = timestamp
  словаря для синхронизации), `GetProductSaleAndPriceListPrices`,
  `GetTreeDefTS`.
- **Смена статуса на «удалён»**: `UpdateDocStateToDeleted`,
  `UpdatePricesOrDiscountsLimitsStateToDeleted` — мягкое удаление
  (пометка), не физическое.

---

## 2. Функции `asf_` (68) — расчёт и выборка (только чтение)

Логически чистые функции: остатки, цены, скидки, долги, доступы. Формально
безопасны для чтения, но в дашборде **не вызываются** — метрики считаются
собственными запросами. Ценность блока — как документация бизнес-правил ERP.

### 2.1. Остатки регистров (`Get*Rem`, `Get*AccRem`) — ⭐ полезно для отчётности

Соответствуют регистрам из раздела 1.2. `AccRem` = accumulated remainder
(накопленный остаток на дату).

| Функция | Что возвращает (по имени) |
|---|---|
| `asf_GetProductRem` / `asf_GetProductAvailableRem` | Остаток товара / доступный к продаже остаток |
| `asf_GetAgentProductRem` | Товарный остаток у торгового агента (ван) |
| `asf_GetSoldProductRem` | Остаток проданных товаров |
| `asf_GetStorageAccRem` | Накопленный остаток по складу |
| `asf_GetAssetRem` | Остаток актива |
| `asf_GetDepositProductRem` | Остаток товара на депозите |
| `asf_GetReservationAccRem` | Накопленный зарезервированный остаток |
| `asf_GetSaleRem` | Остаток по продаже |
| `asf_GetAgentsSumAccRem` | Накопленный денежный остаток агента |
| `asf_GetCustomersSumAccRem` | Накопленная сумма по клиенту (возвраты/предоплаты) |
| `asf_GetCustomersDebtAccRem` | **Накопленный долг клиента на дату** |

> ⭐ `asf_GetCustomersDebtAccRem`, `asf_GetCustomersSumAccRem` описывают ту же
> механику, что заложена в `DEBT_CALCULATION_FORMULA.md` (долг = дебет − |возвраты|
> − |предоплата|). Полезны как справка при сверке долга.

### 2.2. Долги и платежи — ⭐ полезно для отчётности

- `asf_GetDebtDocRem` — остаток по долговому документу (недопогашенная сумма).
- `asf_GetDebtDocuments` — список долговых документов (табличная функция).
- `asf_ExistsPaymentDischargeForDebtDoc` — есть ли погашение по долговому документу.
- `asf_ExistsPaymentDischargeForSale` — есть ли погашение по продаже.

### 2.3. Цены, скидки, прайс-листы

Расчёт продажной цены и скидок для клиента/товара — ядро ценообразования:

- **Цены**: `asf_GetCustomerSalePrice`, `asf_GetCustomerSalePrices`,
  `asf_GetProductSalePrice`, `asf_GetProductsSalePrices`,
  `asf_GetProductSalePriceFromPriceList`, `asf_GetProductsSalePriceFromPriceList`,
  `asf_GetProductsSalePricesFromPriceList`, `asf_GetProductsCustomerSalePrices`,
  `asf_GetProductsSaleAndPriceListPrices`.
- **Скидки**: `asf_GetProductDiscount`, `asf_GetProductsDiscounts`,
  `asf_GetProductsScaleDiscounts`, `asf_GetPriceOrDiscountLimit`,
  `asf_GetProductCodesForExistingScaleDiscount`.
- **Прайс-листы**: `asf_GetPriceListTypes`.

### 2.4. Промоакции и подарки

- `asf_GetGiftPromotions`, `asf_GetProductGiftPromotions`,
  `asf_GetExistingGiftPromotionProductCodes` — подарочные акции.
- `asf_ExistsProductSalePromotion` — есть ли действующая промоакция на товар.

### 2.5. Резервирование

- `asf_GetAvailableReserveQuantities` — доступные к резерву количества.
- `asf_GetReservationsQuantities` — количества по резервированиям.
- `asf_GetProductReservedQuantity` — зарезервированное количество товара.

### 2.6. Торговые агенты и визиты — ⭐ полезно для отчётности

Суммы по визиту агента и его рабочие наборы (аналитика продаж по агентам):

- **Итоги визита**: `asf_GetAgentVisitSalesSum`, `asf_GetAgentVisitOrdersSum`,
  `asf_GetAgentVisitReturnsSum`, `asf_GetAgentVisitPaymentsSum`,
  `asf_GetOrdersSumInCalls`.
- **Рабочие наборы агента**: `asf_GetSalesAgentCustomers`,
  `asf_GetSalesAgentAreas`, `asf_GetSalesAgentAccessibleAreas`,
  `asf_GetSalesAgentAccessProducts`, `asf_GetSalesAgentDivisionsTrees`,
  `asf_GetSalesAgentVanAgentsAccess`, `asf_GetSalesAgentQuestionnaires`,
  `asf_GetSalesAgentQuestions`, `asf_GetSalesAgentVisitResults`.
- **Активные группы клиентов агента**: `asf_GetSalesAgentActiveCustomersGroups`,
  `asf_GetSalesAgentActiveCustomersGiftPromotionsGroups`,
  `asf_GetSalesAgentActiveCustomersScaleDiscountGroups`.

### 2.7. Клиенты и товары (выборки)

- `asf_GetCustomersBySalesArea` — клиенты по территории продаж.
- `asf_GetCustomersBySpheres` — клиенты по сферам деятельности.
- `asf_GetProducts` — выборка товаров.
- `asf_GetProductGiftPromotions`, `asf_GetProductCodesForExistingScaleDiscount`
  (см. также разделы 2.3–2.4).

### 2.8. Справочники, деревья, утилиты

- `asf_GetTreeLeaves`, `asf_GetTreeLeavesT` — листья иерархии `TREES` (территории,
  дивизионы, группы). Полезно для разворачивания кодов справочников в наборы.
- `asf_GetRekvValue`, `asf_UpdateRekvValue` — доступ к «реквизитам» (доп. атрибутам
  сущностей). Пара, несмотря на префикс `asf_`, `Update*` предполагает побочный
  эффект — использовать с осторожностью.
- `asf_Split_to_table` — утилита-парсер строки в таблицу (split по разделителю).
  Часто используется для передачи списков ID в другие процедуры/функции.
- `asf_GetDocNumberByDocISN` — номер документа по его `fISN`.
- `asf_CheckRemOnlyByNumber` — проверка остатка по номеру (предположительно).

### 2.9. Доступы и права (выборки)

- `asf_GetUserAccessOnlineReports` — доступные пользователю онлайн-отчёты.
- `asf_GetUserAccessStorages` — доступные пользователю склады.
- `asf_GetSalesAgentAccessProducts`, `asf_GetSalesAgentAccessibleAreas`
  (см. 2.6).
- `asf_ExistsProductsInventoriesByCreationTypeId` — наличие инвентаризаций товара
  по типу создания.

### 2.10. Прочие проверки существования (`Exists*`)

Булевы функции-предикаты, применяются в валидации: `ExistsPaymentDischargeForDebtDoc`,
`ExistsPaymentDischargeForSale`, `ExistsProductSalePromotion`,
`ExistsProductsInventoriesByCreationTypeId` (перечислены выше по темам).

---

## 3. Итоговая карта пригодности для аналитики

| Группа | Тип | Для отчётов дашборда |
|---|---|---|
| `asp_add* / edit* / delete*` | запись | ❌ запрещено (мутации ERP) |
| `asp_Store* / Correct* / deleteHi*` | запись | ❌ запрещено; ценно как документация механики регистров `HI*`/`HIREST*` |
| `asp_set* / Update* / системные` | запись/служебное | ❌ запрещено |
| `asf_Get*Rem / *AccRem` | чтение | ⭐ справка по остаткам и долгу (дашборд считает сам) |
| `asf_GetDebt* / GetCustomersDebtAccRem` | чтение | ⭐ сверка формулы долга |
| `asf_GetAgentVisit*Sum` | чтение | ⭐ аналитика продаж по агентам |
| `asf_GetCustomersBy* / GetTreeLeaves*` | чтение | ⭐ разворачивание территорий/групп/сфер |
| `asf_Get*Price / *Discount*` | чтение | ℹ️ логика ценообразования (справочно) |

> **Важно.** Даже read-only функции `asf_*` в проекте не вызываются: чтобы
> сохранить единый контроль над SQL и производительностью, дашборд формирует
> запросы к таблицам напрямую. Настоящий файл — навигационная карта по 417
> объектам БД, а не руководство к их вызову.
