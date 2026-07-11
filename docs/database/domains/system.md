# Система, доступы и служебные справочники

Домен объединяет служебную «платформенную» часть ERP AS-Sales Management 7: пользователей и их роли, матрицу прав доступа (по подсистемам, справочникам, документам, папкам и онлайн-отчётам), пользовательские и ролевые параметры, локализацию строк, определения деревьев/представлений/отчётов, скомпилированные скрипты, устройства мобильных агентов и служебные журналы/метаданные. Эти таблицы описывают не бизнес-операции (продажи, долги, остатки), а конфигурацию самой платформы и разграничение доступа к ней. В аналитическом приложении `app_v2.py` этот домен напрямую не используется (дашборд читает данные продаж, клиентов и долгов), однако таблицы важны для понимания модели прав, локализации подписей (`STRINGRESOURCES`) и определения справочников-деревьев (`TREEDEF` ↔ `TREES.fTREEID`), от которых зависят соседние домены.

Ключевые сквозные идентификаторы домена:
- `fUSERID` (int) — пользователь системы; встречается почти во всех «пользовательских» таблицах и в бизнес-журналах.
- `fROLE` / `fCODE` (nvarchar(3)) — код роли доступа; связывает `ROLEACCESS` с матрицами прав.
- `fSUBSYSTEMID` / `fDIRECTORYTYPE` / `fDOCTYPE` / `fCOMMANDID` — числовые коды объектов платформы (подсистема, тип справочника, тип документа, команда/папка).
- `fNAME` (символьный код) — имя определения (представление, отчёт, дерево, ресурс, AWP), нередко ссылается на `fCAPTIONID` → `STRINGRESOURCES.fNAME` для локализованной подписи.

---

## dbo.USERSROLES  (65 строк)

- Назначение: связка «пользователь ↔ роль доступа». Определяет, какую роль (`fROLE`) имеет пользователь (`fUSERID`); уникальный индекс по `fUSERID` фактически задаёт одну роль на пользователя.
- Ключевая таблица разграничения прав: через роль пользователь получает наборы разрешений из `SUBSYSTEMACCESS`, `DIRECTORYACCESS`, `DOCUMENTACCESS`, `FOLDERACCESS`, `ONLINEREPORTACCESS`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fUSERID | int | нет | Идентификатор пользователя системы |

- Ключи и связи: PK (`fROLE`, `fUSERID`), кластерный; уникальный индекс `I_USERSROLES1` по `fUSERID` (один пользователь — одна роль). Неявные связи: `fROLE → ROLEACCESS.fCODE`; `fUSERID` — тот же пользователь, что в `USERPARAMS`, `DIALOGVALUES`, `DIRSLOG.fUSERID`, `SALESAGENTS.fUSERID` и др.

## dbo.ROLEACCESS  (5 строк)

