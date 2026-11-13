"""Мост сайта к native-процессу: локальная копия ERP или подписанный HTTP к Windows-мосту."""
from __future__ import annotations

from contextlib import closing
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

from . import erp_chain_guard as guard

logger = logging.getLogger(__name__)
ROOT = Path(r'G:\Araqich-ERP-Test-20261105-SQL2025')
SERVER = r'(localdb)\AraqichERP20261105'
DATABASE = 'AraqichTest_20261105'
BRIDGE_ENV = ('ROUTES_ERP_CHAIN_BRIDGE_URL', 'ROUTES_ERP_CHAIN_BRIDGE_SECRET_FILE', 'ROUTES_ERP_CHAIN_JOURNAL')
WRITE_ACTIONS = frozenset({'execute', 'reconcile-execute'})
STALE_READY_LEASE_SECONDS = 720  # предпросмотр native живёт 10 минут, плюс запас на расхождение часов с мостом
MAX_BRIDGE_REPLY = 32 * 1024 * 1024
BUSY_NEUTRAL = 'ERP-ի մեկ այլ գործողություն է ընթանում։ Քիչ անց կրկին փորձեք։'
ERRORS = {
    'writes_disabled': 'ERP-ում գրանցումը դեռ միացված չէ։ Այս փորձով գրանցում չի կատարվել։',
    'operation_active': 'ERP-ի գործողությունը դեռ ընթանում է։ Մի փոքր անց կրկին ստուգեք արդյունքը։',
    'release_not_allowed': 'Պլանը ձեռքով կարելի է ազատել միայն մասնակի կամ անորոշ արդյունքի դեպքում։',
    'quarantined': 'ERP-ի գրանցումները ժամանակավորապես կանգնեցված են ստուգման պատճառով։',
    'stock': 'Ընտրված պահեստում հասանելի քանակը չի բավարարում՝ հաշվի առնելով պահուստավորումները։',
    'changed': 'Պլանը կամ ERP-ի պատվերները փոխվել են․ նորից բացեք նախադիտումը։',
    'expired': 'Նախադիտման ժամկետն ավարտվել է․ նորից ստուգեք պատվերները։',
    'directory': 'Պահեստը, մեքենան կամ առաքիչը ERP-ում հասանելի չէ կամ փակ է։',
    'unsupported': 'Այս տարբերակում հարկային արտահանմամբ, փոխանակումով կամ վաճառվող տարայով պատվերը դեռ չի աջակցվում։',
    'empty': 'Տրամադրման և վաճառքի համար հասանելի պատվեր չկա։',
    'claimed': 'Պատվերն արդեն ներառված է այլ գրանցման մեջ․ ստուգեք դրա արդյունքը։',
    'order_state': 'Պատվերի վիճակը թույլ չի տալիս ստեղծել տրամադրում և վաճառք։',
    'too_many': 'Ընտրված պատվերների քանակը գերազանցում է թույլատրված սահմանը։',
    'not_found': 'Գրանցումը չի գտնվել։',
    'past': 'Անցած օրվա համար փաստաթղթեր չեն ստեղծվում։',
    'unknown': 'Գրանցման արդյունքը դեռ հաստատված չէ։ Սեղմեք «Ստուգել արդյունքը»․ կրկնակի գրանցում չի կատարվի։',
    'busy': 'ERP-ի մեկ այլ գործողություն է ընթանում։ Այս փորձով գրանցում չի կատարվել․ քիչ անց բացեք նոր նախադիտում։',
    'providing_missing': 'Տրամադրումը բոլոր պահեստների համար չի ստեղծվել։ Ավարտեք գրանցումը ՀԾ-ում ձեռքով, ապա սեղմեք «Ազատել պլանը»։',
    'foreign_child': 'Պատվերի փաստաթղթերի կապերը չեն համընկնում մատյանին․ լրացումը կանգնեցված է։',
    'unregistered': 'Հիմք հանդիսացող փաստաթուղթը ՀԾ-ում գրանցված չէ․ լրացումը կանգնեցված է։',
    'verification': 'ՀԾ-ի փաստաթղթերի գումարները, քանակները կամ կապերը չեն համընկնում․ անհրաժեշտ է համադրում։',
    'bad_request': 'Ստուգեք ընտրված տվյալները և կրկին փորձեք։',
    'reason': 'Լրացրեք ստուգման պատճառը՝ 10–500 նիշով։',
    'already_complete': 'Գրանցումն արդեն ավարտված է․ ստուգեք արդյունքը։',
    'courier_choice': 'Ընտրեք վարորդի ERP կոդը։',
    'courier_twice': 'Նույն ERP կոդը երկու մեքենայի համար ընտրել չի կարելի։',
    'person_changed': 'Վարորդը փոխվել է․ նորից բացեք պատուհանը։',
    'order_warehouse': 'Պատվերի պահեստը հնարավոր չէ որոշել։',
    'reserve_choice': 'Պատվերի պահուստն այլ պահեստում է, ոչ բաժնի պահեստում։ Ցուցակում նշեք պատվերը և ընտրեք պահեստը։',
}


