# -*- coding: utf-8 -*-
"""Чтение из ERP (AS-Sales Management, MS SQL) — СТРОГО ТОЛЬКО ЧТЕНИЕ.

Это боевая БД компании. Любой запрос идёт через `_select()`:
  - разрешены только SELECT/WITH, один оператор, без слов записи/DDL/EXEC
    (FORBIDDEN_WORDS, FORBIDDEN_PHRASES);
  - соединение ApplicationIntent=ReadOnly, autocommit;
  - в каждой таблице WITH (NOLOCK), значения — только параметрами `?`;
  - даты — объектами date/datetime (строковые даты ломаются о локаль сервера).
`DatabaseConnection.execute_query` дашборда не используем: он глотает ошибки и возвращает [],
а пустые данные выглядели бы как нули в KPI. Здесь любая ошибка драйвера — ErpError.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Sequence

import pyodbc

from .demand import SaleDoc
from .dispatch import DispatchData, DispatchOrder, FactData, Place, SameDayData, ShippedDoc, hint_reason
from .evaluate import ActualVisit
from .geo import Fix
from .plan import TemplateRow

FORBIDDEN_WORDS = (
    # список плана (§0)
    'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE', 'ALTER', 'DROP', 'TRUNCATE',
    'EXEC', 'EXECUTE', 'GRANT', 'REVOKE', 'DENY', 'INTO', 'BACKUP', 'RESTORE',
    'DBCC', 'OPENROWSET', 'OPENQUERY', 'BULK',
    # усиление: T-SQL не требует «;» между операторами, поэтому запрещены и слова, которыми
    # начинается второй оператор или блокируется боевая ERP
    'SHUTDOWN', 'KILL', 'WAITFOR', 'RECONFIGURE', 'BEGIN', 'COMMIT', 'ROLLBACK', 'DECLARE',
    'SET', 'USE', 'OPENDATASOURCE', 'TABLOCK', 'TABLOCKX', 'XLOCK', 'UPDLOCK', 'HOLDLOCK',
    # триггеры, контрольная точка, смена пользователя, запись text/image, очереди Service Broker
    'DISABLE', 'ENABLE', 'CHECKPOINT', 'SETUSER', 'WRITETEXT', 'UPDATETEXT', 'RECEIVE', 'SEND',
)
# Фразы из нескольких слов: NEXT VALUE FOR внутри SELECT сдвигает SEQUENCE — это запись.
FORBIDDEN_PHRASES = ('NEXT VALUE FOR',)
FORBIDDEN_PREFIXES = ('SP_', 'XP_')

_START_RE = re.compile(r'\s*(?:SELECT|WITH)\b', re.IGNORECASE)
# Слева — не буква/_/@/#/$ (а не \b): T-SQL читает «1DELETE» как «1» и «DELETE».
_KEYWORD_LEFT = r'(?<![A-Za-z_@#$])'
# Слова и префиксы ищем в исходном тексте — в том числе внутри комментариев и строк.
_FORBIDDEN_RE = re.compile(
    _KEYWORD_LEFT + r'(?:' + '|'.join(FORBIDDEN_WORDS) + r')\b'
    r'|' + _KEYWORD_LEFT + r'(?:' + '|'.join(p.rstrip('_') for p in FORBIDDEN_PREFIXES) + r')_',
    re.IGNORECASE)
# Фразы: между словами только пробелы. «NEXT/**/VALUE FOR» ловится по тексту без комментариев
# (_strip_comments): регулярка с комментариями между словами разбиралась экспоненциально.
_PHRASE_RE = re.compile(
    _KEYWORD_LEFT + r'(?:' + '|'.join(r'\s+'.join(p.split()) for p in FORBIDDEN_PHRASES) + r')\b',
    re.IGNORECASE)
_QUOTE_CLOSE = {"'": "'", '"': '"', '[': ']'}

logger = logging.getLogger(__name__)

READ_ONLY_SUFFIX = 'ApplicationIntent=ReadOnly;'
_IN_CHUNK = 900  # у SQL Server предел 2100 параметров на запрос


class UnsafeSqlError(ValueError):
    """Запрос не прошёл read-only guard — выполнять нельзя."""


class ErpError(RuntimeError):
    """ERP недоступна или запрос завершился ошибкой драйвера."""


def _strip_comments(sql: str) -> str:
    """Текст запроса без комментариев T-SQL за один проход (линейно по длине).

    /* … */ — с вложенностью, как у SQL Server; -- — до конца строки; каждый комментарий
    заменяется пробелом (он разделяет слова, но не склеивает их). Строки '…', идентификаторы
    "…" и […] (удвоенная закрывающая кавычка — экранирование) копируются как есть: маркер
    комментария внутри них — не комментарий. Незакрытые комментарий или строка — до конца текста.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        pair = sql[i:i + 2]
        if pair == '--':
            end = sql.find('\n', i)
            i = n if end < 0 else end
            out.append(' ')
        elif pair == '/*':
            depth, i = 1, i + 2
            while i < n and depth:
                pair = sql[i:i + 2]
                if pair == '/*':
                    depth, i = depth + 1, i + 2
                elif pair == '*/':
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(' ')
        elif sql[i] in _QUOTE_CLOSE:
            close, j = _QUOTE_CLOSE[sql[i]], i + 1
            while True:
                k = sql.find(close, j)
                if k < 0:
                    j = n
                    break
                if sql[k + 1:k + 2] == close:   # '' внутри строки, ]] внутри [имени]
                    j = k + 2
                    continue
                j = k + 1
                break
            out.append(sql[i:j])
            i = j
        else:
            out.append(sql[i])
            i += 1
    return ''.join(out)