- Назначение: справочник ролей доступа (профилей прав). Каждая роль — код `fCODE` и её описание `fCOMMENT` (напр. `000` — «Համակարգի ադմինիստրատոր», системный администратор).
- Корневой справочник модели безопасности: на код роли ссылаются все таблицы `*ACCESS` и `SUBSYSTEMACCESS`/`ROLEPARAMS`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fCODE | nvarchar(3) | нет | Код роли (PK, напр. `000`, `001`) |
| fCOMMENT | nvarchar(50) | нет | Наименование/описание роли |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fCODE`. Неявно ссылаются: `USERSROLES.fROLE`, `SUBSYSTEMACCESS.fROLE`, `DIRECTORYACCESS.fROLE`, `DOCUMENTACCESS.fROLE`, `FOLDERACCESS.fROLE`, `ONLINEREPORTACCESS.fROLE`, `ROLEPARAMS.fROLE`.

## dbo.SUBSYSTEMACCESS  (36 строк)

- Назначение: разрешения роли на подсистемы приложения. Строка = «роль `fROLE` имеет доступ к подсистеме `fSUBSYSTEMID`»; `fPARTLY` отмечает частичный (ограниченный) доступ.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fSUBSYSTEMID | smallint | нет | Код подсистемы платформы |
| fPARTLY | bit | нет | Частичный доступ (1) против полного (0) |

- Ключи и связи: PK (`fROLE`, `fSUBSYSTEMID`), кластерный. Неявная связь: `fROLE → ROLEACCESS.fCODE`. `fSUBSYSTEMID` — тот же код подсистемы, что и `USERACCESS.fSUBSYSTEM`.

## dbo.USERACCESS  (0 строк)

- Назначение: точечные (пер-пользовательские) переопределения прав на уровне подсистемы/объекта — разрешение на операцию (`fALLOWOPERATION`) и на просмотр (`fALLOWVIEW`). В текущей БД пустая (права заданы только на уровне ролей).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fUSERID | int | нет | Пользователь, которому назначено право |
| fSUBSYSTEM | smallint | нет | Код подсистемы (ср. `SUBSYSTEMACCESS.fSUBSYSTEMID`) |
| fACCESSID | smallint | нет | Код объекта/операции внутри подсистемы |
| fALLOWOPERATION | bit | нет | Разрешена операция (изменение) |
| fALLOWVIEW | bit | нет | Разрешён просмотр |

- Ключи и связи: PK (`fUSERID`, `fSUBSYSTEM`, `fACCESSID`), кластерный. Неявная связь: `fUSERID → USERSROLES.fUSERID`.

## dbo.DIRECTORYACCESS  (87 строк)

- Назначение: права роли на справочники (directories) с раздельными флагами добавления/редактирования/просмотра/удаления. `fDIRECTORYTYPE` — числовой тип справочника (напр. 51, 52, 53 …).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fDIRECTORYTYPE | tinyint | нет | Тип справочника |
| fALLOWADD | bit | нет | Разрешено добавление |
| fALLOWEDIT | bit | нет | Разрешено редактирование |
| fALLOWVIEW | bit | нет | Разрешён просмотр |
| fALLOWDELETE | bit | нет | Разрешено удаление |

- Ключи и связи: PK (`fROLE`, `fDIRECTORYTYPE`), кластерный. Неявная связь: `fROLE → ROLEACCESS.fCODE`.

## dbo.DOCUMENTACCESS  (145 строк)

- Назначение: права роли на типы документов (`fDOCTYPE`) с флагами add/edit/view/delete. Определяет, какие виды документов (продажа, заказ, возврат и т.д.) роль может создавать, редактировать, видеть и удалять.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDOCTYPE | tinyint | нет | Тип документа (код `fDOCTYPE` из документного домена) |
| fALLOWADD | bit | нет | Разрешено создание документа |
| fALLOWEDIT | bit | нет | Разрешено редактирование |
| fALLOWVIEW | bit | нет | Разрешён просмотр |
| fALLOWDELETE | bit | нет | Разрешено удаление |
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |

- Ключи и связи: PK (`fROLE`, `fDOCTYPE`), кластерный. Неявные связи: `fROLE → ROLEACCESS.fCODE`; `fDOCTYPE` — тот же код типа документа, что в `DOCUMENTS`/`SALES` документного домена.

## dbo.FOLDERACCESS  (4 строки)

- Назначение: доступ роли к «папкам»/командам интерфейса (folder commands). Строка = «роль `fROLE` видит команду/папку `fCOMMANDID`».

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fCOMMANDID | smallint | нет | Код команды/папки интерфейса |

- Ключи и связи: PK (`fROLE`, `fCOMMANDID`), кластерный. Неявная связь: `fROLE → ROLEACCESS.fCODE`.

## dbo.ONLINEREPORTACCESS  (44 строки)

- Назначение: доступ роли к шаблонам онлайн-отчётов. Строка = «роль `fROLE` имеет доступ к шаблону отчёта `fTEMPLATECODE`».

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fTEMPLATECODE | nvarchar(20) | нет | Код шаблона онлайн-отчёта |

- Ключи и связи: PK (`fROLE`, `fTEMPLATECODE`), кластерный. Неявные связи: `fROLE → ROLEACCESS.fCODE`; `fTEMPLATECODE` соотносится с `ONLINEREPORTPARAMCONFIGS.fTEMPLATENAME`.

## dbo.ONLINEREPORTPARAMCONFIGS  (0 строк)

- Назначение: конфигурация параметров шаблонов онлайн-отчётов (какие параметры доступны/настроены для шаблона). В текущей БД пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fTEMPLATENAME | nvarchar(20) | нет | Имя/код шаблона онлайн-отчёта |
| fPARAMCODE | nvarchar(30) | нет | Код параметра шаблона |

- Ключи и связи: PK (`fTEMPLATENAME`, `fPARAMCODE`), кластерный. Неявная связь: `fTEMPLATENAME` ↔ `ONLINEREPORTACCESS.fTEMPLATECODE`.

## dbo.PARAMS  (367 строк)

- Назначение: глобальные системные параметры платформы в виде «ключ→значение» (`fPARID` → `fVALUE`). Хранит общесистемные настройки (флаги, коды, числовые/строковые значения).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPARID | smallint | нет | Числовой код параметра (PK) |
| fVALUE | nvarchar(255) | нет | Значение параметра (строкой; напр. `True`, `1`, `715040`) |

- Ключи и связи: PK `fPARID`. Логически соотносится с `USERPARAMS.fPARAMID` (переопределение того же параметра на уровне пользователя) и `ROLEPARAMS.fPARAMID` (на уровне роли).

## dbo.USERPARAMS  (219 строк)

- Назначение: пользовательские параметры/настройки — переопределения системных значений для конкретного пользователя (`fUSERID` + `fPARAMID` → `fVALUE`).

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fUSERID | int | нет | Пользователь системы |
| fPARAMID | smallint | нет | Код параметра (ср. `PARAMS.fPARID`) |
| fVALUE | nvarchar(255) | нет | Значение параметра для пользователя |

- Ключи и связи: PK (`fUSERID`, `fPARAMID`), кластерный. Неявные связи: `fUSERID → USERSROLES.fUSERID`; `fPARAMID` ↔ `PARAMS.fPARID`.

## dbo.ROLEPARAMS  (25 строк)

- Назначение: параметры на уровне роли — значения настроек, общие для всех пользователей роли (`fROLE` + `fPARAMID` → `fVALUE`). Примеры значений: границы дат (`20150101`, `20261231`), числовые флаги.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fPARAMID | smallint | нет | Код параметра (ср. `PARAMS.fPARID`) |
| fROLE | nvarchar(3) | нет | Код роли → `ROLEACCESS.fCODE` |
| fVALUE | nvarchar(255) | нет | Значение параметра для роли |

- Ключи и связи: PK (`fROLE`, `fPARAMID`), кластерный. Неявные связи: `fROLE → ROLEACCESS.fCODE`; `fPARAMID` ↔ `PARAMS.fPARID`.

## dbo.DIALOGVALUES  (823 строки)

- Назначение: сохранённые значения диалогов/фильтров пользователя (последние параметры экранов и отчётов). Тело `fBODY` — текстовый блок «ключ:значение» (напр. `STARTDATE:20230901`, `DIVISION:000000`), запоминающий состояние диалога по имени `fNAME`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fUSERID | int | нет | Пользователь (0 — общесистемные/дефолтные значения) |
| fNAME | nvarchar(30) | нет | Имя диалога/экрана (напр. `ABCAnalyse`, `ALLDOCUMENT`) |
| fSTORE | smallint | нет | Признак/область хранения набора значений |
| fBODY | nvarchar(max) | нет | Сериализованные параметры диалога (строки `КЛЮЧ:значение`) |

- Ключи и связи: PK (`fUSERID`, `fNAME`), кластерный. Неявная связь: `fUSERID → USERSROLES.fUSERID`.

## dbo.LAYOUTVALUES  (5 строк)

- Назначение: сохранённые макеты интерфейса пользователя (настройки таблиц/гридов). `fBODY` — Base64-сериализованный XML-макет (`XtraSerializer … GridControl`) конкретного экрана `fNAME`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fUSERID | int | нет | Пользователь-владелец макета |
| fNAME | nvarchar(50) | нет | Имя экрана/провайдера (напр. `Sales`, `CustomersRems`) |
| fCAPTION | nvarchar(50) | нет | Подпись макета (напр. «Ռութերի գրանցում») |
| fTYPE | tinyint | нет | Тип макета/элемента управления |
| fBODY | nvarchar(max) | нет | Сериализованный (Base64/XML) макет грида |

- Ключи и связи: PK (`fUSERID`, `fNAME`, `fCAPTION`), кластерный. Неявная связь: `fUSERID → USERSROLES.fUSERID`.

## dbo.STRINGRESOURCES  (77 строк)

- Назначение: локализация строковых ресурсов (подписи UI/отчётов) по имени `fNAME` и культуре `fCULTURE` (`HY-AM`, `EN-US`). Значение — `fVALUE`. Используется для перевода `fCAPTIONID` определений (AWP, DATAVIEWDEF, USERREPORTS, TREEDEF) в человекочитаемый текст.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(50) | нет | Имя ресурса/ключ подписи (ср. `*.fCAPTIONID`) |
| fCULTURE | nvarchar(6) | нет | Код культуры/языка (`HY-AM`, `EN-US`) |
| fSYSTEM | bit | нет | Системный ресурс (1) против пользовательского (0) |
| fVALUE | nvarchar(500) | нет | Локализованный текст |

- Ключи и связи: PK (`fNAME`, `fCULTURE`), кластерный. Неявные связи: `fNAME` ← `fCAPTIONID`/`fNODECAPTIONID`/`fCODECAPTIONID` из `TREEDEF`, `AWP`, `DATAVIEWDEF`, `USERREPORTS`, `DATAVIEWSETTINGS`.

## dbo.DATAVIEWSETTINGS  (33 строки)

- Назначение: пользовательские настройки представлений данных (набор и порядок колонок для дата-провайдера). `fDEFINITION` — XML `<ViewSetting>…<Columns>…` для экрана `fNAME` внутри группы `fGROUP`, владелец — `fOWNERUSERID`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | smallint | нет | Идентификатор настройки (часть PK) |
| fNAME | nvarchar(20) | нет | Имя представления/провайдера (напр. `Customers`) |
| fGROUP | nvarchar(20) | нет | Группа/контекст представления |
| fCAPTION | nvarchar(50) | нет | Подпись настройки |
| fOWNERUSERID | int | нет | Владелец настройки (пользователь; 0 — общесистемная) |
| fDEFINITION | nvarchar(max) | да | XML-определение колонок представления |

- Ключи и связи: PK (`fID`, `fOWNERUSERID`, `fNAME`, `fGROUP`), кластерный. Неявные связи: `fOWNERUSERID → USERSROLES.fUSERID`; `fNAME`/`fGROUP` соотносятся с `DATAVIEWDEF.fNAME`/`fGROUP`.

## dbo.DATAVIEWDEF  (2 строки)

- Назначение: определения пользовательских представлений данных (view definitions) с вычисляемыми колонками. `fDEFINITION` — XML `<View>…<CalculatedColumns>…`. Хранит метаданные: включено (`fENABLED`), системное (`fSYSTEM`), по умолчанию (`fDEFAULT`), статистику выполнения.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(20) | нет | Имя представления (напр. `UD_DepRem`) |
| fCAPTIONID | nvarchar(50) | нет | Ключ подписи → `STRINGRESOURCES.fNAME` |
| fGROUP | nvarchar(20) | нет | Группа/дата-провайдер (напр. `ProductsOpsByCust`) |
| fENABLED | bit | нет | Представление включено |
| fSYSTEM | bit | нет | Системное представление |
| fDEFAULT | bit | нет | Представление по умолчанию |
| fDEFINITION | nvarchar(max) | да | XML-определение представления и вычисляемых колонок |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fUPDATEDATE | datetime | нет | Дата последнего изменения определения |
| fLASTEXEC | datetime | нет | Дата последнего выполнения |
| fEXECCOUNT | int | нет | Счётчик выполнений |

- Ключи и связи: PK (`fGROUP`, `fNAME`), кластерный. Неявные связи: `fCAPTIONID → STRINGRESOURCES.fNAME`; `fNAME`/`fGROUP` ↔ `DATAVIEWSETTINGS`.

## dbo.USERREPORTS  (3 строки)

- Назначение: определения пользовательских отчётов. `fDATAPROVIDER` указывает источник данных (напр. `SalesRems`, `SalesAnalyse`), `fDEFINITION` — XML `<UserReport …>` со способом показа (Excel/Folder). Ведётся статистика выполнения.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(20) | нет | Имя отчёта (PK, напр. `UD_SalesRems`) |
| fCAPTIONID | nvarchar(50) | нет | Ключ подписи → `STRINGRESOURCES.fNAME` |
| fDATAPROVIDER | nvarchar(50) | нет | Дата-провайдер/источник данных отчёта |
| fSYSTEM | bit | нет | Системный отчёт |
| fDEFINITION | nvarchar(max) | да | XML-определение отчёта |
| fUPDATEDATE | datetime | нет | Дата последнего изменения |
| fLASTEXEC | datetime | нет | Дата последнего выполнения |
| fEXECCOUNT | int | нет | Счётчик выполнений |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fNAME`. Неявные связи: `fCAPTIONID → STRINGRESOURCES.fNAME`; имя отчёта используется как `Command` в `AWP.fDEFINITION` (напр. `UD_SalesRems`).