class ServiceError(RuntimeError):
    def __init__(self, code: str, status: int = 409, *, no_write: bool = False, detail: str = ''):
        self.code, self.http_status, self.no_write = code, status, no_write
        text = BUSY_NEUTRAL if code == 'busy' and not no_write else ERRORS.get(code, 'ERP-ի գործողության արդյունքը հաստատված չէ․ ստուգեք տվյալները։')
        super().__init__(text + (' ' + detail if detail else ''))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identifier(value: str) -> str:
    try:
        parsed = UUID(value)
        if not parsed.int:
            raise ValueError()
        return str(parsed)
    except (ValueError, TypeError, AttributeError) as error:
        raise ServiceError('not_found', 404) from error


def _proven_no_write(request: dict[str, Any], data: dict[str, Any]) -> bool:
    """Отказ чтения (например busy) ничего не доказывает о записи, идущей в этот момент."""
    return request.get('action') in WRITE_ACTIONS and data.get('no_write_dispatched') is True


def _label_providings(result: dict[str, Any]) -> dict[str, Any]:
    """Шаг providing подписывается складом; склад незавершённой записи, у которого шага нет, — отдельная строка 'missing'."""
    loads, steps = result.get('loads'), result.get('steps')
    if not isinstance(loads, list) or not isinstance(steps, list):
        return result
    orders = [{str(o.get('isn')).lower() for o in load.get('orders', [])} for load in loads]
    labelled, covered = [], set()
    for step in steps:
        if step.get('kind') == 'providing':
            parents = {str(i).lower() for i in step.get('order_isns', [])}
            index = next((n for n, isns in enumerate(orders) if isns & parents), None)
            if index is not None:
                covered.add(index)
                step = {**step, 'warehouse': loads[index].get('warehouse')}
        labelled.append(step)
    if result.get('status') in ('partial', 'unknown'):
        labelled += [{'kind': 'providing', 'warehouse': load.get('warehouse'), 'status': 'missing'}
                     for n, load in enumerate(loads) if n not in covered]
    return {**result, 'steps': labelled}