def check_sql(sql: str) -> None:
    """Read-only guard: только SELECT/WITH, один оператор, без запрещённых слов.

    Слова — по исходному тексту (и в комментариях тоже), фразы — по исходному тексту и по
    тексту без комментариев. Все проверки линейны: guard не зависает на длинном запросе.
    """
    if not isinstance(sql, str) or not _START_RE.match(sql):
        raise UnsafeSqlError('Разрешены только запросы SELECT/WITH')
    if ';' in sql:
        raise UnsafeSqlError('Несколько операторов в одном запросе запрещены')
    m = (_FORBIDDEN_RE.search(sql) or _PHRASE_RE.search(sql)
         or _PHRASE_RE.search(_strip_comments(sql)))
    if m:
        raise UnsafeSqlError(f'Запрещённое слово в запросе: {m.group(0)}')


def connect(connection_string: str, *, login_timeout: int = 15,
            query_timeout: int = 120) -> pyodbc.Connection:
    """Соединение только для чтения: ApplicationIntent=ReadOnly, autocommit."""
    cs = connection_string.strip()
    if not cs.endswith(';'):
        cs += ';'
    try:
        conn = pyodbc.connect(cs + READ_ONLY_SUFFIX, autocommit=True, timeout=login_timeout)
    except pyodbc.Error as e:
        raise ErpError('Не удалось подключиться к ERP') from e
    conn.timeout = query_timeout
    return conn


def close_quietly(conn: Any) -> None:
    """Закрыть соединение; ошибка закрытия не должна подменять исходную ошибку запроса."""
    try:
        conn.close()
    except pyodbc.Error:
        logger.warning('[Routes] Ошибка при закрытии соединения с ERP', exc_info=True)


