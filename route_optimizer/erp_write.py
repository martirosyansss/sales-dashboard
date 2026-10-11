# -*- coding: utf-8 -*-
"""Запись в ERP (ответ владельца №97, docs/plans/erp-write-plan.md) — ЕДИНСТВЕННОЕ место, где сайт пишет в боевую ERP.

Сайт ставит в заказы дня առաքիչ и машину по отправленному водителям плану «Развоза» (№81). Пишется ТОЛЬКО:
ORDERS.fVANAGENTID, ORDERS.fDELIVERYCAR; DOCUMENTS.fBODY (только строки VANAGENTID / DELIVERYCAR), fLASTMODIFYDATE,
fLASTMODIFIERID того же fISN (fDOCTYPE = 1); строка истории DOCUMENTSLOG. Ничего больше — накладные, остатки, проводки,
справочники и SALESAGENTCREATEDDOCUMENTBODIES не трогаются (առաքիչ и машина есть ещё только в RETURNORDERS, RETURNS, SALES —
не наши документы). Весь SQL записи — статические константы SQL_W_* с параметрами `?`, без динамического SQL.

Пишет ОДИН сервер: строка подключения ROUTES_ERP_WRITE_DSN задаётся ТОЛЬКО на CT115 (единственный хост записи); на ПК и
прочих копиях её нет — запись выключена (кнопки нет, API — 404). На CT115 записи процессов разводит строка erp_write_lease
базы «Маршрутов» (views._erp_lock). Строка подключения не пишется в журнал.

Инварианты (доказуемо безопасно; ERP — locking read committed, fBODY — nvarchar(max) с двоичным сравнением):
- решение по заказу — чистая функция plan_assignments: пишется только заказ, который видел отправленный план (не заведённый
  после отправки), клиента рейса ровно одной машины T, в состоянии fSTATE 1 или 2, без дочерних документов (DOCPARENTS: ни
  накладной, ни տրամադրում), առաքիչ — ровно один открытый ERP-агент с кодом B… (экспедиторы; A… — менеджеры) с именем
  водителя T на день (водитель дня однозначен: не ведёт две машины, посадка сборки не устарела), fBODY с ровно теми
  строками VANAGENTID / DELIVERYCAR, что в ORDERS (rewrite_body), значения отличаются от текущих; иначе — пропуск;
- соединение записи (connect_write): autocommit выключен, SET XACT_ABORT ON (ошибка — откат всей транзакции сервером),
  SET DEADLOCK_PRIORITY LOW (в тупике уступаем ERP), SET LOCK_TIMEOUT 5000;
- каждый заказ — своя транзакция (apply): перед ней @@TRANCOUNT = 0; UPDATE ORDERS оптимистично по прежним значениям и при
  отсутствии дочерних документов (DOCPARENTS под UPDLOCK, HOLDLOCK: накладная не появится до COMMIT; не ровно 1 строка —
  откат), повторная проверка дочерних документов под тем же замком, fBODY — тот, что видел предпросмотр (sha256), замена
  только двух строк (остальной текст байт-в-байт), одно время SELECT GETDATE() — и в DOCUMENTS.fLASTMODIFYDATE, и в
  DOCUMENTSLOG.fDATE (как пишет сама ERP), UPDATE по прежним fTS и fBODY, чтение назад внутри транзакции (fBODY и
  ORDERS — ровно новые значения), DOCUMENTSLOG (ключ (fISN, fDATE, fOP) занят — log_conflict); строка «pending» журнала
  сайта (до COMMIT); COMMIT; чтение назад; итог в журнал сайта;
- ошибка заказа — откат его транзакции, остальные идут дальше; запись останавливается целиком, если: откат не удался,
  @@TRANCOUNT не 0, обрыв на COMMIT не подтверждён чтением новым соединением (commit_unknown), чтение назад после COMMIT
  не совпало (verify_failed), журнал сайта не пишется, замок записи не продлился.
Предпросмотр читает ERP обычным путём только для чтения (erp.load_order_states).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

import pyodbc

from .erp import Agent, Car, ErpError, OrderState

logger = logging.getLogger(__name__)

DSN_ENV = 'ROUTES_ERP_WRITE_DSN'
MAX_ORDERS_ENV = 'ROUTES_ERP_WRITE_MAX_ORDERS'   # заказов в одной записи; нет — 1 (проба), поднять — настройкой
LOGIN_TIMEOUT_S = 15
QUERY_TIMEOUT_S = 30
LOCK_TIMEOUT_MS = 5000      # ждать чужой замок ERP не дольше 5 с (SQL_W_LOCK_TIMEOUT) — потом ошибка и откат заказа
COMPNAME = 'ARAQICH-SITE'   # DOCUMENTSLOG.fCOMPNAME nvarchar(32)
COMMENT_PREFIX = 'Կայք՝ '
COMMENT_MAX = 100           # DOCUMENTSLOG.fCOMMENT nvarchar(255) — с запасом
CAR_MAX = 12                # ORDERS.fDELIVERYCAR nvarchar(12)
STATES = (1, 2)             # заказ в этих состояниях ERP можно править (fSTATE)
COURIER_PREFIX = 'B'        # առաքիչ — только экспедиторы (коды B…); A… — менеджеры (A000 Rocarm, A008/6 «19 լ» возят сами)
LABEL_VAN = 'Առաքիչ'        # подписи полей в истории ERP (fCHANGEDESCRIPTION), как пишет сама ERP
LABEL_CAR = 'Առաքող ավտոմեքենա'
KEY_VAN = 'VANAGENTID'
KEY_CAR = 'DELIVERYCAR'
EOL = '\r\n'

# причины пропуска (plan_assignments) и неудачи записи (apply); тексты — для страницы
SKIP_REASONS = ('not_in_plan', 'not_in_sent_plan', 'split_store', 'order_state', 'has_child_docs', 'courier_not_found',
                'courier_ambiguous', 'body_format', 'body_mismatch', 'unchanged')
CAR_NOT_IN_ERP = 'car_not_in_erp'   # не пропуск: пишется только առաքիչ
FAIL_REASONS = ('changed_in_erp', 'has_child_docs', 'body_format', 'body_mismatch', 'log_conflict', 'error')
VERIFY_FAILED = 'verify_failed'     # записано (COMMIT), но чтение назад не совпало — смотреть заказ в ERP; стоп
COMMIT_UNKNOWN = 'commit_unknown'   # обрыв на COMMIT и проверка не подтвердила — записано ли, неизвестно; стоп
# почему запись остановлена (Batch.stopped), кроме COMMIT_UNKNOWN / VERIFY_FAILED
STOP_REASONS = ('rollback', 'trancount', 'journal', 'lease')
REASON_HY = {
    'not_in_plan': 'Ուղարկված պլանի երթերում չէ',
    'not_in_sent_plan': 'Ուղարկելուց հետո մուտքագրված պատվեր',
    'split_store': 'Խանութը մի քանի մեքենայի երթերում է',
    'order_state': 'Պատվերի վիճակը թույլ չի տալիս փոխել',
    'has_child_docs': 'Պատվերից արդեն փաստաթուղթ կա (ապրանքագիր)',
    'courier_not_found': 'Մեքենայի վարորդը ERP-ում (B կոդով) չի գտնվել',
    'courier_ambiguous': 'Վարորդը միանշանակ չէ (նույն անունով մի քանի աշխատակից, երկու մեքենա կամ փոխված նստեցում)',
    'unchanged': 'ERP-ում արդեն նույնն է',
    'car_not_in_erp': 'Մեքենան ERP-ում չկա — կգրվի միայն առաքիչը',
    'changed_in_erp': 'Պատվերը փոխվել է ERP-ում — թարմացրեք',
    'body_format': 'Պատվերի տվյալներն ERP-ում անսովոր են — չգրվեց',
    'body_mismatch': 'Պատվերի տվյալներն ERP-ում իրար չեն համապատասխանում — չգրվեց',
    'log_conflict': 'ERP-ի պատմության գրառումը չստացվեց — չգրվեց',
    'error': 'Սխալ — չգրվեց',
    'verify_failed': 'Գրվեց, բայց ստուգումը չհամընկավ — նայեք ERP-ում',
    'commit_unknown': 'Անհայտ՝ ստուգեք ERP-ում',
    'not_written': 'Այս պատվերը կայքից չի գրվել',
}

# --- SQL записи (статические; значения — только параметрами) ---
# настройка соединения записи (connect_write)
SQL_W_XACT_ABORT = 'SET XACT_ABORT ON'
SQL_W_DEADLOCK = 'SET DEADLOCK_PRIORITY LOW'
SQL_W_LOCK_TIMEOUT = 'SET LOCK_TIMEOUT 5000'
# перед заказом: открытой транзакции быть не должно (SELECT без таблиц неявную транзакцию не начинает)
SQL_W_TRANCOUNT = 'SELECT @@TRANCOUNT'
# Внутри транзакции чтение без NOLOCK: видим только зафиксированное (и своё).
SQL_W_ORDER = """SELECT fSTATE, fVANAGENTID, fDELIVERYCAR FROM ORDERS WHERE fISN = ?"""
# Оптимистично: те же состояние, առաքիչ и машина, что в предпросмотре, и ни одного дочернего документа (замок на диапазон
# DOCPARENTS до конца транзакции: накладная из этого заказа не создастся, пока мы не зафиксировали или не откатили)
SQL_W_UPDATE_ORDER = """UPDATE ORDERS SET fVANAGENTID = ?, fDELIVERYCAR = ?
WHERE fISN = ? AND fSTATE = ? AND ISNULL(fVANAGENTID, 0) = ? AND LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?
  AND NOT EXISTS (SELECT 1 FROM DOCPARENTS p WITH (UPDLOCK, HOLDLOCK) WHERE p.fPARENTISN = ORDERS.fISN)"""
# машина не меняется (нет в ERP или та же) — столбец fDELIVERYCAR не трогаем вовсе
SQL_W_UPDATE_ORDER_VAN = """UPDATE ORDERS SET fVANAGENTID = ?
WHERE fISN = ? AND fSTATE = ? AND ISNULL(fVANAGENTID, 0) = ? AND LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?
  AND NOT EXISTS (SELECT 1 FROM DOCPARENTS p WITH (UPDLOCK, HOLDLOCK) WHERE p.fPARENTISN = ORDERS.fISN)"""
SQL_W_CHILDREN = """SELECT COUNT(*) FROM DOCPARENTS WITH (UPDLOCK, HOLDLOCK) WHERE fPARENTISN = ?"""
SQL_W_BODY = """SELECT fBODY, fTS FROM DOCUMENTS WHERE fISN = ? AND fDOCTYPE = 1"""
# одно время правки: DOCUMENTS.fLASTMODIFYDATE и DOCUMENTSLOG.fDATE — одинаковые до миллисекунды, как у самой ERP
SQL_W_NOW = 'SELECT GETDATE()'
# по прежней версии строки (fTS) и прежнему тексту (fBODY — nvarchar(max), двоичное сравнение). CAST: pyodbc передаёт
# строку длиннее 4000 символов как ntext, а nvarchar(max) = ntext — ошибка 402
SQL_W_UPDATE_BODY = """UPDATE DOCUMENTS SET fBODY = CAST(? AS nvarchar(max)), fLASTMODIFYDATE = ?, fLASTMODIFIERID = 0
WHERE fISN = ? AND fDOCTYPE = 1 AND fTS = ? AND fBODY = CAST(? AS nvarchar(max))"""
SQL_W_LOG = """INSERT INTO DOCUMENTSLOG (fISN, fDATE, fUSERID, fSTATE, fOP, fCOMMENT, fCOMPNAME, fCHANGEDESCRIPTION, fSOURCE)
VALUES (?, ?, 0, ?, 2, ?, ?, ?, 0)"""
# чтение назад после COMMIT
SQL_W_VERIFY = """SELECT o.fVANAGENTID, LTRIM(RTRIM(ISNULL(o.fDELIVERYCAR, ''))), d.fBODY
FROM ORDERS o JOIN DOCUMENTS d ON d.fISN = o.fISN AND d.fDOCTYPE = 1
WHERE o.fISN = ?"""


class BodyFormatError(ValueError):
    """fBODY заказа не того вида, что ожидаем (нет строки VANAGENTID, ключ дважды, не текст) — не пишем."""


class BodyMismatchError(BodyFormatError):
    """Строки VANAGENTID / DELIVERYCAR в fBODY не ровно те, что в ORDERS, — ERP расходится сама с собой, не пишем."""


class ErpWriteError(ErpError):
    """Соединение записи не открылось (API — как недоступная ERP, 503)."""


class JournalError(RuntimeError):
    """Журнал сайта не записался — запись останавливается (apply)."""


def name_key(name: str | None) -> str:
    """Имя человека для сравнения водителя дня с ERP-агентом: пробелы схлопнуты (в ERP бывают двойные), регистр не важен."""
    return ' '.join((name or '').split()).casefold()


def body_sha(body: Any) -> str:
    """Отпечаток fBODY (что видел предпросмотр); одиночные суррогаты — как есть (surrogatepass); не текст — ''."""
    return hashlib.sha256(body.encode('utf-8', 'surrogatepass')).hexdigest() if isinstance(body, str) else ''


def max_orders() -> int:
    """Сколько заказов можно записать за раз (env ROUTES_ERP_WRITE_MAX_ORDERS); нет, не число или меньше 1 — 1."""
    raw = os.environ.get(MAX_ORDERS_ENV, '').strip()
    try:
        n = int(raw)
    except ValueError:
        return 1
    return n if n >= 1 else 1


# --- Решения (чисто) ---

@dataclass(frozen=True)
class Decision:
    """Решение по заказу: write — писать (old → new), иначе пропуск с причиной reason (SKIP_REASONS). У записи reason —
    '' или CAR_NOT_IN_ERP (машина не меняется, пишется только առաքիչ). truck — машина плана ('' — заказ не в рейсах; у
    магазина в рейсах нескольких машин — коды через «, »). Коды агентов — для истории ERP и страницы. car_raw — что
    писать в ORDERS.fDELIVERYCAR, когда машина меняется: None — new_car; у «Վերադարձնել» — ровно прежняя строка столбца из
    журнала сайта (не обрезанная; NULL — ''). body_sha — отпечаток fBODY, который видел предпросмотр: в транзакции другой —
    не пишем ('' — не сверять, у «Վերադարձնել»)."""
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
    car_raw: str | None = None
    body_sha: str = ''

    @property
    def car_changes(self) -> bool:
        return self.new_car != self.old_car

    @property
    def db_car(self) -> str:
        """Значение ORDERS.fDELIVERYCAR после записи, если машина меняется (никогда не NULL: ERP хранит '')."""
        return self.car_raw if self.car_raw is not None else self.new_car

    def json(self) -> dict[str, Any]:
        return asdict(self)


def courier_of(name: str | None, agents: Mapping[int, Agent]) -> tuple[Agent | None, str]:
    """ERP-агент водителя по имени: (агент, '') или (None, 'courier_not_found' | 'courier_ambiguous'). Только открытые
    экспедиторы с кодом B… (A… — менеджеры: совпало только с ним — не найден); тёзки — не угадываем."""
    key = name_key(name)
    if not key:
        return None, 'courier_not_found'
    found = [a for a in agents.values()
             if not a.closed and a.code.startswith(COURIER_PREFIX) and name_key(a.name) == key]
    if not found:
        return None, 'courier_not_found'
    if len(found) > 1:
        return None, 'courier_ambiguous'
    return found[0], ''


def plan_assignments(trips: Sequence[tuple[str, Sequence[int]]], orders: Sequence[OrderState],
                     drivers: Mapping[str, str | None], agents: Mapping[int, Agent],
                     cars: Mapping[str, Car], seen: Collection[str], ambiguous: Collection[str] = ()) -> list[Decision]:
    """Решения по заказам отправленного плана дня (порядок — как orders).

    trips — рейсы отправленного водителям плана (Draft.for_drivers): (машина, клиенты); orders — текущее состояние ERP
    заказов магазинов плана с fBODY (erp.load_order_states); drivers — машина → водитель на день (None — не указан / не
    вышел); agents — ERP-агенты (id → агент); cars — машины ERP CARS (код → машина); seen — заказы, которые видел
    отправленный план (заказы сборки, добавленные, взятые заказы дня); ambiguous — машины, чей водитель дня неоднозначен
    (ведёт две машины, посадка сборки устарела). Правила — по порядку: магазин в рейсах ни одной машины — not_in_plan;
    заказа отправленный план не видел — not_in_sent_plan; магазин в рейсах нескольких машин — split_store; fSTATE не 1/2 —
    order_state; есть дочерние документы — has_child_docs; водитель машины неоднозначен — courier_ambiguous; առաքիչ — ровно
    один открытый агент B… с именем водителя машины (courier_of); машина — код T, если она есть в CARS, не закрыта и влезает
    в столбец, иначе машина заказа не меняется (CAR_NOT_IN_ERP); ничего не меняется — unchanged; fBODY не того вида или его
    строки не те, что в ORDERS (rewrite_body), — body_format / body_mismatch."""
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
                        old_code, old_code, body_sha=body_sha(o.body))
        reason = ('not_in_plan' if not trucks else 'not_in_sent_plan' if o.isn not in seen
                  else 'split_store' if len(trucks) > 1 else 'order_state' if o.state not in STATES
                  else 'has_child_docs' if o.children else 'courier_ambiguous' if truck in ambiguous else '')
        agent = None
        if not reason:
            agent, reason = courier_of(drivers.get(truck), agents)
        if reason:
            out.append(replace(base, reason=reason))
            continue
        assert agent is not None
        car = cars.get(truck)
        in_erp = car is not None and not car.closed and len(truck) <= CAR_MAX
        new_car = truck if in_erp else o.car
        d = replace(base, new_van=agent.id, new_car=new_car, new_code=agent.code, new_name=' '.join(agent.name.split()),
                    reason='' if in_erp else CAR_NOT_IN_ERP)
        if d.new_van == d.old_van and not d.car_changes:
            out.append(replace(d, reason='unchanged'))
            continue
        try:
            new_body(o.body, d)
        except BodyMismatchError:
            out.append(replace(base, reason='body_mismatch'))
            continue
        except BodyFormatError:
            out.append(replace(base, reason='body_format'))
            continue
        out.append(replace(d, write=True))
    return out


def signature(decisions: Sequence[Decision]) -> str:
    """Отпечаток предпросмотра (и fBODY заказов — body_sha): подтверждение пишет, только если план и ERP с тех пор не
    изменились (тот же отпечаток)."""
    raw = json.dumps(sorted((d.json() for d in decisions), key=lambda x: x['isn']), ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode('ascii')).hexdigest()[:32]


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


def _clean(value: str, what: str) -> None:
    if '\r' in value or '\n' in value or value != value.strip():
        raise BodyFormatError(f'{what}: перевод строки или краевые пробелы')


def rewrite_body(body: Any, van_id: int, car_code: str | None, *, old_van: int, old_car: str) -> str:
    """fBODY заказа с новыми առաքիչ и машиной. Сначала — прежние строки ровно те, что в ORDERS: «VANAGENTID:old_van» и
    «DELIVERYCAR:old_car» (old_car '' — строки нет), иначе BodyMismatchError. Строка VANAGENTID заменяется; DELIVERYCAR —
    заменяется, без неё — вставляется сразу после VANAGENTID, car_code '' — строка удаляется, None — машина не меняется
    (строка как была). Строки — `\\r\\nKEY:VALUE\\r\\n`, значения бывают с пробелами внутри. Остальной текст — байт-в-байт
    прежний (проверяется). Нет VANAGENTID, ключ дважды, не текст, значение с переводом строки или краевыми пробелами —
    BodyFormatError."""
    for v in (van_id, old_van):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BodyFormatError('առաքիչ — целый id')
    if van_id <= 0:
        raise BodyFormatError('առաքիչ — положительный id')
    if not isinstance(old_car, str) or (car_code is not None and not isinstance(car_code, str)):
        raise BodyFormatError('машина — строка')
    _clean(old_car, 'прежняя машина')
    if car_code is not None:
        _clean(car_code, 'код машины')
        if len(car_code) > CAR_MAX:
            raise BodyFormatError('код машины длиннее столбца')
    lines = _lines(body)
    van, car = _index(lines, KEY_VAN), _index(lines, KEY_CAR)
    if van is None:
        raise BodyFormatError('нет строки VANAGENTID')
    if lines[van] != f'{KEY_VAN}:{old_van}' or (lines[car] if car is not None else '') != (
            f'{KEY_CAR}:{old_car}' if old_car else ''):
        raise BodyMismatchError('строки VANAGENTID / DELIVERYCAR не те, что в ORDERS')
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


def new_body(body: Any, d: Decision) -> str:
    """fBODY после записи решения d (rewrite_body: прежние строки — d.old_*, машина — только если меняется)."""
    return rewrite_body(body, d.new_van, d.new_car if d.car_changes else None, old_van=d.old_van, old_car=d.old_car)


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
    """Итог записи заказа: ok — COMMIT прошёл (и чтение назад его подтвердило, если COMMIT оборвался); reason —
    FAIL_REASONS (не записано), VERIFY_FAILED (записано, но чтение назад не совпало) или COMMIT_UNKNOWN (не ok: записано
    ли — неизвестно); old_car_raw — ORDERS.fDELIVERYCAR до записи ровно как в столбце (NULL — ''), для «Վերադարձնել»."""
    decision: Decision
    ok: bool
    reason: str = ''
    old_car_raw: str = ''


@dataclass(frozen=True)
class Batch:
    """Итог apply: результаты по порядку; stopped — почему запись остановлена (COMMIT_UNKNOWN, VERIFY_FAILED,
    STOP_REASONS); '' — прошли все решения."""
    results: tuple[Result, ...]
    stopped: str = ''


class Journal(Protocol):
    """Журнал сайта для apply (views): renew — продлить замок записи (False — стоп); pending — строка «pending» перед
    COMMIT (→ токен; JournalError — откат заказа и стоп); finish — итог заказа (токен None — заказ не дошёл до COMMIT,
    новая строка; JournalError — стоп)."""

    def renew(self) -> bool: ...

    def pending(self, d: Decision, old_car_raw: str) -> Any: ...

    def finish(self, token: Any, r: Result) -> None: ...


def connect_write(dsn: str) -> Any:
    """Соединение записи: autocommit выключен (транзакция на заказ — apply), таймауты, XACT_ABORT, низкий приоритет в
    тупике, чужой замок — не дольше LOCK_TIMEOUT_MS. Строку подключения не логируем."""
    try:
        conn = pyodbc.connect(dsn, autocommit=False, timeout=LOGIN_TIMEOUT_S)
    except pyodbc.Error:
        raise ErpWriteError('Не удалось подключиться к ERP для записи') from None   # текст драйвера может нести DSN
    conn.timeout = QUERY_TIMEOUT_S
    try:
        cur = conn.cursor()
        for sql in (SQL_W_XACT_ABORT, SQL_W_DEADLOCK, SQL_W_LOCK_TIMEOUT):
            cur.execute(sql)
        cur.close()
        conn.commit()
    except pyodbc.Error:
        try:
            conn.close()
        except pyodbc.Error:
            pass
        raise ErpWriteError('Не удалось настроить соединение записи ERP') from None
    return conn


def write_dsn() -> str | None:
    """Строка подключения записи из env ROUTES_ERP_WRITE_DSN (задаётся только на CT115); нет — None (запись выключена)."""
    return os.environ.get(DSN_ENV, '').strip() or None


def _rollback(conn: Any) -> bool:
    """Откат; не удался — False (транзакция, может быть, открыта: дальше не пишем)."""
    try:
        conn.rollback()
        return True
    except Exception:
        logger.error('[Routes] ERP-запись: откат не выполнен — запись остановлена', exc_info=True)
        return False


def _trancount(conn: Any) -> int | None:
    """@@TRANCOUNT соединения; не прочиталось — None."""
    try:
        cur = conn.cursor()
        try:
            row = cur.execute(SQL_W_TRANCOUNT).fetchone()
        finally:
            cur.close()
        return int(row[0])
    except Exception:
        logger.error('[Routes] ERP-запись: @@TRANCOUNT не прочитан — запись остановлена', exc_info=True)
        return None


class _Abort(Exception):
    """Откат заказа с причиной (внутри _one)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _one(conn: Any, d: Decision, site_user: str | None, reconnect: Callable[[], Any] | None,
         journal: Journal | None) -> tuple[Result, str, Any]:
    """Один заказ — одна транзакция → (итог, почему остановить запись или '', токен строки журнала «pending» или None).
    Любое расхождение — откат и причина; откат не удался — стоп; обрыв на COMMIT — COMMIT_UNKNOWN, если чтение назад новым
    соединением не подтвердило запись; чтение назад после COMMIT не совпало — VERIFY_FAILED; оба — стоп."""
    cur = conn.cursor()
    old_raw = ''
    token = None

    def fail(reason: str) -> tuple[Result, str, Any]:
        return Result(d, False, reason, old_raw), '' if _rollback(conn) else 'rollback', None
    try:
        if not d.write or d.state not in STATES or d.new_van <= 0 or len(d.db_car) > CAR_MAX:
            raise ValueError('решение не для записи')
        row = cur.execute(SQL_W_ORDER, (d.isn,)).fetchone()
        old_raw = (row[2] or '') if row is not None else ''
        if d.car_changes:
            cur.execute(SQL_W_UPDATE_ORDER, (d.new_van, d.db_car, d.isn, d.state, d.old_van, d.old_car))
        else:
            cur.execute(SQL_W_UPDATE_ORDER_VAN, (d.new_van, d.isn, d.state, d.old_van, d.old_car))
        if cur.rowcount != 1:      # 0 — изменили или есть накладная; -1 (NOCOUNT) и больше — не знаем: откат
            raise _Abort('has_child_docs' if cur.rowcount == 0 and cur.execute(SQL_W_CHILDREN, (d.isn,)).fetchone()[0]
                         else 'changed_in_erp')
        if cur.execute(SQL_W_CHILDREN, (d.isn,)).fetchone()[0]:    # и после UPDATE — повторно, под тем же замком
            raise _Abort('has_child_docs')
        rows = cur.execute(SQL_W_BODY, (d.isn,)).fetchall()
        if len(rows) != 1:
            raise _Abort('body_format')
        body, ts = rows[0][0], rows[0][1]
        if d.body_sha and body_sha(body) != d.body_sha:            # не тот fBODY, что видел предпросмотр
            raise _Abort('changed_in_erp')
        text = new_body(body, d)
        now = cur.execute(SQL_W_NOW).fetchone()[0]
        cur.execute(SQL_W_UPDATE_BODY, (text, now, d.isn, ts, body))
        if cur.rowcount != 1:
            raise _Abort('changed_in_erp')
        # чтение назад внутри транзакции: ровно то, что записали
        after = cur.execute(SQL_W_BODY, (d.isn,)).fetchall()
        if len(after) != 1 or after[0][0] != text:
            raise _Abort('body_mismatch')
        o = cur.execute(SQL_W_ORDER, (d.isn,)).fetchone()
        want_car = d.db_car if d.car_changes else (row[2] if row is not None else None)
        if o is None or (o[0], int(o[1] or 0), o[2]) != (d.state, d.new_van, want_car):
            raise _Abort('changed_in_erp')
        try:
            cur.execute(SQL_W_LOG, (d.isn, now, d.state, log_comment(site_user), COMPNAME, change_description(d)))
        except pyodbc.IntegrityError:    # ключ DOCUMENTSLOG (fISN, fDATE, fOP) уже занят
            raise _Abort('log_conflict') from None
        if cur.rowcount != 1:
            raise _Abort('error')
        if journal is not None:
            try:
                token = journal.pending(d, old_raw)
            except JournalError:
                res, stop, _ = fail('error')
                return res, stop or 'journal', None
        try:
            conn.commit()
        except Exception:
            # обрыв на COMMIT: зафиксировано или нет — неизвестно. Решает чтение назад НОВЫМ соединением (locking read
            # committed: незавершённая транзакция держала бы замок — чтение ждёт и падает, а не видит чужое); не
            # подтвердилось — COMMIT_UNKNOWN, запись останавливается
            logger.exception('[Routes] ERP-запись: заказ %s (%s) — COMMIT не подтверждён', d.doc_num, d.isn)
            _rollback(conn)
            if _verify_new(reconnect, d):
                return Result(d, True, '', old_raw), '', token
            return Result(d, False, COMMIT_UNKNOWN, old_raw), COMMIT_UNKNOWN, token
    except _Abort as a:
        return fail(a.reason)
    except BodyMismatchError:
        logger.warning('[Routes] ERP-запись: заказ %s — строки fBODY не те, что в ORDERS, не записан', d.doc_num)
        return fail('body_mismatch')
    except BodyFormatError:
        logger.warning('[Routes] ERP-запись: заказ %s — fBODY не того вида, не записан', d.doc_num, exc_info=True)
        return fail('body_format')
    except Exception:
        logger.exception('[Routes] ERP-запись: заказ %s не записан (откат)', d.doc_num)
        return fail('error')
    finally:
        try:
            cur.close()
        except Exception:
            pass
    if _verify(conn, d):
        return Result(d, True, '', old_raw), '', token
    return Result(d, True, VERIFY_FAILED, old_raw), VERIFY_FAILED, token