## dbo.AWP  (2 строки)

- Назначение: определения АРМ (Automated Work Place, «рабочее место») — конфигурация экрана/панели пользователя из команд и отчётов. `fDEFINITION` — XML `<AWP>…<AWPLeaf Command="…">`, связывающий рабочее место с командами и пользовательскими отчётами.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(20) | нет | Имя АРМ (PK, напр. `AWPBO`, `StockkeeperAWP`) |
| fCAPTIONID | nvarchar(50) | нет | Ключ подписи → `STRINGRESOURCES.fNAME` |
| fSYSTEM | bit | нет | Системный АРМ |
| fDEFINITION | nvarchar(max) | да | XML-определение рабочего места (команды/отчёты) |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fNAME`. Неявные связи: `fCAPTIONID → STRINGRESOURCES.fNAME`; команды в `fDEFINITION` ссылаются на `USERREPORTS.fNAME` и коды команд/папок.

## dbo.CONTEXTFUNCTIONDEF  (0 строк)

- Назначение: определения контекстных функций (действий в контексте объекта/группы) — имя, группа, порядок, включённость и XML-определение. В текущей БД пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(20) | нет | Имя контекстной функции |
| fCAPTIONID | nvarchar(50) | нет | Ключ подписи → `STRINGRESOURCES.fNAME` |
| fGROUP | nvarchar(20) | нет | Группа/контекст функции |
| fENABLED | bit | нет | Функция включена |
| fORDER | tinyint | нет | Порядок отображения |
| fDEFINITION | nvarchar(max) | да | XML-определение функции |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK (`fGROUP`, `fNAME`), кластерный. Неявная связь: `fCAPTIONID → STRINGRESOURCES.fNAME`.

## dbo.USERSCRIPTS  (0 строк)

- Назначение: исходные пользовательские скрипты (расширения логики) — имя, подпись и текст определения. В текущей БД пустая; скомпилированный результат хранится в `COMPILEDSCRIPTS`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(32) | нет | Имя скрипта (PK) |
| fCAPTION | nvarchar(150) | нет | Подпись/описание скрипта |
| fDEFINITION | nvarchar(max) | да | Исходный текст скрипта |
| fTS | timestamp | нет | Версия строки (rowversion) |

- Ключи и связи: PK `fNAME`. Логически связан с `COMPILEDSCRIPTS` (исходник → сборка).

## dbo.COMPILEDSCRIPTS  (1 строка)

- Назначение: скомпилированные сборки пользовательских скриптов (бинарный образ .NET-сборки) с именем и версией. Хранит рантайм-артефакт логики платформы.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fASSEMBLYNAME | nvarchar(50) | нет | Имя сборки (напр. `CompiledScripts`) |
| fVERSION | nvarchar(20) | нет | Версия сборки (напр. `7.15.4.0`) |
| fSCRIPT | varbinary(max) | нет | Бинарный образ скомпилированной сборки |

- Ключи и связи: PK (`fASSEMBLYNAME`, `fVERSION`), кластерный. Логически связан с `USERSCRIPTS` (источник компиляции).

## dbo.TREEDEF  (31 строка)

- Назначение: определения деревьев/справочников платформы — метаописание каждого типа дерева (`fNAME`), используемого в таблице узлов `TREES`. Задаёт длину кода (`fCODELEN`), подписи (`fCAPTIONID`, `fNODECAPTIONID`, `fCODECAPTIONID`), многоуровневость (`fMULTILEVEL`), признаки системности, логирования и доступности закрытия. Примеры `fNAME`: `AssetGrp`, `CsDscGrp` — им соответствуют значения `TREES.fTREEID`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fNAME | nvarchar(8) | нет | Код/имя дерева (PK) → `TREES.fTREEID` |
| fCAPTIONID | nvarchar(50) | нет | Ключ подписи дерева → `STRINGRESOURCES.fNAME` |
| fNODECAPTIONID | nvarchar(50) | нет | Ключ подписи узла → `STRINGRESOURCES.fNAME` |
| fCODECAPTIONID | nvarchar(50) | нет | Ключ подписи кода → `STRINGRESOURCES.fNAME` |
| fCODELEN | smallint | нет | Длина кода узла (символов) |
| fCAPTIONLEN | smallint | нет | Максимальная длина наименования узла |
| fMULTILEVEL | bit | нет | Многоуровневое (иерархическое) дерево |
| fPREFIXBASED | bit | нет | Иерархия по префиксу кода |
| fDOCUMENTBASED | bit | нет | Дерево, основанное на документах |
| fSYSTEM | bit | нет | Системное дерево |
| fISLOGAVAILABLE | bit | нет | Доступно логирование изменений |
| fISCLOSEDAVAILABLE | bit | нет | Доступен признак «закрыт» для узлов |
| fATTACHMENTSTYPES | tinyint | нет | Разрешённые типы вложений |
| fEXTENSION | nvarchar(max) | да | XML-расширение определения дерева |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fSPECCAPTIONID | nvarchar(50) | нет | Ключ подписи спец-поля → `STRINGRESOURCES.fNAME` |
| fSPECLEN | smallint | нет | Длина спец-поля |
| fNEXTCODEPARAMID | smallint | да | Код параметра автогенерации следующего кода |
| fCACHEID | smallint | да | Идентификатор кэша дерева |

- Ключи и связи: PK `fNAME`. Ключевая связь домена с соседними: `TREEDEF.fNAME → TREES.fTREEID` (метаопределение для узлов деревьев `SArea`, `CustGrp`, `Division` и др., используемых в доменах агентов/клиентов/продаж). Подписи `fCAPTIONID`/`fNODECAPTIONID` → `STRINGRESOURCES.fNAME`.

## dbo.DICTIONARYTS  (9 строк)

- Назначение: служебные версии/метки времени справочников (`fID` → `fVALUE` + `fTS`). Используется платформой для контроля актуальности кэшей справочников при синхронизации.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | smallint | нет | Идентификатор справочника/кэша (PK) |
| fTS | timestamp | нет | Версия строки (rowversion) |
| fVALUE | smallint | нет | Служебное значение версии/состояния |

- Ключи и связи: PK `fID`. Соотносится с `TREEDEF.fCACHEID` (идентификаторы кэшей справочников-деревьев).

## dbo.DIRSLOG  (119 544 строки)

- Назначение: журнал изменений справочников (directory log / аудит). Каждая строка — операция над элементом справочника: тип справочника `fID`, элемент `fITEMID`, дата `fDATE`, пользователь `fUSERID`, код операции `fOP`, имя ПК `fCOMPNAME`, текстовое описание изменения `fCHANGEDESCRIPTION`.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | tinyint | нет | Тип справочника (часть PK; ср. `DIRECTORYACCESS.fDIRECTORYTYPE`) |
| fITEMID | int | нет | Идентификатор изменённого элемента справочника |
| fDATE | datetime | нет | Дата/время операции (default `getdate()`) |
| fUSERID | int | нет | Пользователь, выполнивший операцию |
| fOP | tinyint | нет | Код операции (напр. 2, 4, 16 — изменение/действие) |
| fCOMMENT | nvarchar(255) | да | Комментарий к операции |
| fCOMPNAME | nvarchar(32) | нет | Имя компьютера/рабочей станции |
| fCHANGEDESCRIPTION | nvarchar(max) | да | Текстовое описание изменения (старое → новое) |
| fSOURCE | smallint | нет | Источник операции (канал/подсистема) |

- Ключи и связи: PK (`fID`, `fITEMID`, `fDATE`, `fOP`), кластерный; индекс `iDIRSLOG1` по `fUSERID`. Неявные связи: `fUSERID → USERSROLES.fUSERID`; `fID` — тип справочника (тот же домен кодов, что `DIRECTORYACCESS.fDIRECTORYTYPE`).

## dbo.DEVICES  (0 строк)

- Назначение: реестр мобильных устройств ван-агентов и метки времени синхронизации (последняя выгрузка/загрузка данных и документов, полная загрузка). В текущей БД пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fDEVICEID | uniqueidentifier | нет | Идентификатор устройства (PK) → `SALESAGENTS.fDEVICEID` |
| fLASTUPLOADTIME | datetime | да | Время последней выгрузки данных с устройства |
| fDOCLASTDOWNLOADTIME | datetime | да | Время последней загрузки документов на устройство |
| fLASTDOWNLOADTIME | datetime | да | Время последней загрузки данных на устройство |
| fFULLDATADOWNLOADTIME | datetime | да | Время последней полной загрузки данных |

- Ключи и связи: PK `fDEVICEID`. Неявная связь: `fDEVICEID → SALESAGENTS.fDEVICEID` (устройство агента).

## dbo.ONETIMEAUTHENTICATIONDATA  (13 строк)

- Назначение: одноразовые данные аутентификации (токены/GUID) пользователей с датой выдачи. Используется для разовой авторизации/входа мобильных клиентов.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fID | uniqueidentifier | нет | Одноразовый токен/идентификатор аутентификации |
| fUSERID | smallint | нет | Пользователь (напр. -1 — служебный/анонимный) |
| fDATE | datetime | нет | Дата/время выдачи токена |

- Ключи и связи: PK `fUSERID`, кластерный; уникальный индекс по `fID`. Неявная связь: `fUSERID → USERSROLES.fUSERID`.

## dbo.USERSMAPPING  (0 строк)

- Назначение: сопоставление внутренних и внешних идентификаторов пользователей (для интеграций/обмена). Строка = «внутренний `fINNERUSERID` ↔ внешний `fOUTERUSERID`». В текущей БД пустая.

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| fINNERUSERID | int | нет | Внутренний идентификатор пользователя системы |
| fOUTERUSERID | int | нет | Внешний идентификатор пользователя (интеграция) |

- Ключи и связи: PK `fOUTERUSERID`, кластерный; уникальный индекс `iUSERSMAPPING2` по `fINNERUSERID`. Неявная связь: `fINNERUSERID → USERSROLES.fUSERID`.

---

## Связи домена

Модель безопасности выстроена вокруг роли:

```
USERSROLES(fUSERID, fROLE)
   fROLE ─→ ROLEACCESS(fCODE)                      -- справочник ролей
              ├─→ SUBSYSTEMACCESS(fROLE, fSUBSYSTEMID)   -- доступ к подсистемам
              ├─→ DIRECTORYACCESS(fROLE, fDIRECTORYTYPE) -- права на справочники
              ├─→ DOCUMENTACCESS(fROLE, fDOCTYPE)        -- права на типы документов
              ├─→ FOLDERACCESS(fROLE, fCOMMANDID)        -- доступ к командам/папкам
              ├─→ ONLINEREPORTACCESS(fROLE, fTEMPLATECODE) -- доступ к онлайн-отчётам
              └─→ ROLEPARAMS(fROLE, fPARAMID)            -- параметры уровня роли
   fUSERID ─→ USERACCESS      -- точечные права пользователя (переопределения)
             USERPARAMS       -- параметры пользователя (переопределяют PARAMS)
             DIALOGVALUES     -- сохранённые фильтры/диалоги
             LAYOUTVALUES     -- сохранённые макеты гридов
             DATAVIEWSETTINGS -- пользовательские представления
             DIRSLOG          -- аудит изменений справочников