def _select(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[Any]:
    """ЕДИНСТВЕННАЯ точка выполнения SQL в разделе. Сначала guard, потом запрос."""
    check_sql(sql)
    try:
        cur = conn.cursor()
        try:
            if params:
                cur.execute(sql, list(params))
            else:
                cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.close()
    except pyodbc.Error as e:
        raise ErpError('Ошибка чтения из ERP') from e


def _chunks(ids: Sequence[int], size: int = _IN_CHUNK) -> Iterator[Sequence[int]]:
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _placeholders(n: int) -> str:
    return ','.join('?' * n)


def _float(x: Any) -> float | None:
    return None if x is None else float(x)


def _str(x: Any) -> str:
    return (x or '').strip()


def _day(x: Any) -> date:
    return x.date() if isinstance(x, datetime) else x


# --- Запросы (все — SELECT с WITH (NOLOCK); {ph} — только плейсхолдеры `?`) ---

SQL_AGENTS = """
SELECT a.fID, a.fCODE, a.fNAME, a.fCLOSED, ar.area
FROM SALESAGENTS a WITH (NOLOCK)
OUTER APPLY (SELECT TOP 1 COALESCE(t.fCAPTION, RTRIM(sa.fSALESAREA)) AS area
             FROM SALESAGENTAREAS sa WITH (NOLOCK)
             LEFT JOIN TREES t WITH (NOLOCK) ON t.fTREEID = 'SArea' AND t.fCODE = sa.fSALESAREA
             WHERE sa.fSALESAGENTID = a.fID
             ORDER BY sa.fDEFAULT DESC, sa.fROWNUM) ar
"""

SQL_ROUTE_TEMPLATES = """
SELECT r.fSALESAGENTID, r.fID, r.fWEEK, r.fWEEKDAY, r.fPERIODICITY,
       l.fCUSTOMERID, l.fROWNUM, l.fDELIVERYADDRESSID
FROM ROUTETEMPLATES r WITH (NOLOCK)
JOIN ROUTETEMPLATESLIST l WITH (NOLOCK) ON l.fID = r.fID
"""

SQL_CUSTOMERS = """
SELECT c.fID, c.fCODE, c.fNAME, RTRIM(c.fGROUP), t.fCAPTION, c.fCLOSED, ar.area
FROM CUSTOMERS c WITH (NOLOCK)
LEFT JOIN TREES t WITH (NOLOCK) ON t.fTREEID = 'CustGrp' AND t.fCODE = c.fGROUP
OUTER APPLY (SELECT TOP 1 RTRIM(sa.fSALESAREA) AS area
             FROM CUSTOMERSALESAREAS sa WITH (NOLOCK)
             WHERE sa.fCUSTOMERID = c.fID
             ORDER BY sa.fDEFAULT DESC, sa.fROWNUM) ar
WHERE c.fID IN ({ph})
"""

SQL_CUSTOMER_GROUPS = """
SELECT RTRIM(t.fCODE), t.fCAPTION
FROM TREES t WITH (NOLOCK)
WHERE t.fTREEID = 'CustGrp'
"""

SQL_ADDRESSES = """
SELECT a.fID, a.fCUSTOMERID, a.fLATITUDE, a.fLONGITUDE, a.fDEFAULT, a.fCLOSED
FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
"""

SQL_SALES_DOCS = """
SELECT s.fCUSTOMERID, s.fSALESAGENTID, CAST(s.fDATE AS date), od.order_date,
       s.fTOTALSUM, ISNULL(k.kg, 0)
FROM SALES s WITH (NOLOCK)
OUTER APPLY (SELECT MIN(CAST(o.fDATE AS date)) AS order_date
             FROM DOCPARENTS p WITH (NOLOCK)
             JOIN ORDERS o WITH (NOLOCK) ON o.fISN = p.fPARENTISN
             WHERE p.fISN = s.fISN AND p.fPARENTDOCTYPE = 1) od
OUTER APPLY (SELECT SUM(sd.fQUANTITY * pr.fWEIGHT) AS kg
             FROM SALEDOCDETAILS sd WITH (NOLOCK)
             JOIN PRODUCTS pr WITH (NOLOCK) ON pr.fID = sd.fPRODUCTID
             WHERE sd.fISN = s.fISN) k
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?
"""

SQL_FIRST_SALE_DATES = """
SELECT s.fCUSTOMERID, MIN(CAST(s.fDATE AS date))
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fCUSTOMERID IN ({ph})
GROUP BY s.fCUSTOMERID
"""

SQL_MONTHLY_REVENUE = """
SELECT YEAR(s.fDATE), MONTH(s.fDATE), SUM(s.fTOTALSUM)
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?
GROUP BY YEAR(s.fDATE), MONTH(s.fDATE)
"""

SQL_VISITS = """
SELECT v.fCUSTOMERID, v.fSALESAGENTID, CAST(v.fDATE AS date), v.fSTARTTIME, v.fENDTIME,
       v.fLATITUDE, v.fLONGITUDE, v.fACCURACY, RTRIM(v.fVISITRESULT)
FROM ACTUALROUTES v WITH (NOLOCK)
WHERE v.fDATE >= ? AND v.fDATE < ?
"""

SQL_TRACKS = """
SELECT p.fSALESAGENTID, p.fDATETIME, p.fLATITUDE, p.fLONGITUDE, p.fACCURACY
FROM AGENTLOCATIONS p WITH (NOLOCK)
WHERE p.fDATETIME >= ? AND p.fDATETIME < ?
"""

SQL_CARS = """
SELECT LTRIM(RTRIM(c.fCODE)), c.fNAME, c.fISCLOSED, c.fMAXCAPACITYBYWEIGHT
FROM CARS c WITH (NOLOCK)
"""

# Когда машина последний раз везла проведённую реализацию (за всё время) — машины без развоза выключены «авто»
SQL_CAR_LAST_USED = """
SELECT LTRIM(RTRIM(s.fDELIVERYCAR)), MAX(CAST(s.fDATE AS date))
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) <> ''
GROUP BY LTRIM(RTRIM(s.fDELIVERYCAR))
"""

# Экспедиторы: везли проведённые реализации БЕЗ машины в накладной (fDELIVERYCAR пусто), и не свои —
# fVANAGENTID = fSALESAGENTID — менеджер развозит сам, это не машина парка. Документов и последний день.
SQL_EXPEDITORS = """
SELECT s.fVANAGENTID, COUNT(*), MAX(CAST(s.fDATE AS date))
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) = ''
  AND ISNULL(s.fVANAGENTID, 0) <> 0 AND s.fVANAGENTID <> ISNULL(s.fSALESAGENTID, 0)
GROUP BY s.fVANAGENTID
"""

# Долг клиента на сегодня — формула дашборда (DEBT_CALCULATION_FORMULA.md, /api/customers в app_v2.py):
# ДОЛГ = ДЕБЕТ (HICUSTOMERSDEBT: D − C, клиент — через DOCUMENTS) − |Type01| − |Type02| (HIRESTCUSTOMERSSUM)
SQL_CUSTOMER_DEBIT = """
SELECT doc.fCUSTOMERID, SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
FROM HICUSTOMERSDEBT d WITH (NOLOCK)
JOIN DOCUMENTS doc WITH (NOLOCK) ON doc.fISN = d.fDEBTDOCISN
WHERE doc.fCUSTOMERID IN ({ph})
GROUP BY doc.fCUSTOMERID
"""

SQL_CUSTOMER_REST = """
SELECT r.fCUSTOMERID,
       SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END),
       SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END)
FROM HIRESTCUSTOMERSSUM r WITH (NOLOCK)
WHERE r.fCUSTOMERID IN ({ph})
GROUP BY r.fCUSTOMERID
"""

# Сколько везла машина в день: документов и кг (вес — как в SQL_SALES_DOCS: количество × вес товара)
SQL_CAR_DAYS = """
SELECT LTRIM(RTRIM(s.fDELIVERYCAR)), CAST(s.fDATE AS date), COUNT(*), SUM(ISNULL(k.kg, 0))
FROM SALES s WITH (NOLOCK)
OUTER APPLY (SELECT SUM(sd.fQUANTITY * pr.fWEIGHT) AS kg
             FROM SALEDOCDETAILS sd WITH (NOLOCK)
             JOIN PRODUCTS pr WITH (NOLOCK) ON pr.fID = sd.fPRODUCTID
             WHERE sd.fISN = s.fISN) k
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND LTRIM(RTRIM(s.fDELIVERYCAR)) <> ''
GROUP BY LTRIM(RTRIM(s.fDELIVERYCAR)), CAST(s.fDATE AS date)
"""

# План развоза (dispatch-plan §1, проверено по данным 2026-10-01):
# - заказы — ORDERS с fSTATE = 2 (проведён; удалённые заказы остаются только в DOCUMENTS с fDOCSTATE = 99,
#   в ORDERS их нет); fDELIVERYDATE и fDELIVERYADDRESSID в заказах не заполняются — день доставки
#   считаем по дате заказа, адрес — адрес клиента по умолчанию;
# - строки заказа — SALEDOCDETAILS по fISN заказа (PROVIDINGDELIVERIES ссылается на реализации);
# - «уже отгружен» — первая проведённая реализация SALES по заказу (DOCPARENTS, fPARENTDOCTYPE = 1);
#   у заказа бывают и другие дочерние документы — берём только SALES;
# - fVANAGENTID = fSALESAGENTID — менеджер развозит сам (A000, A008/6 «19 литров»): не для машин парка;
# - день ввода заказа — DOCUMENTS.fCREATIONDATE того же fISN (SQL_ORDER_CREATED): заведён раньше своей даты — заказ на
#   эту дату (ответ владельца №78, dispatch.DispatchOrder.predated); документа нет — NULL, заказ как обычный.
SQL_DISPATCH_ORDERS = """
SELECT CAST(o.fISN AS nvarchar(36)), RTRIM(o.fDOCNUM), CAST(o.fDATE AS date), o.fCUSTOMERID,
       o.fSALESAGENTID, LTRIM(RTRIM(ISNULL(o.fDELIVERYCAR, ''))), o.fTOTALSUM, ISNULL(k.kg, 0), sh.shipped,
       o.fVANAGENTID, cr.entered
FROM ORDERS o WITH (NOLOCK)
OUTER APPLY (SELECT MIN(CAST(d.fCREATIONDATE AS date)) AS entered
             FROM DOCUMENTS d WITH (NOLOCK)
             WHERE d.fISN = o.fISN) cr
OUTER APPLY (SELECT SUM(sd.fQUANTITY * pr.fWEIGHT) AS kg
             FROM SALEDOCDETAILS sd WITH (NOLOCK)
             JOIN PRODUCTS pr WITH (NOLOCK) ON pr.fID = sd.fPRODUCTID
             WHERE sd.fISN = o.fISN) k
OUTER APPLY (SELECT MIN(CAST(s.fDATE AS date)) AS shipped
             FROM DOCPARENTS p WITH (NOLOCK)
             JOIN SALES s WITH (NOLOCK) ON s.fISN = p.fISN
             WHERE p.fPARENTISN = o.fISN AND p.fPARENTDOCTYPE = 1 AND s.fSTATE = 2) sh
WHERE o.fSTATE = 2 AND o.fDATE >= ? AND o.fDATE < ?
"""

# Когда завели заказы дня (новые заказы дня, ответ владельца №72; проверено 05.10.2026): DOCUMENTS.fCREATIONDATE того же
# fISN — дата и время ввода заказа (в ORDERS времени нет)
SQL_ORDER_CREATED = """
SELECT CAST(o.fISN AS nvarchar(36)), d.fCREATIONDATE
FROM ORDERS o WITH (NOLOCK)
JOIN DOCUMENTS d WITH (NOLOCK) ON d.fISN = o.fISN
WHERE o.fSTATE = 2 AND o.fDATE >= ? AND o.fDATE < ?
"""

# Адрес доставки клиента текстом (по умолчанию) — для карточки рейса и листа водителю
SQL_ADDRESS_TEXT = """
SELECT a.fCUSTOMERID, a.fADDRESS
FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
WHERE a.fDEFAULT = 1 AND a.fCUSTOMERID IN ({ph})
"""

# Клиенты списка «машины не везут» (№74, настройки «Маршрутов»): поиск — код с начала или часть названия (! — escape
# для % _ [ в тексте поиска), точный код — первым; по id — для уже отмеченных. Код, название, адрес по умолчанию
SQL_CUSTOMER_FIND = """
SELECT TOP 30 c.fID, RTRIM(c.fCODE), c.fNAME, ad.fADDRESS
FROM CUSTOMERS c WITH (NOLOCK)
OUTER APPLY (SELECT TOP 1 a.fADDRESS FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
             WHERE a.fCUSTOMERID = c.fID AND a.fDEFAULT = 1 ORDER BY a.fROWNUM) ad
WHERE c.fCODE LIKE ? ESCAPE '!' OR c.fNAME LIKE ? ESCAPE '!'
ORDER BY CASE WHEN RTRIM(c.fCODE) = ? THEN 0 ELSE 1 END, c.fNAME, c.fID
"""
SQL_CUSTOMER_REFS = """
SELECT c.fID, RTRIM(c.fCODE), c.fNAME, ad.fADDRESS
FROM CUSTOMERS c WITH (NOLOCK)
OUTER APPLY (SELECT TOP 1 a.fADDRESS FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
             WHERE a.fCUSTOMERID = c.fID AND a.fDEFAULT = 1 ORDER BY a.fROWNUM) ad
WHERE c.fID IN ({ph})
"""
# Подсказка к этому списку: заказы периода по клиенту и менеджеру и адреса по умолчанию клиентов (текст и точка)
SQL_CUSTOMER_ORDERS = """
SELECT o.fCUSTOMERID, o.fSALESAGENTID, COUNT(*), SUM(o.fTOTALSUM), MAX(CAST(o.fDATE AS date))
FROM ORDERS o WITH (NOLOCK)
WHERE o.fSTATE = 2 AND o.fDATE >= ? AND o.fDATE < ?
GROUP BY o.fCUSTOMERID, o.fSALESAGENTID
"""
SQL_DEFAULT_PLACES = """
SELECT a.fCUSTOMERID, a.fADDRESS, a.fLATITUDE, a.fLONGITUDE
FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
WHERE a.fDEFAULT = 1 AND a.fCUSTOMERID IN ({ph})
"""

# Какие машины везли заказы менеджера (сравнение «по менеджерам»): документов за период. Накладная без
# машины, которую вёз экспедитор (не сам менеджер), — строка с пустой машиной и id экспедитора: за ним может
# быть закреплена ручная машина (store.Truck.van_agent_id).
SQL_AGENT_CARS = """
SELECT s.fSALESAGENTID, x.car, CASE WHEN x.car = '' THEN s.fVANAGENTID ELSE 0 END, COUNT(*)
FROM SALES s WITH (NOLOCK)
CROSS APPLY (SELECT LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) AS car) x
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?
  AND (x.car <> '' OR (ISNULL(s.fVANAGENTID, 0) <> 0 AND s.fVANAGENTID <> ISNULL(s.fSALESAGENTID, 0)))
GROUP BY s.fSALESAGENTID, x.car, CASE WHEN x.car = '' THEN s.fVANAGENTID ELSE 0 END
"""

# Факт развоза за дату: проведённые реализации с машиной, кг и суммой («план и факт»)
SQL_SHIPPED = """
SELECT s.fCUSTOMERID, s.fSALESAGENTID, LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))), s.fTOTALSUM, ISNULL(k.kg, 0),
       s.fVANAGENTID
FROM SALES s WITH (NOLOCK)
OUTER APPLY (SELECT SUM(sd.fQUANTITY * pr.fWEIGHT) AS kg
             FROM SALEDOCDETAILS sd WITH (NOLOCK)
             JOIN PRODUCTS pr WITH (NOLOCK) ON pr.fID = sd.fPRODUCTID
             WHERE sd.fISN = s.fISN) k
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?
"""


# --- Справочники ---

@dataclass(frozen=True)
class Agent:
    id: int
    code: str
    name: str
    closed: bool
    area: str = ''          # «գիծ»: название основной зоны продаж (SALESAGENTAREAS → TREES 'SArea')


@dataclass(frozen=True)
class Customer:
    id: int
    code: str
    name: str
    group: str
    group_name: str | None
    closed: bool
    area: str | None


@dataclass(frozen=True)
class Address:
    id: int
    customer_id: int
    lat: float | None
    lon: float | None
    is_default: bool
    closed: bool


@dataclass(frozen=True)
class Car:
    code: str
    name: str
    closed: bool
    capacity_kg: float | None = None   # грузоподъёмность из карточки ERP (fMAXCAPACITYBYWEIGHT, тонны); 0 — не задана


def agents(conn: Any) -> dict[int, Agent]:
    return {int(r[0]): Agent(int(r[0]), _str(r[1]), _str(r[2]), bool(r[3]), _str(r[4]))
            for r in _select(conn, SQL_AGENTS)}


def route_templates(conn: Any) -> list[TemplateRow]:
    return [TemplateRow(agent_id=int(r[0]), template_id=int(r[1]), week=int(r[2]),
                        weekday=int(r[3]), periodicity=int(r[4]), customer_id=int(r[5]),
                        rownum=int(r[6]), address_id=int(r[7] or 0))
            for r in _select(conn, SQL_ROUTE_TEMPLATES)]


def customers(conn: Any, ids: Sequence[int]) -> dict[int, Customer]:
    out: dict[int, Customer] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_CUSTOMERS.format(ph=_placeholders(len(chunk))), chunk):
            out[int(r[0])] = Customer(int(r[0]), _str(r[1]), _str(r[2]), _str(r[3]),
                                      _str(r[4]) or None, bool(r[5]), _str(r[6]) or None)
    return out


