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
from datetime import date, datetime
from typing import Any, Iterator, Sequence

import pyodbc

from .demand import SaleDoc
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
SELECT a.fID, a.fCODE, a.fNAME, a.fCLOSED
FROM SALESAGENTS a WITH (NOLOCK)
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
SELECT LTRIM(RTRIM(c.fCODE)), c.fNAME, c.fISCLOSED
FROM CARS c WITH (NOLOCK)
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

SQL_CAR_USAGE = """
SELECT LTRIM(RTRIM(s.fDELIVERYCAR)), s.fSALESAGENTID, COUNT(*)
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND LTRIM(RTRIM(s.fDELIVERYCAR)) <> ''
GROUP BY LTRIM(RTRIM(s.fDELIVERYCAR)), s.fSALESAGENTID
"""


# --- Справочники ---

@dataclass(frozen=True)
class Agent:
    id: int
    code: str
    name: str
    closed: bool


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


def agents(conn: Any) -> dict[int, Agent]:
    return {int(r[0]): Agent(int(r[0]), _str(r[1]), _str(r[2]), bool(r[3]))
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

def cars(conn: Any) -> dict[str, Car]:
    return {_str(r[0]): Car(_str(r[0]), _str(r[1]), bool(r[2])) for r in _select(conn, SQL_CARS)}


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


def car_usage(conn: Any, since: date, until: date) -> dict[str, dict[int, int]]:
    """Сколько документов продажи вёз каждый автомобиль у каждого агента: {машина: {агент: n}}."""
    out: dict[str, dict[int, int]] = {}
    for r in _select(conn, SQL_CAR_USAGE, (since, until)):
        out.setdefault(_str(r[0]), {})[int(r[1])] = int(r[2])
    return out