```

Параметры конфигурации образуют трёхуровневую иерархию по общему коду параметра: `PARAMS.fPARID` (глобально) ← `ROLEPARAMS.fPARAMID` (роль) ← `USERPARAMS.fPARAMID` (пользователь).

Локализация: определения (`TREEDEF`, `AWP`, `USERREPORTS`, `DATAVIEWDEF`, `CONTEXTFUNCTIONDEF`) ссылаются полем `fCAPTIONID`/`fNODECAPTIONID`/`fCODECAPTIONID` на `STRINGRESOURCES.fNAME`, откуда берётся текст по нужной культуре (`fCULTURE`).

Связь с соседними доменами: `TREEDEF.fNAME → TREES.fTREEID` — метаопределения деревьев-справочников (территории `SArea`, клиентские группы `CustGrp`, дивизионы `Division` и др.), на узлы которых опираются домены агентов, клиентов и продаж. `DOCUMENTACCESS.fDOCTYPE` использует тот же код типа документа, что и документный домен (`DOCUMENTS`/`SALES`). `SALESAGENTS.fUSERID → USERSROLES.fUSERID` и `SALESAGENTS.fDEVICEID → DEVICES.fDEVICEID` связывают агентов с пользователями и устройствами.

## Примеры отчётных запросов

Все запросы — только чтение (SELECT), по реально существующим колонкам.

1. Пользователи с их ролями и описанием роли:

```sql
SELECT ur.fUSERID,
       ur.fROLE,
       ra.fCOMMENT AS RoleName