def customer_groups(conn: Any) -> dict[str, str]:
    return {_str(r[0]): _str(r[1]) for r in _select(conn, SQL_CUSTOMER_GROUPS)}


def addresses(conn: Any) -> list[Address]:
    """Все адреса доставки: «дефолтные» точки ищутся по всему справочнику, не только по плану."""
    return [Address(int(r[0]), int(r[1]), _float(r[2]), _float(r[3]), bool(r[4]), bool(r[5]))
            for r in _select(conn, SQL_ADDRESSES)]


# --- Продажи ---

def sales_docs(conn: Any, since: date, until: date) -> list[SaleDoc]:
    """Проведённые продажи (fSTATE=2) с датой продажи в [since, until), с датой заказа и кг."""
    return [SaleDoc(customer_id=int(r[0]), agent_id=int(r[1]), sale_date=_day(r[2]),
                    order_date=_day(r[3]) if r[3] is not None else None,
                    revenue=float(r[4] or 0), kg=float(r[5] or 0))
            for r in _select(conn, SQL_SALES_DOCS, (since, until))]


def first_sale_dates(conn: Any, ids: Sequence[int]) -> dict[int, date]:
    out: dict[int, date] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_FIRST_SALE_DATES.format(ph=_placeholders(len(chunk))), chunk):
            out[int(r[0])] = _day(r[1])
    return out