class LocalNative:
    """Без shell и секретов в аргументах; native самостоятельно проверяет firewall и SQL."""
    database = DATABASE

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [str(ROOT / 'harness' / 'AraqichNativeTest.exe'), 'dispatch'],
                input=json.dumps(request), text=True, encoding='utf-8',
                capture_output=True, timeout=150, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode:
                raise ServiceError('unknown', 503)
            data = json.loads(result.stdout.lstrip('\ufeff'))
            if not data.get('success'):
                raise ServiceError(data.get('code', 'unknown'), no_write=_proven_no_write(request, data))
            response = data['result']
            if response.get('database') != DATABASE or response.get('environment', 'isolated-test') != 'isolated-test':
                raise ServiceError('unknown', 503)
            return response
        except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as error:
            raise ServiceError('unknown', 503) from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class RemoteNative:
    """Подписанный HTTP к Windows-мосту, без повторов: любой сбой транспорта или подписи — 'unknown'."""
    def __init__(self, url: str, secret: bytes, *, database: str = 'SalesManagement', environment: str = 'production',
                 read_timeout: float = 170, write_timeout: float = 960):
        self.url, self.secret, self.database, self.environment = url.rstrip('/'), secret, database, environment
        self.read_timeout, self.write_timeout = read_timeout, write_timeout
        # Запрос идёт только на указанный хост: без прокси окружения и без перенаправлений.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _sign(self, text: str) -> str:
        return hmac.new(self.secret, text.encode('utf-8'), hashlib.sha256).hexdigest()

    def _exchange(self, outgoing: urllib.request.Request, timeout: float) -> tuple[int, Any, bytes]:
        try:
            with self._opener.open(outgoing, timeout=timeout) as reply:
                return reply.status, reply.headers, reply.read(MAX_BRIDGE_REPLY + 1)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers, error.read(MAX_BRIDGE_REPLY + 1)

    def _send(self, method: str, path: str, body: bytes, timeout: float) -> tuple[int, dict[str, Any]]:
        stamp, nonce = str(int(time.time())), secrets.token_hex(16)
        signature = self._sign(f'v1\n{method}\n{path}\n{stamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}')
        outgoing = urllib.request.Request(self.url + path, data=body if method == 'POST' else None, method=method, headers={
            'Content-Type': 'application/json; charset=utf-8', 'X-Araqich-Timestamp': stamp,
            'X-Araqich-Nonce': nonce, 'X-Araqich-Signature': signature})
        try:
            status, headers, payload = self._exchange(outgoing, timeout)
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise ServiceError('unknown', 503) from error
        expected = self._sign(f'v1-resp\n{nonce}\n{status}\n{hashlib.sha256(payload).hexdigest()}')
        given = (headers.get('X-Araqich-Signature') or '').encode('utf-8', 'replace')
        if len(payload) > MAX_BRIDGE_REPLY or not hmac.compare_digest(given, expected.encode('ascii')):
            raise ServiceError('unknown', 503)
        try:
            data = json.loads(payload.decode('utf-8').lstrip('﻿'))
        except ValueError as error:
            raise ServiceError('unknown', 503) from error
        if not isinstance(data, dict):
            raise ServiceError('unknown', 503)
        return status, data

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        timeout = self.write_timeout if request.get('action') in WRITE_ACTIONS else self.read_timeout
        status, data = self._send('POST', '/v1/dispatch', json.dumps(request, ensure_ascii=False).encode('utf-8'), timeout)
        if data.get('success') is not True:
            code = data.get('code') if isinstance(data.get('code'), str) and data.get('code') else 'unknown'
            raise ServiceError(code, 503 if code == 'unknown' else 409, no_write=_proven_no_write(request, data))
        response = data.get('result')
        if (status != 200 or not isinstance(response, dict) or response.get('database') != self.database
                or response.get('environment') != self.environment):
            raise ServiceError('unknown', 503)
        return response

    def health(self) -> dict[str, Any]:
        status, data = self._send('GET', '/v1/health', b'', self.read_timeout)
        if status != 200 or data.get('success') is not True:
            raise ServiceError('unknown', 503)
        return data