FROM USERSROLES ur
LEFT JOIN ROLEACCESS ra ON ra.fCODE = ur.fROLE
ORDER BY ur.fROLE, ur.fUSERID;
```

2. Матрица прав роли на типы документов (что роль может видеть/менять/удалять):

```sql
SELECT da.fROLE,
       ra.fCOMMENT AS RoleName,
       da.fDOCTYPE,
       da.fALLOWADD,
       da.fALLOWEDIT,
       da.fALLOWVIEW,
       da.fALLOWDELETE
FROM DOCUMENTACCESS da
LEFT JOIN ROLEACCESS ra ON ra.fCODE = da.fROLE
WHERE da.fALLOWVIEW = 1
ORDER BY da.fROLE, da.fDOCTYPE;
```

3. Аудит активности пользователей по журналу справочников (число операций за 2025 год):

```sql
SELECT dl.fUSERID,
       COUNT(*)        AS OperationsCount,
       MIN(dl.fDATE)   AS FirstOperation,
       MAX(dl.fDATE)   AS LastOperation
FROM DIRSLOG dl
WHERE dl.fDATE >= '2025-01-01' AND dl.fDATE < '2026-01-01'
GROUP BY dl.fUSERID
ORDER BY OperationsCount DESC;
```

4. Определения деревьев-справочников с локализованной (армянской) подписью:

```sql
SELECT td.fNAME,
       td.fCODELEN,
       td.fMULTILEVEL,
       td.fSYSTEM,
       sr.fVALUE AS CaptionHy
FROM TREEDEF td
LEFT JOIN STRINGRESOURCES sr
       ON sr.fNAME = td.fCAPTIONID
      AND sr.fCULTURE = 'HY-AM'
ORDER BY td.fNAME;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)