def monthly_revenue(conn: Any, since: date, until: date) -> dict[tuple[int, int], float]:
    return {(int(r[0]), int(r[1])): float(r[2] or 0)
            for r in _select(conn, SQL_MONTHLY_REVENUE, (since, until))}


# --- Визиты и треки ---

def visits(conn: Any, since: date, until: date) -> list[ActualVisit]:
    return [ActualVisit(customer_id=int(r[0]), agent_id=int(r[1]), day=_day(r[2]), start=r[3],
                        end=r[4], lat=_float(r[5]), lon=_float(r[6]), accuracy=_float(r[7]),
                        result=_str(r[8]))
            for r in _select(conn, SQL_VISITS, (since, until))]


def tracks(conn: Any, since: datetime, until: datetime) -> dict[int, list[Fix]]:
    out: dict[int, list[Fix]] = {}
    for r in _select(conn, SQL_TRACKS, (since, until)):
        out.setdefault(int(r[0]), []).append(Fix(r[1], float(r[2]), float(r[3]), _float(r[4])))
    for fixes in out.values():
        fixes.sort(key=lambda f: f.at)
    return out


# --- Машины ---

def _capacity_kg(tonnes: Any) -> float | None:
    """Грузоподъёмность ERP (тонны, money — 4 знака) → целые кг, как сохраняет страница; NULL, 0 и меньше — не задана."""
    return float(round(tonnes * 1000)) if tonnes is not None and tonnes > 0 else None