class ChainService:
    def __init__(self, journal: Path, native: LocalNative | RemoteNative | None = None, *, quarantine: Path | None = None):
        self.journal, self.native = journal, native or LocalNative()
        self.database = getattr(self.native, 'database', DATABASE)
        self.quarantine = quarantine if quarantine is not None else ROOT / 'QUARANTINED.json'

    def _db(self) -> sqlite3.Connection:
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.journal, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS previews(
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, day TEXT NOT NULL, plan TEXT NOT NULL,
                signature TEXT NOT NULL, native_preview TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_claims(
                database_id TEXT NOT NULL, order_isn TEXT NOT NULL, operation_id TEXT NOT NULL,
                PRIMARY KEY(database_id,order_isn)
            );
            CREATE TABLE IF NOT EXISTS no_write_refusals(
                operation_id TEXT PRIMARY KEY, code TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plan_lease_releases(
                operation_id TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
                released_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS person_cards(
                person_id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reconciliation_reviews(
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, actor TEXT NOT NULL,
                preview TEXT NOT NULL, status TEXT NOT NULL, reason TEXT
            );
        ''')
        return db

    def _refuse_card_conflict(self, db: sqlite3.Connection, day: str, codes: dict[str, str], own: str | None = None) -> None:
        """Один код — одна машина за день. Разные коды на одной машине допустимы: водитель мог смениться днём."""
        rows = db.execute("SELECT id,plan FROM previews WHERE day=? AND status NOT IN ('ready','refused')", (day,)).fetchall()
        for row in rows:
            if row['id'] == own:
                continue
            for load in json.loads(row['plan'])['loads']:
                for car, code in codes.items():
                    if load['courier'] == code and load['car'] != car:
                        raise ServiceError('courier_twice')

    def bindings(self) -> dict[int, str]:
        """Человек терминала (courier drivers.id) → его карточка ERP."""
        with closing(self._db()) as db:
            return {row['person_id']: row['code'] for row in db.execute('SELECT person_id,code FROM person_cards')}

    def claims(self, isns: list[str]) -> dict[str, str]:
        """fISN заказа (верхний регистр) → статус записи сайта, которая его держит. Только чтение журнала."""
        keys = {}
        for isn in isns:
            try:
                keys[str(UUID(isn))] = isn.upper()
            except (ValueError, TypeError, AttributeError):
                continue
        with closing(self._db()) as db:
            rows = db.execute('SELECT c.order_isn, p.status FROM order_claims c JOIN previews p ON p.id = c.operation_id '
                              'WHERE c.database_id = ?', (self.database,)).fetchall()
        return {keys[row[0]]: row[1] for row in rows if row[0] in keys}

    def _remember_cards(self, db: sqlite3.Connection, plan: dict[str, Any], actor: str) -> None:
        """Первый код завершённой записи запоминается за человеком, если кода у него ещё нет и код не закреплён за другим.
        Разовая замена кода оператором существующую привязку не меняет."""
        for load in plan['loads']:
            person = load.get('person')
            if person is None:
                continue
            stored = db.execute('SELECT code FROM person_cards WHERE person_id=?', (person,)).fetchone()
            taken = db.execute('SELECT 1 FROM person_cards WHERE code=? AND person_id<>? LIMIT 1', (load['courier'], person)).fetchone()
            if taken is None and stored is None:
                db.execute('INSERT INTO person_cards(person_id,name,code,updated_by) VALUES(?,?,?,?)',
                           (person, load.get('person_name') or '', load['courier'], actor))

    def _set_status(self, db: sqlite3.Connection, token: str, status: str) -> None:
        """Статус записи; только первый переход в complete запоминает карточки её людей."""
        row = db.execute('SELECT status,plan,actor FROM previews WHERE id=?', (token,)).fetchone()
        db.execute('UPDATE previews SET status=? WHERE id=?', (status, token))
        if status == 'complete' and row is not None and row['status'] != 'complete':
            self._remember_cards(db, json.loads(row['plan']), row['actor'])

    def check_cards(self, day: str, codes: dict[str, str]) -> None:
        with closing(self._db()) as db:
            self._refuse_card_conflict(db, day, codes)

    def options(self) -> dict[str, Any]:
        return self.native.call({'action': 'options'})

    def preview(self, actor: str, plan: dict[str, Any]) -> dict[str, Any]:
        result = self.native.call({'action': 'preview', 'plan': plan})
        token = identifier(result['id'])
        with closing(self._db()) as db, db:
            db.execute('INSERT INTO previews VALUES(?,?,?,?,?,?,?)',
                       (token, actor, plan['day'], json.dumps(plan), digest(plan), json.dumps(result), 'ready'))
        return result

    def get(self, token: str, actor: str) -> dict[str, Any]:
        with closing(self._db()) as db:
            row = db.execute('SELECT * FROM previews WHERE id=? AND actor=?', (identifier(token), actor)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        return dict(row)

    def execute(self, token: str, actor: str, current_plan: dict[str, Any] | Callable[[], dict[str, Any]],
                *, plan_store: Any = None) -> dict[str, Any]:
        token = identifier(token)
        record = self.get(token, actor)
        if record['status'] != 'ready':
            return self.status(token, actor, plan_store=plan_store)
        if self.quarantine.exists():
            raise ServiceError('quarantined', 503)
        leased = False
        native_started = False
        if plan_store is not None:
            if not guard.acquire(plan_store, json.loads(record['plan']), token):
                raise ServiceError('changed')
            leased = True
        try:
            # Живой план перечитывается только после установки долговечной защиты изменений.
            current = current_plan() if callable(current_plan) else current_plan
            with closing(self._db()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM previews WHERE id=? AND actor=?', (token, actor)).fetchone()
                if row is None:
                    raise ServiceError('not_found', 404)
                replay = row['status'] != 'ready'
                if not replay:
                    if digest(current) != row['signature']:
                        raise ServiceError('changed')
                    self._refuse_card_conflict(db, row['day'], {x['car']: x['courier'] for x in current['loads']}, token)
                    preview = json.loads(row['native_preview'])
                    if not preview['order_count']:
                        raise ServiceError('empty')
                    try:
                        db.executemany('INSERT INTO order_claims VALUES(?,?,?)',
                                       [(self.database, identifier(o['isn']), token)
                                        for load in preview['loads'] for o in load['orders']])
                    except sqlite3.IntegrityError as error:
                        raise ServiceError('claimed') from error
                    db.execute("UPDATE previews SET status='running' WHERE id=?", (token,))
            if replay:
                # Другой процесс уже занял заказы: только читаем его результат, не снимая защиту.
                native_started = True
                return self.status(token, actor, plan_store=plan_store)
            native_started = True
            try:
                result = self.native.call({'action': 'execute', 'id': token})
            except Exception as error:
                with closing(self._db()) as db, db:
                    known_no_write = isinstance(error, ServiceError) and error.no_write
                    db.execute('UPDATE previews SET status=? WHERE id=?', ('refused' if known_no_write else 'unknown', token))
                    if known_no_write:
                        db.execute('DELETE FROM order_claims WHERE operation_id=?', (token,))
                        db.execute('INSERT OR REPLACE INTO no_write_refusals VALUES(?,?)', (token, error.code))
                raise
            with closing(self._db()) as db, db:
                self._set_status(db, token, result['status'])
            # Native 'refused' означает ноль Store: план свободен, а заказы остаются занятыми журналом native.
            if leased and result['status'] in ('complete', 'refused'):
                guard.release(plan_store, token)
            return _label_providings(result)
        except BaseException as error:
            if leased and (not native_started or isinstance(error, ServiceError) and error.no_write):
                guard.release(plan_store, token)
            raise

    def status(self, token: str, actor: str, *, plan_store: Any = None) -> dict[str, Any]:
        result = _label_providings(self._status(token, actor, plan_store=plan_store))
        token = identifier(token)
        with closing(self._db()) as db:
            released = db.execute('SELECT 1 FROM plan_lease_releases WHERE operation_id=? LIMIT 1', (token,)).fetchone()
        # Запись журнала без снятого замка (сбой между ними, новый замок сверки) не значит, что план свободен.
        if released is None or plan_store is None or token in guard.held_operations(plan_store):
            return result
        return {**result, 'plan_released': True}

    def release_plan(self, token: str, actor: str, reason: str, *, plan_store: Any) -> dict[str, Any]:
        """Ручной выход после проверки ERP человеком: снимается только замок плана, заказы остаются занятыми."""
        token = identifier(token)
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500:
            raise ServiceError('reason', 400)
        current = self.status(token, actor, plan_store=plan_store)
        if current['status'] == 'running':
            raise ServiceError('operation_active')
        if current['status'] not in ('partial', 'unknown') and current.get('stale_lease') is not True:
            raise ServiceError('release_not_allowed')
        with closing(self._db()) as db, db:
            db.execute('INSERT INTO plan_lease_releases(operation_id,actor,reason) VALUES(?,?,?)', (token, actor, reason.strip()))
        guard.release(plan_store, token)
        return {**current, 'plan_released': True}

    def _status(self, token: str, actor: str, *, plan_store: Any = None) -> dict[str, Any]:
        row = self.get(token, actor)
        if row['status'] == 'ready':
            result = json.loads(row['native_preview'])
            age = guard.lease_age_seconds(plan_store, row['id']) if plan_store is not None else None
            # Сбой между замком и пометкой running: native не запускался, а ждать больше нечего.
            return {**result, 'stale_lease': True} if age is not None and age >= STALE_READY_LEASE_SECONDS else result
        with closing(self._db()) as db:
            refusal = db.execute('SELECT code FROM no_write_refusals WHERE operation_id=?', (row['id'],)).fetchone()
        if refusal is not None:
            if plan_store is not None:
                guard.release(plan_store, row['id'])
            return {'id': row['id'], 'database': self.database, 'status': 'refused', 'day': row['day'],
                    'steps': [], 'replay_dispatched': False, 'no_write_dispatched': True, 'error_code': refusal['code']}
        try:
            result = self.native.call({'action': 'status', 'id': identifier(token)})
            with closing(self._db()) as db, db:
                self._set_status(db, row['id'], result['status'])
            if plan_store is not None and result['status'] in ('complete', 'refused'):
                guard.release(plan_store, row['id'])
            return result
        except ServiceError as error:
            if error.code == 'busy':
                # Занят сам native: возможно, идёт именно эта запись, поэтому про её исход ничего не утверждаем.
                return {'id': row['id'], 'database': self.database, 'status': 'running', 'day': row['day'],
                        'steps': [], 'replay_dispatched': False, 'error_code': 'busy'}
            if error.code not in ('not_found', 'no_operation_expired'):
                raise
            # Решение принимает только native: под run lock и по своим часам он проверил, что записи не было
            # и уже не будет. Простой not_found ничего не доказывает: журнал мог быть удалён после записи.
            if error.code == 'no_operation_expired' and row['status'] in ('running', 'unknown'):
                with closing(self._db()) as db, db:
                    db.execute("UPDATE previews SET status='refused' WHERE id=?", (row['id'],))
                    db.execute('DELETE FROM order_claims WHERE operation_id=?', (row['id'],))
                    db.execute('INSERT OR REPLACE INTO no_write_refusals VALUES(?,?)', (row['id'], 'expired'))
                if plan_store is not None:
                    guard.release(plan_store, row['id'])
                return {'id': row['id'], 'database': self.database, 'status': 'refused', 'day': row['day'],
                        'steps': [], 'replay_dispatched': False, 'no_write_dispatched': True, 'error_code': 'expired'}
            # The web process may have stopped before launching native. Claims stay held.
            return {'id': identifier(token), 'database': self.database, 'status': 'refused' if row['status'] == 'refused' else 'unknown',
                    'day': row['day'], 'steps': [], 'replay_dispatched': False, 'error_code': 'unknown'}

    def recent(self, actor: str, day: str, *, plan_store: Any = None) -> list[dict[str, str]]:
        """Записи этого дня и записи пользователя, всё ещё держащие замок плана, какого бы дня они ни были."""
        held = guard.held_operations(plan_store) if plan_store is not None else []
        marks = ','.join('?' * len(held))
        with closing(self._db()) as db:
            rows = db.execute(
                f"SELECT id,status,native_preview FROM previews WHERE actor=? AND ((day=? AND status<>'ready') OR id IN ({marks})) "
                f"ORDER BY id IN ({marks}) DESC, rowid DESC LIMIT ?",
                (actor, day, *held, *held, 20 + len(held))).fetchall()
        result = []
        for row in rows:
            preview = json.loads(row['native_preview'])
            cars = ', '.join(sorted({load['car'] for load in preview['loads']}))
            numbers = [order.get('number', order['isn'][:8]) for load in preview['loads'] for order in load['orders']]
            result.append({'id': row['id'], 'status': row['status'],
                           'label': cars + ' · ' + ', '.join(numbers[:2]) + ('…' if len(numbers) > 2 else '')})
        return result

    def reconciliation_preview(self, token: str, actor: str) -> dict[str, Any]:
        record = self.get(token, actor)
        if record['status'] in {'ready', 'complete'}:
            raise ServiceError('changed')
        result = self.native.call({'action': 'reconcile-preview', 'id': record['id']})
        review_id = identifier(result['review_id'])
        if result.get('id') != record['id'] or result.get('can_resume') is not True:
            raise ServiceError('verification')
        with closing(self._db()) as db, db:
            db.execute('INSERT INTO reconciliation_reviews VALUES(?,?,?,?,?,NULL)',
                       (review_id, record['id'], actor, json.dumps(result), 'ready'))
        return result

    def reconcile(self, review_id: str, actor: str, reason: str, *, plan_store: Any) -> dict[str, Any]:
        review_id = identifier(review_id)
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500:
            raise ServiceError('reason', 400)
        if self.quarantine.exists():
            raise ServiceError('quarantined', 503)
        with closing(self._db()) as db:
            row = db.execute('SELECT * FROM reconciliation_reviews WHERE id=? AND actor=?', (review_id, actor)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        record = self.get(row['operation_id'], actor)
        if row['status'] != 'ready' or record['status'] == 'complete':
            return self.status(record['id'], actor, plan_store=plan_store)
        if not guard.acquire(plan_store, json.loads(record['plan']), record['id'], keep_existing=True):
            raise ServiceError('changed')
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE reconciliation_reviews SET status='running',reason=? WHERE id=? AND status='ready'",
                                 (reason.strip(), review_id)).rowcount
        if not changed:
            return self.status(record['id'], actor, plan_store=plan_store)
        try:
            result = self.native.call({'action': 'reconcile-execute', 'id': record['id'], 'review_id': review_id,
                                       'actor': actor, 'reason': reason.strip()})
        except BaseException:
            # Даже доказанный отказ этой попытки не снимает защиту предыдущих частичных документов.
            with closing(self._db()) as db, db:
                db.execute("UPDATE reconciliation_reviews SET status='unknown' WHERE id=?", (review_id,))
            raise
        with closing(self._db()) as db, db:
            db.execute('UPDATE reconciliation_reviews SET status=? WHERE id=?', (result['status'], review_id))
            self._set_status(db, record['id'], result['status'])
        if result['status'] == 'complete':
            guard.release(plan_store, record['id'])
        return _label_providings(result)


def _bridge_service(url: str, secret_file: str, journal: str) -> ChainService:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.path not in ('', '/') or parts.query or parts.fragment or parts.username:
        raise RuntimeError('ERP chain bridge URL must contain only scheme, host and port')
    try:
        address = None if parts.hostname == 'localhost' else ipaddress.ip_address(parts.hostname or '')
    except ValueError as error:
        raise RuntimeError('ERP chain bridge host must be a private or loopback IP address') from error
    if address is not None and not (address.is_private or address.is_loopback):
        raise RuntimeError('ERP chain bridge host must be a private or loopback IP address')
    secret = Path(secret_file).read_text(encoding='utf-8').strip()
    if not secret:
        raise RuntimeError('ERP chain bridge secret file is empty')
    path = Path(journal)
    return ChainService(path, RemoteNative(url, secret.encode('utf-8')), quarantine=path.parent / 'QUARANTINED.json')


def from_environment(connection_string: str) -> ChainService | None:
    root = os.environ.get('ROUTES_ERP_CHAIN_LOCAL_ROOT')
    bridge = [os.environ.get(key) for key in BRIDGE_ENV]
    if root and any(bridge):
        raise RuntimeError('ERP chain: set either the local root or the bridge settings, not both')
    if not root:
        if not all(bridge):
            return None
        try:
            return _bridge_service(*bridge)
        except (RuntimeError, OSError, ValueError) as error:
            # Ошибка настройки моста отключает только эту функцию, а не весь раздел.
            logger.error('ERP chain bridge disabled, invalid settings: %s', error)
            return None
    if os.name != 'nt' or Path(root).resolve() != ROOT.resolve():
        raise RuntimeError('ERP chain requires the exact isolated Windows test root')
    # Configuration guard before connecting: production read connections cannot enable this feature.
    import pyodbc
    parts = {k.strip().lower(): v.strip().strip('{}')
             for item in connection_string.split(';') if '=' in item for k, v in [item.split('=', 1)]}
    if parts.get('server', '').lower() != SERVER.lower() or parts.get('database') != DATABASE:
        raise RuntimeError('ERP chain requires the isolated clone read connection')
    with pyodbc.connect(connection_string, timeout=8, autocommit=True) as db:
        machine, actual, is_localdb = db.cursor().execute(
            "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')),DB_NAME(),CONVERT(int,SERVERPROPERTY('IsLocalDB'))"
        ).fetchone()
        if machine.lower() != os.environ['COMPUTERNAME'].lower() or actual != DATABASE or is_localdb != 1:
            raise RuntimeError('ERP chain target identity mismatch')
    return ChainService(ROOT / 'dispatch' / 'web-journal.sqlite3')
