# -*- coding: utf-8 -*-
"""Запись в ERP (ответ владельца №97, docs/plans/erp-write-plan.md) — ЕДИНСТВЕННОЕ место, где сайт пишет в боевую ERP.

Сайт ставит в заказы дня առաքիչ и машину по отправленному водителям плану «Развоза» (№81). Пишется ТОЛЬКО:
ORDERS.fVANAGENTID, ORDERS.fDELIVERYCAR; DOCUMENTS.fBODY (только строки VANAGENTID / DELIVERYCAR), fLASTMODIFYDATE,
fLASTMODIFIERID того же fISN (fDOCTYPE = 1); строка истории DOCUMENTSLOG. Ничего больше — накладные, остатки, проводки и
справочники не трогаются. Весь SQL записи — статические константы SQL_W_* с параметрами `?`, без динамического SQL.

Инварианты (доказуемо безопасно):
- решение по заказу — чистая функция plan_assignments: пишется только заказ клиента рейса ровно одной машины T отправленного
  плана, в состоянии fSTATE 1 или 2, без дочерних документов (DOCPARENTS: ни накладной, ни տրամադրում), առաքիչ — ровно один
  не закрытый ERP-агент с именем водителя T на день, значения отличаются от текущих; иначе — пропуск с причиной;
- каждый заказ — своя транзакция (apply): UPDATE ORDERS оптимистично по прежним значениям и при отсутствии дочерних
  документов (не 1 строка — откат), повторная проверка дочерних документов, fBODY — замена только двух строк (rewrite_body:
  остальной текст байт-в-байт тот же) по прежней версии строки (fTS), DOCUMENTSLOG; COMMIT; потом чтение назад и сверка;
- любая ошибка — откат этого заказа, остальные идут дальше; результат каждого заказа — в отчёте (и в журнале сайта).
Соединение записи — env ROUTES_ERP_WRITE_DSN (connect_write); его нет — запись выключена. Строка подключения не пишется в
журнал. Предпросмотр читает ERP обычным путём только для чтения (erp.load_order_states).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping, Sequence

import pyodbc

from .erp import Agent, Car, ErpError, OrderState

logger = logging.getLogger(__name__)

DSN_ENV = 'ROUTES_ERP_WRITE_DSN'
LOGIN_TIMEOUT_S = 15
QUERY_TIMEOUT_S = 30
COMPNAME = 'ARAQICH-SITE'
COMMENT_PREFIX = 'Կայք՝ '
COMMENT_MAX = 100           # fCOMMENT DOCUMENTSLOG: длина столбца не известна — не длиннее 100 символов
STATES = (1, 2)             # заказ в этих состояниях ERP можно править (fSTATE)
LABEL_VAN = 'Առաքիչ'        # подписи полей в истории ERP (fCHANGEDESCRIPTION), как пишет сама ERP
LABEL_CAR = 'Առաքող ավտոմեքենա'
KEY_VAN = 'VANAGENTID'
KEY_CAR = 'DELIVERYCAR'
EOL = '\r\n'

# причины пропуска (plan_assignments) и неудачи записи (apply); тексты — для страницы
SKIP_REASONS = ('not_in_plan', 'split_store', 'order_state', 'has_child_docs', 'courier_not_found', 'courier_ambiguous',
                'unchanged')
CAR_NOT_IN_ERP = 'car_not_in_erp'   # не пропуск: пишется только առաքիչ
FAIL_REASONS = ('changed_in_erp', 'has_child_docs', 'body_format', 'body_mismatch', 'error')
VERIFY_FAILED = 'verify_failed'     # записано (COMMIT), но чтение назад не совпало — смотреть заказ в ERP
REASON_HY = {
    'not_in_plan': 'Ուղարկված պլանի երթերում չէ',
    'split_store': 'Խանութը մի քանի մեքենայի երթերում է',
    'order_state': 'Պատվերի վիճակը թույլ չի տալիս փոխել',
    'has_child_docs': 'Պատվերից արդեն փաստաթուղթ կա (ապրանքագիր)',
    'courier_not_found': 'Մեքենայի վարորդը ERP-ում չի գտնվել',
    'courier_ambiguous': 'ERP-ում այդ անունով մի քանի աշխատակից կա',
    'unchanged': 'ERP-ում արդեն նույնն է',
    'car_not_in_erp': 'Մեքենան ERP-ում չկա — կգրվի միայն առաքիչը',
    'changed_in_erp': 'Պատվերը փոխվել է ERP-ում — թարմացրեք',
    'body_format': 'Պատվերի տվյալներն ERP-ում անսովոր են — չգրվեց',
    'body_mismatch': 'Պատվերի տվյալներն ERP-ում իրար չեն համապատասխանում — չգրվեց',
    'error': 'Սխալ — չգրվեց',
    'verify_failed': 'Գրվեց, բայց ստուգումը չհամընկավ — նայեք ERP-ում',
    'not_written': 'Այս պատվերը կայքից չի գրվել',
}

# --- SQL записи (статические; значения — только параметрами) ---
# Внутри транзакции чтение без NOLOCK: видим только зафиксированное (и своё).
SQL_W_ORDER_CAR = """SELECT fDELIVERYCAR FROM ORDERS WHERE fISN = ?"""
# Оптимистично: те же состояние, առաքիչ и машина, что в предпросмотре, и ни одного дочернего документа
SQL_W_UPDATE_ORDER = """UPDATE ORDERS SET fVANAGENTID = ?, fDELIVERYCAR = ?
WHERE fISN = ? AND fSTATE = ? AND ISNULL(fVANAGENTID, 0) = ? AND LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?
  AND NOT EXISTS (SELECT 1 FROM DOCPARENTS p WHERE p.fPARENTISN = ORDERS.fISN)"""
# машина не меняется (нет в ERP или та же) — столбец fDELIVERYCAR не трогаем вовсе
SQL_W_UPDATE_ORDER_VAN = """UPDATE ORDERS SET fVANAGENTID = ?
WHERE fISN = ? AND fSTATE = ? AND ISNULL(fVANAGENTID, 0) = ? AND LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?
  AND NOT EXISTS (SELECT 1 FROM DOCPARENTS p WHERE p.fPARENTISN = ORDERS.fISN)"""
SQL_W_CHILDREN = """SELECT COUNT(*) FROM DOCPARENTS WHERE fPARENTISN = ?"""
SQL_W_BODY = """SELECT fBODY, fTS FROM DOCUMENTS WHERE fISN = ? AND fDOCTYPE = 1"""
SQL_W_UPDATE_BODY = """UPDATE DOCUMENTS SET fBODY = ?, fLASTMODIFYDATE = GETDATE(), fLASTMODIFIERID = 0
WHERE fISN = ? AND fDOCTYPE = 1 AND fTS = ?"""
SQL_W_LOG = """INSERT INTO DOCUMENTSLOG (fISN, fDATE, fUSERID, fSTATE, fOP, fCOMMENT, fCOMPNAME, fCHANGEDESCRIPTION, fSOURCE)
VALUES (?, GETDATE(), 0, ?, 2, ?, ?, ?, 0)"""
# чтение назад после COMMIT
SQL_W_VERIFY = """SELECT o.fVANAGENTID, LTRIM(RTRIM(ISNULL(o.fDELIVERYCAR, ''))), d.fBODY
FROM ORDERS o JOIN DOCUMENTS d ON d.fISN = o.fISN AND d.fDOCTYPE = 1
WHERE o.fISN = ?"""


class BodyFormatError(ValueError):
    """fBODY заказа не того вида, что ожидаем (нет строки VANAGENTID, ключ дважды, не текст) — не пишем."""


class ErpWriteError(ErpError):
    """Соединение записи не открылось (API — как недоступная ERP, 503)."""


def name_key(name: str | None) -> str:
    """Имя человека для сравнения водителя дня с ERP-агентом: пробелы схлопнуты (в ERP бывают двойные), регистр не важен."""
    return ' '.join((name or '').split()).casefold()


# --- Решения (чисто) ---

@dataclass(frozen=True)
class Decision:
    """Решение по заказу: write — писать (old → new), иначе пропуск с причиной reason (SKIP_REASONS). У записи reason —
    '' или CAR_NOT_IN_ERP (машина не меняется, пишется только առաքիչ). truck — машина плана ('' — заказ не в рейсах; у
    магазина в рейсах нескольких машин — коды через «, »). Коды агентов — для истории ERP и страницы. empty_null — какой
    писать пустую машину в ORDERS.fDELIVERYCAR: NULL (True) или '' — только у «Վերադարձնել» к пустой машине (как было до
    нашей записи, store.erp_write_log.old_car_null); запись по плану пустую машину не пишет."""
    isn: str
    doc_num: str
    customer_id: int
    truck: str
    state: int
    old_van: int
    new_van: int
    old_car: str
    new_car: str
    write: bool
    reason: str
    old_code: str = ''
    new_code: str = ''
    new_name: str = ''
    empty_null: bool = True

    @property
    def car_changes(self) -> bool:
        return self.new_car != self.old_car

    def json(self) -> dict[str, Any]:
        return asdict(self)


def courier_of(name: str | None, agents: Mapping[int, Agent]) -> tuple[Agent | None, str]:
    """ERP-агент водителя по имени: (агент, '') или (None, 'courier_not_found' | 'courier_ambiguous'). Закрытые агенты не
    в счёт; тёзки — не угадываем."""
    key = name_key(name)
    if not key:
        return None, 'courier_not_found'
    found = [a for a in agents.values() if not a.closed and name_key(a.name) == key]
    if not found:
        return None, 'courier_not_found'
    if len(found) > 1:
        return None, 'courier_ambiguous'
    return found[0], ''


def plan_assignments(trips: Sequence[tuple[str, Sequence[int]]], orders: Sequence[OrderState],
                     drivers: Mapping[str, str | None], agents: Mapping[int, Agent],
                     cars: Mapping[str, Car]) -> list[Decision]:
    """Решения по заказам отправленного плана дня (порядок — как orders).

    trips — рейсы отправленного водителям плана (Draft.for_drivers): (машина, клиенты); orders — текущее состояние ERP
    заказов магазинов плана (erp.load_order_states); drivers — машина → водитель на день (None — не указан / не вышел);
    agents — ERP-агенты (id → агент); cars — машины ERP CARS (код → машина). Правила — по порядку: магазин в рейсах ни одной
    машины — not_in_plan, нескольких — split_store; fSTATE не 1/2 — order_state; есть дочерние документы — has_child_docs;
    առաքիչ — ровно один не закрытый агент с именем водителя машины (courier_of); машина — код T, если она есть в CARS и
    не закрыта, иначе машина заказа не меняется (CAR_NOT_IN_ERP); ничего не меняется — unchanged."""
    trucks_of: dict[int, set[str]] = {}
    for truck, cids in trips:
        for cid in cids:
            trucks_of.setdefault(cid, set()).add(truck)
    out: list[Decision] = []
    for o in orders:
        trucks = sorted(trucks_of.get(o.customer_id, ()))
        truck = ', '.join(trucks)
        old_code = agents[o.van_id].code if o.van_id in agents else ''
        base = Decision(o.isn, o.doc_num, o.customer_id, truck, o.state, o.van_id, o.van_id, o.car, o.car, False, '',
                        old_code, old_code)
        reason = ('not_in_plan' if not trucks else 'split_store' if len(trucks) > 1
                  else 'order_state' if o.state not in STATES else 'has_child_docs' if o.children else '')
        agent = None
        if not reason:
            agent, reason = courier_of(drivers.get(truck), agents)
        if reason:
            out.append(replace(base, reason=reason))
            continue
        assert agent is not None
        car = cars.get(truck)
        in_erp = car is not None and not car.closed
        new_car = truck if in_erp else o.car
        d = replace(base, new_van=agent.id, new_car=new_car, new_code=agent.code, new_name=' '.join(agent.name.split()),
                    reason='' if in_erp else CAR_NOT_IN_ERP)
        if d.new_van == d.old_van and not d.car_changes:
            out.append(replace(d, reason='unchanged'))
        else:
            out.append(replace(d, write=True))
    return out


def signature(decisions: Sequence[Decision]) -> str:
    """Отпечаток предпросмотра: подтверждение пишет, только если план и ERP с тех пор не изменились (тот же отпечаток)."""
    raw = json.dumps(sorted((d.json() for d in decisions), key=lambda x: x['isn']), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


# --- fBODY ---

def _lines(body: Any) -> list[str]:
    if not isinstance(body, str):
        raise BodyFormatError('fBODY не текст')
    return body.split(EOL)


def _index(lines: Sequence[str], key: str) -> int | None:
    """Номер строки «KEY:…»; ключ дважды — BodyFormatError."""
    found = [i for i, line in enumerate(lines) if line.startswith(key + ':')]
    if len(found) > 1:
        raise BodyFormatError(f'{key} дважды')
    return found[0] if found else None


def body_values(body: Any) -> tuple[str, str | None]:
    """(значение VANAGENTID, значение DELIVERYCAR или None — строки нет) из fBODY; нет VANAGENTID / ключ дважды —
    BodyFormatError."""
    lines = _lines(body)
    van, car = _index(lines, KEY_VAN), _index(lines, KEY_CAR)
    if van is None:
        raise BodyFormatError('нет строки VANAGENTID')
    return lines[van][len(KEY_VAN) + 1:], None if car is None else lines[car][len(KEY_CAR) + 1:]


def rewrite_body(body: Any, van_id: int, car_code: str | None) -> str:
    """fBODY заказа с новыми առաքիչ и машиной: строка VANAGENTID заменяется; DELIVERYCAR — заменяется, без неё —
    вставляется сразу после VANAGENTID, car_code '' — строка удаляется, None — машина не меняется (строка как была).
    Строки — `\\r\\nKEY:VALUE\\r\\n`, значения бывают с пробелами. Остальной текст — байт-в-байт прежний (проверяется).
    Нет VANAGENTID, ключ дважды, не текст, значение с переводом строки — BodyFormatError."""
    if not isinstance(van_id, int) or isinstance(van_id, bool) or van_id <= 0:
        raise BodyFormatError('առաքիչ — положительный id')
    if car_code is not None and (car_code != car_code.strip() or '\r' in car_code or '\n' in car_code):
        raise BodyFormatError('код машины с краевыми пробелами или переводом строки')
    lines = _lines(body)
    van, car = _index(lines, KEY_VAN), _index(lines, KEY_CAR)
    if van is None:
        raise BodyFormatError('нет строки VANAGENTID')
    out = list(lines)
    out[van] = f'{KEY_VAN}:{van_id}'
    if car_code is not None:
        if car is None and car_code:
            out.insert(van + 1, f'{KEY_CAR}:{car_code}')
        elif car is not None and car_code:
            out[car] = f'{KEY_CAR}:{car_code}'
        elif car is not None:
            del out[car]
    new = EOL.join(out)
    # инвариант: всё, кроме двух строк, — прежнее до байта
    keep = lambda ls: [x for x in ls if not x.startswith(KEY_VAN + ':') and not x.startswith(KEY_CAR + ':')]  # noqa: E731
    if keep(new.split(EOL)) != keep(lines):
        raise BodyFormatError('правка задела другие строки')
    got_van, got_car = body_values(new)
    if got_van != str(van_id) or (car_code is not None and (got_car or '') != car_code):
        raise BodyFormatError('правка не дала нужных значений')
    return new


def change_description(d: Decision) -> str:
    """fCHANGEDESCRIPTION, как пишет ERP: ("Առաքիչ", "A008/6" -> "B008/10"), ("Առաքող ավտոմեքենա", "старый" -> "новый") —
    только изменившиеся поля, через «, »; коды агентов, пусто — ""."""
    parts = []
    if d.new_van != d.old_van:
        parts.append(f'("{LABEL_VAN}", "{d.old_code}" -> "{d.new_code}")')
    if d.car_changes:
        parts.append(f'("{LABEL_CAR}", "{d.old_car}" -> "{d.new_car}")')
    return ', '.join(parts)


def log_comment(site_user: str | None) -> str:
    return (COMMENT_PREFIX + (site_user or ''))[:COMMENT_MAX]


# --- Запись ---

@dataclass(frozen=True)
class Result:
    """Итог записи заказа: ok — COMMIT прошёл; reason — FAIL_REASONS (не записано) или VERIFY_FAILED (записано, но чтение
    назад не совпало); old_car_null — ORDERS.fDELIVERYCAR был NULL до записи (для «Վերադարձնել»)."""
    decision: Decision
    ok: bool
    reason: str = ''
    old_car_null: bool = True


def connect_write(dsn: str) -> Any:
    """Соединение записи: autocommit выключен (транзакция на заказ — apply), таймауты. Строку подключения не логируем."""
    try:
        conn = pyodbc.connect(dsn, autocommit=False, timeout=LOGIN_TIMEOUT_S)
    except pyodbc.Error:
        raise ErpWriteError('Не удалось подключиться к ERP для записи') from None   # текст драйвера может нести DSN
    conn.timeout = QUERY_TIMEOUT_S
    return conn


def write_dsn() -> str | None:
    """Строка подключения записи из env ROUTES_ERP_WRITE_DSN; нет — None (запись выключена)."""
    return os.environ.get(DSN_ENV, '').strip() or None


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:   # откат после обрыва связи — сервер и так откатит незафиксированное
        logger.warning('[Routes] ERP-запись: откат не выполнен', exc_info=True)


def _one(conn: Any, d: Decision, site_user: str | None) -> Result:
    """Один заказ — одна транзакция. Не 1 строка, дочерний документ, не тот fBODY — откат и причина."""
    cur = conn.cursor()
    try:
        if not d.write or d.state not in STATES or d.new_van <= 0:
            raise ValueError('решение не для записи')
        row = cur.execute(SQL_W_ORDER_CAR, (d.isn,)).fetchone()
        old_null = row is None or row[0] is None
        if d.car_changes:
            db_car = d.new_car if d.new_car else (None if d.empty_null else '')
            cur.execute(SQL_W_UPDATE_ORDER, (d.new_van, db_car, d.isn, d.state, d.old_van, d.old_car))
        else:
            cur.execute(SQL_W_UPDATE_ORDER_VAN, (d.new_van, d.isn, d.state, d.old_van, d.old_car))
        if cur.rowcount != 1:
            children = cur.execute(SQL_W_CHILDREN, (d.isn,)).fetchone()[0]
            _rollback(conn)
            return Result(d, False, 'has_child_docs' if children else 'changed_in_erp', old_null)
        if cur.execute(SQL_W_CHILDREN, (d.isn,)).fetchone()[0]:    # и после UPDATE — повторно
            _rollback(conn)
            return Result(d, False, 'has_child_docs', old_null)
        rows = cur.execute(SQL_W_BODY, (d.isn,)).fetchall()
        if len(rows) != 1:
            _rollback(conn)
            return Result(d, False, 'body_format', old_null)
        body, ts = rows[0][0], rows[0][1]
        van, car = body_values(body)
        if van.strip() != str(d.old_van) or (car or '').strip() != d.old_car:
            _rollback(conn)
            return Result(d, False, 'body_mismatch', old_null)
        new_body = rewrite_body(body, d.new_van, d.new_car if d.car_changes else None)
        cur.execute(SQL_W_UPDATE_BODY, (new_body, d.isn, ts))
        if cur.rowcount != 1:
            _rollback(conn)
            return Result(d, False, 'changed_in_erp', old_null)
        cur.execute(SQL_W_LOG, (d.isn, d.state, log_comment(site_user), COMPNAME, change_description(d)))
        if cur.rowcount != 1:
            _rollback(conn)
            return Result(d, False, 'error', old_null)
        try:
            conn.commit()
        except Exception:
            # обрыв на COMMIT: зафиксировано или нет — неизвестно; чтение назад (без NOLOCK — только зафиксированное)
            # решает, а не догадка: новые значения на месте — записано, иначе — нет (или проверить не удалось: ERP
            # покажет следующий предпросмотр)
            logger.exception('[Routes] ERP-запись: заказ %s — COMMIT не подтверждён', d.doc_num)
            _rollback(conn)
            ok = _verify(conn, d)
            return Result(d, ok, '' if ok else 'error', old_null)
    except BodyFormatError:
        _rollback(conn)
        logger.warning('[Routes] ERP-запись: заказ %s — fBODY не того вида, не записан', d.doc_num, exc_info=True)
        return Result(d, False, 'body_format')
    except Exception:
        _rollback(conn)
        logger.exception('[Routes] ERP-запись: заказ %s не записан (откат)', d.doc_num)
        return Result(d, False, 'error')
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return Result(d, True, '' if _verify(conn, d) else VERIFY_FAILED, old_null)


def _verify(conn: Any, d: Decision) -> bool:
    """Чтение назад после COMMIT: ORDERS и fBODY — новые значения. Сбой чтения — не совпало (смотреть в ERP)."""
    cur = conn.cursor()
    try:
        rows = cur.execute(SQL_W_VERIFY, (d.isn,)).fetchall()
        if len(rows) != 1:
            return False
        van_id, car, body = rows[0]
        bvan, bcar = body_values(body)
        return (int(van_id or 0) == d.new_van and (car or '') == d.new_car and bvan == str(d.new_van)
                and (bcar or '').strip() == d.new_car)
    except Exception:
        logger.warning('[Routes] ERP-запись: заказ %s — чтение назад не удалось', d.doc_num, exc_info=True)
        return False
    finally:
        _rollback(conn)   # закрыть неявную транзакцию чтения (autocommit выключен)
        try:
            cur.close()
        except Exception:
            pass


def apply(conn: Any, decisions: Sequence[Decision], site_user: str | None,
          on_result: Callable[[Result], None] | None = None) -> list[Result]:
    """Записать решения write=True — каждое своей транзакцией; пропуски не трогаются. on_result — сразу после заказа
    (журнал сайта: сбой процесса посреди записи не теряет уже записанное). Ошибка заказа не останавливает остальные."""
    out = []
    for d in decisions:
        if not d.write:
            continue
        r = _one(conn, d, site_user)
        logger.info('[Routes] ERP-запись %s (%s): առաքիչ %s→%s, машина %r→%r — %s%s', d.doc_num, d.isn, d.old_code,
                    d.new_code, d.old_car, d.new_car if d.car_changes else d.old_car, 'да' if r.ok else 'нет',
                    f' ({r.reason})' if r.reason else '')
        if on_result is not None:
            on_result(r)
        out.append(r)
    return out