def cars(conn: Any) -> dict[str, Car]:
    return {_str(r[0]): Car(_str(r[0]), _str(r[1]), bool(r[2]), _capacity_kg(r[3])) for r in _select(conn, SQL_CARS)}


def car_last_used(conn: Any) -> dict[str, date]:
    """Машина → день последней проведённой реализации с ней (за всё время)."""
    return {_str(r[0]): _day(r[1]) for r in _select(conn, SQL_CAR_LAST_USED)}


def expeditors(conn: Any, since: date, until: date) -> dict[int, tuple[int, date]]:
    """Экспедитор → (накладных без машины, последний день) за [since, until)."""
    return {int(r[0]): (int(r[1]), _day(r[2])) for r in _select(conn, SQL_EXPEDITORS, (since, until))}


def customer_debts(conn: Any, ids: Sequence[int]) -> dict[int, float]:
    """Долг клиентов на сегодня: дебет (D − C) − |возвраты Type01| − |переплаты Type02| — как на
    странице клиентов дашборда. Клиента без записей нет в словаре (долг 0)."""
    debit: dict[int, float] = {}
    rest: dict[int, float] = {}
    for chunk in _chunks(sorted(set(ids))):
        ph = _placeholders(len(chunk))
        for r in _select(conn, SQL_CUSTOMER_DEBIT.format(ph=ph), chunk):
            debit[int(r[0])] = float(r[1] or 0)
        for r in _select(conn, SQL_CUSTOMER_REST.format(ph=ph), chunk):
            rest[int(r[0])] = abs(float(r[1] or 0)) + abs(float(r[2] or 0))
    return {c: debit.get(c, 0.0) - rest.get(c, 0.0) for c in sorted(set(debit) | set(rest))}