def _verify(conn: Any, d: Decision) -> bool:
    """Чтение назад после COMMIT: ORDERS и fBODY — новые значения. Сбой чтения — не совпало (смотреть в ERP)."""
    try:
        cur = conn.cursor()
    except Exception:
        logger.warning('[Routes] ERP-запись: заказ %s — чтение назад не удалось', d.doc_num, exc_info=True)
        return False
    try:
        rows = cur.execute(SQL_W_VERIFY, (d.isn,)).fetchall()
        if len(rows) != 1:
            return False
        van_id, car, text = rows[0]
        bvan, bcar = body_values(text)
        return (int(van_id or 0) == d.new_van and (car or '') == d.new_car and bvan == str(d.new_van)
                and (bcar or '') == d.new_car)
    except Exception:
        logger.warning('[Routes] ERP-запись: заказ %s — чтение назад не удалось', d.doc_num, exc_info=True)
        return False
    finally:
        _rollback(conn)   # закрыть неявную транзакцию чтения (autocommit выключен)
        try:
            cur.close()
        except Exception:
            pass


def _verify_new(reconnect: Callable[[], Any] | None, d: Decision) -> bool:
    """Чтение назад новым соединением (после обрыва на COMMIT); нет способа или сбой — не подтверждено."""
    if reconnect is None:
        return False
    try:
        conn = reconnect()
    except Exception:
        logger.warning('[Routes] ERP-запись: новое соединение для проверки %s не открылось', d.doc_num, exc_info=True)
        return False
    try:
        return _verify(conn, d)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def apply(conn: Any, decisions: Sequence[Decision], site_user: str | None, journal: Journal | None = None,
          reconnect: Callable[[], Any] | None = None) -> Batch:
    """Записать решения write=True — каждое своей транзакцией; пропуски не трогаются. Перед заказом: замок записи продлён
    (journal.renew), @@TRANCOUNT = 0 — иначе стоп. Ошибка заказа не останавливает остальные; стоп — откат не удался,
    COMMIT_UNKNOWN, VERIFY_FAILED, журнал не пишется (journal), замок не продлился. reconnect — новое соединение для
    проверки после обрыва на COMMIT."""
    out: list[Result] = []
    for d in decisions:
        if not d.write:
            continue
        if journal is not None and not journal.renew():
            return Batch(tuple(out), 'lease')
        if _trancount(conn) != 0:
            return Batch(tuple(out), 'trancount')
        r, stop, token = _one(conn, d, site_user, reconnect, journal)
        logger.info('[Routes] ERP-запись %s (%s): առաքիչ %s→%s, машина %r→%r — %s%s', d.doc_num, d.isn, d.old_code,
                    d.new_code, d.old_car, d.new_car if d.car_changes else d.old_car, 'да' if r.ok else 'нет',
                    f' ({r.reason})' if r.reason else '')
        out.append(r)
        if journal is not None:
            try:
                journal.finish(token, r)
            except JournalError:
                return Batch(tuple(out), 'journal')
        if stop:
            return Batch(tuple(out), stop)
    return Batch(tuple(out))