@dataclass(frozen=True)
class CarDay:
    """Машина в день по продажам ERP (SALES.fDELIVERYCAR): документов и кг."""
    car_code: str
    day: date
    docs: int
    kg: float


def car_days(conn: Any, since: date, until: date) -> list[CarDay]:
    """Сколько везла каждая машина в каждый день [since, until): документов и кг."""
    return [CarDay(_str(r[0]), _day(r[1]), int(r[2]), float(r[3] or 0))
            for r in _select(conn, SQL_CAR_DAYS, (since, until))]


# --- План развоза ---

def dispatch_orders(conn: Any, since: date, until: date) -> list[DispatchOrder]:
    """Проведённые заказы с датой в [since, until): кг, сумма, машина в заказе, дата отгрузки и день ввода."""
    return [DispatchOrder(isn=_str(r[0]).upper(), doc_num=_str(r[1]), order_date=_day(r[2]),
                          customer_id=int(r[3]), agent_id=int(r[4] or 0), car_code=_str(r[5]),
                          revenue=float(r[6] or 0), kg=float(r[7] or 0),
                          shipped=_day(r[8]) if r[8] is not None else None, van_agent_id=int(r[9] or 0),
                          entered=_day(r[10]) if r[10] is not None else None)
            for r in _select(conn, SQL_DISPATCH_ORDERS, (since, until))]


def address_texts(conn: Any, ids: Sequence[int]) -> dict[int, str]:
    """Адрес по умолчанию текстом: клиент → «Марз, город, улица, дом»."""
    out: dict[int, str] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_ADDRESS_TEXT.format(ph=_placeholders(len(chunk))), chunk):
            text = _str(r[1])
            if text:
                out.setdefault(int(r[0]), text)
    return out


def place_texts(conn: Any, ids: Sequence[int]) -> dict[int, Place]:
    """Клиент → (адрес по умолчанию, название) — города-исключения правила «Развоза» (№74, dispatch.FleetRule)."""
    names = {c.id: c.name for c in customers(conn, ids).values()}
    addresses = address_texts(conn, ids)
    return {cid: (addresses.get(cid, ''), names.get(cid, '')) for cid in sorted(set(ids))}


def agent_cars(conn: Any, since: date, until: date) -> dict[int, tuple[str | int, ...]]:
    """Менеджер → кто везли его заказы за период, от самого частого (ничьи — машины по коду, потом
    экспедиторы по id): код машины ERP (str) или id экспедитора, возившего без машины (int) —
    dispatch.history_cars заменяет его закреплённой ручной машиной."""
    counts: dict[int, list[tuple[int, int, str, int]]] = {}
    for r in _select(conn, SQL_AGENT_CARS, (since, until)):
        car, van = _str(r[1]), int(r[2] or 0)
        counts.setdefault(int(r[0]), []).append((-int(r[3]), 0 if car else 1, car, van))
    return {a: tuple(car or van for _, _, car, van in sorted(cs)) for a, cs in counts.items()}


def shipped_docs(conn: Any, day: date) -> list[ShippedDoc]:
    """Проведённые реализации за день: клиент, менеджер, машина, сумма, кг, экспедитор."""
    return [ShippedDoc(customer_id=int(r[0]), agent_id=int(r[1] or 0), car_code=_str(r[2]),
                       revenue=float(r[3] or 0), kg=float(r[4] or 0), van_agent_id=int(r[5] or 0))
            for r in _select(conn, SQL_SHIPPED, (day, day + timedelta(days=1)))]


AGENT_CARS_DAYS = 90   # «машина менеджера по истории» — за 90 дней до даты развоза


def load_dispatch_data(connection_string: str, since: date, until: date, day: date) -> DispatchData:
    """Заказы к развозу на day (дата заказа в [since, until)) и справочники к ним — одним соединением."""
    conn = connect(connection_string)
    try:
        orders = dispatch_orders(conn, since, until)
        ids = sorted({o.customer_id for o in orders})
        names = {c.id: (c.code, c.name) for c in customers(conn, ids).values()}
        return DispatchData(orders=tuple(orders), customers=names, addresses=address_texts(conn, ids),
                            agent_cars=agent_cars(conn, day - timedelta(days=AGENT_CARS_DAYS), day),
                            loaded_at=datetime.now().replace(microsecond=0))
    finally:
        close_quietly(conn)


def load_same_day_data(connection_string: str, day: date) -> SameDayData:
    """Заказы с датой day (новые заказы дня, №72), когда их завели и справочники к ним — одним соединением."""
    conn = connect(connection_string)
    try:
        orders = dispatch_orders(conn, day, day + timedelta(days=1))
        created = {_str(r[0]).upper(): r[1] for r in _select(conn, SQL_ORDER_CREATED, (day, day + timedelta(days=1)))
                   if isinstance(r[1], datetime)}
        ids = sorted({o.customer_id for o in orders})
        names = {c.id: (c.code, c.name) for c in customers(conn, ids).values()}
        return SameDayData(orders=tuple(orders), created=created, customers=names, addresses=address_texts(conn, ids),
                           loaded_at=datetime.now().replace(microsecond=0))
    finally:
        close_quietly(conn)


# --- Клиенты «машины не везут» (№74) ---

CUSTOMER_FIND_MAX_LEN = 100   # символов в тексте поиска


@dataclass(frozen=True)
class CustomerRef:
    customer_id: int
    code: str
    name: str
    address: str


@dataclass(frozen=True)
class CustomerHint:
    """Клиент подсказки: почему (dispatch.hint_reason), заказов и сумма за период, последний день, менеджеры."""
    customer_id: int
    code: str
    name: str
    address: str
    reason: str
    orders: int
    revenue: float
    last_day: date
    agents: tuple[int, ...]


def _ref(r: Sequence[Any]) -> CustomerRef:
    return CustomerRef(int(r[0]), _str(r[1]), _str(r[2]), _str(r[3]))


def find_customers(conn: Any, query: str) -> list[CustomerRef]:
    """До 30 клиентов: код начинается с query или название его содержит (точный код — первым)."""
    q = query.strip()[:CUSTOMER_FIND_MAX_LEN]
    if not q:
        return []
    like = q.replace('!', '!!').replace('%', '!%').replace('_', '!_').replace('[', '![')
    return [_ref(r) for r in _select(conn, SQL_CUSTOMER_FIND, (like + '%', '%' + like + '%', q))]


def customer_refs(conn: Any, ids: Sequence[int]) -> list[CustomerRef]:
    out: list[CustomerRef] = []
    for chunk in _chunks(sorted(set(ids))):
        out += [_ref(r) for r in _select(conn, SQL_CUSTOMER_REFS.format(ph=_placeholders(len(chunk))), chunk)]
    return sorted(out, key=lambda c: (c.name, c.customer_id))


def customer_hints(conn: Any, since: date, until: date) -> list[CustomerHint]:
    """Клиенты с заказами в [since, until) без адреса или вне Армении (dispatch.hint_reason) — по сумме, большие первыми."""
    by: dict[int, list[tuple[int, int, float, date]]] = {}
    for r in _select(conn, SQL_CUSTOMER_ORDERS, (since, until)):
        by.setdefault(int(r[0]), []).append((int(r[1] or 0), int(r[2]), float(r[3] or 0), _day(r[4])))
    texts: dict[int, list[str]] = {}
    points: dict[int, list[tuple[float, float]]] = {}
    for chunk in _chunks(sorted(by)):
        for r in _select(conn, SQL_DEFAULT_PLACES.format(ph=_placeholders(len(chunk))), chunk):
            texts.setdefault(int(r[0]), []).append(_str(r[1]))
            if r[2] is not None and r[3] is not None:
                points.setdefault(int(r[0]), []).append((float(r[2]), float(r[3])))
    reasons = {c: why for c in by if (why := hint_reason(texts.get(c, ()), points.get(c, ()))) is not None}
    names = customers(conn, sorted(reasons)) if reasons else {}
    out = []
    for cid, why in reasons.items():
        rows = by[cid]
        c = names.get(cid)
        out.append(CustomerHint(cid, c.code if c else '', c.name if c else '',
                                next((t for t in texts.get(cid, ()) if t), ''), why, sum(n for _, n, _, _ in rows),
                                sum(s for _, _, s, _ in rows), max(d for _, _, _, d in rows),
                                tuple(sorted({a for a, _, _, _ in rows if a}))))
    return sorted(out, key=lambda h: (-h.revenue, h.customer_id))


def load_customer_refs(connection_string: str, query: str, ids: Sequence[int]) -> list[CustomerRef]:
    """Поиск клиентов (query) или клиенты по id — одним соединением, только чтение."""
    conn = connect(connection_string)
    try:
        return find_customers(conn, query) if query.strip() else customer_refs(conn, ids)
    finally:
        close_quietly(conn)


def load_customer_hints(connection_string: str, since: date, until: date) -> list[CustomerHint]:
    conn = connect(connection_string)
    try:
        return customer_hints(conn, since, until)
    finally:
        close_quietly(conn)


def load_customer_groups(connection_string: str, ids: Sequence[int]) -> dict[int, str]:
    """Клиент → код группы (CustGrp) — одним соединением, только чтение (обучение «Развоза»: сети, №66)."""
    conn = connect(connection_string)
    try:
        return {c.id: c.group for c in customers(conn, ids).values()}
    finally:
        close_quietly(conn)


def load_fact_data(connection_string: str, day: date) -> FactData:
    """Факт развоза за прошедшую дату: реализации с машинами и справочники клиентов."""
    conn = connect(connection_string)
    try:
        docs = shipped_docs(conn, day)
        ids = sorted({d.customer_id for d in docs})
        names = {c.id: (c.code, c.name) for c in customers(conn, ids).values()}
        return FactData(docs=tuple(docs), customers=names)
    finally:
        close_quietly(conn)
