"""RemoteNative против потокового fake-моста: подпись, отказы, транспорт, настройка; ERP не подключается."""
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import logging
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import erp_chain_service as ec
from route_optimizer import erp_chain_guard as guard
from route_optimizer.store import Store
from test_route_erp_chain_service import web, day, client, preview, DAY  # noqa: F401

SECRET = b'fake-bridge-shared-key'
IDENT = {'database': 'SalesManagement', 'environment': 'production'}
ORDER = '00000000-0000-0000-0000-000000000001'
BRIDGE_CLOCK = time.time  # часы моста; тест может сдвинуть только часы клиента


def sign(text, secret=SECRET):
    return hmac.new(secret, text.encode('utf-8'), hashlib.sha256).hexdigest()


def plan(revision=1):
    return {'day': '2026-11-09', 'revision': revision,
            'loads': [{'trip': '1', 'warehouse': '000', 'courier': 'B001', 'car': 'CAR1', 'orders': [ORDER]}]}


def native_reply(request):
    """Ответ native по умолчанию; тест подменяет его через bridge.reply."""
    action = request['action']
    if action == 'options':
        return 200, {'success': True, 'result': {**IDENT, 'warehouses': [], 'writes_enabled': True, 'max_orders': 1}}
    if action == 'preview':
        return 200, {'success': True, 'result': {**IDENT, 'id': str(uuid4()), 'day': request['plan']['day'], 'status': 'preview',
                                                 'order_count': 1, 'loads': [{'car': 'CAR1', 'orders': [{'isn': ORDER, 'number': '7'}]}]}}
    return 200, {'success': True, 'result': {**IDENT, 'id': request['id'], 'status': 'complete', 'steps': []}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self, status, payload, nonce, headers=()):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        signature = sign(f'v1-resp\n{nonce}\n{status}\n{hashlib.sha256(body).hexdigest()}')
        if self.server.tamper is not None:
            status, body, signature = self.server.tamper(status, body, signature)
        self.send_response(status)
        if signature is not None:
            self.send_header('X-Araqich-Signature', signature)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        stamp, nonce = self.headers.get('X-Araqich-Timestamp'), self.headers.get('X-Araqich-Nonce')
        canonical = f'v1\n{self.command}\n{self.path}\n{stamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}'
        fresh = stamp is not None and abs(BRIDGE_CLOCK() - int(stamp)) <= 120
        # Как настоящий мост: всё, что отвергнуто до принятия nonce, подписано по пустому nonce.
        if self.server.pre_auth is not None:
            self._answer(self.server.pre_auth[0], {'success': False, 'code': self.server.pre_auth[1], 'no_write_dispatched': True}, '')
            return
        if not fresh:
            self._answer(401, {'success': False, 'code': 'stale_timestamp', 'no_write_dispatched': True}, '')
            return
        if not hmac.compare_digest(self.headers.get('X-Araqich-Signature') or '', sign(canonical)):
            self._answer(401, {'success': False, 'code': 'bad_signature', 'no_write_dispatched': True}, '')
            return
        if nonce in self.server.seen:
            self._answer(401, {'success': False, 'code': 'unknown'}, '')
            return
        self.server.seen.add(nonce)
        self.server.calls.append({'method': self.command, 'path': self.path, 'body': body})
        if self.server.delay:
            time.sleep(self.server.delay)
        if self.path == '/v1/health':
            self._answer(200, {'success': True, 'status': 'ok', 'native_exe_present': True}, nonce)
            return
        outcome = self.server.reply(json.loads(body.decode('utf-8')))
        self._answer(outcome[0], outcome[1], nonce, outcome[2] if len(outcome) > 2 else ())

    do_POST = do_GET = _run


@pytest.fixture
def bridge():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.calls, server.seen, server.reply, server.tamper, server.delay, server.pre_auth = [], set(), native_reply, None, 0, None
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()


def remote_client(bridge, **options):
    return ec.RemoteNative(f'http://127.0.0.1:{bridge.server_address[1]}', SECRET, **options)


def test_signed_request_and_reply_round_trip(bridge):
    result = remote_client(bridge).call({'action': 'options'})
    assert result['writes_enabled'] is True
    assert bridge.calls == [{'method': 'POST', 'path': '/v1/dispatch', 'body': b'{"action": "options"}'}]


def test_armenian_text_is_sent_as_utf8(bridge):
    reason = 'Փաստաթղթերի ստուգումը կատարված է'
    remote_client(bridge).call({'action': 'status', 'id': ORDER, 'reason': reason})
    assert json.loads(bridge.calls[0]['body'].decode('utf-8'))['reason'] == reason
    assert reason.encode('utf-8') in bridge.calls[0]['body']


def test_health_is_signed(bridge):
    assert remote_client(bridge).health()['status'] == 'ok'
    assert bridge.calls[0]['method'] == 'GET' and bridge.calls[0]['path'] == '/v1/health'


def test_each_call_uses_a_new_nonce(bridge):
    remote = remote_client(bridge)
    remote.call({'action': 'options'})
    remote.call({'action': 'options'})
    assert len(bridge.seen) == 2


def test_signed_refusal_honours_no_write(bridge):
    bridge.reply = lambda request: (200, {'success': False, 'code': 'writes_disabled', 'no_write_dispatched': True})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'writes_disabled' and caught.value.no_write and caught.value.http_status == 409
    assert 'գրանցումը դեռ միացված չէ' in str(caught.value)


def test_signed_refusal_without_flag_does_not_claim_no_write(bridge):
    bridge.reply = lambda request: (200, {'success': False, 'code': 'busy'})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'busy' and not caught.value.no_write


def test_signed_bridge_failure_is_unknown(bridge):
    bridge.reply = lambda request: (502, {'success': False, 'code': 'unknown'})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'unknown' and caught.value.http_status == 503 and not caught.value.no_write


@pytest.mark.parametrize('tamper', [
    lambda status, body, signature: (status, body + b' ', signature),
    lambda status, body, signature: (status + 1, body, signature),
    lambda status, body, signature: (status, body, None),
    lambda status, body, signature: (status, body, '0' * 64),
    lambda status, body, signature: (status, body, 'գրանցում'.encode('utf-8').decode('latin-1')),
    lambda status, body, signature: (status, body, sign('v1-resp\nother-nonce\n200\n' + hashlib.sha256(body).hexdigest())),
], ids=['body', 'status', 'absent', 'wrong', 'non_ascii', 'other_nonce'])
def test_invalid_reply_signature_is_unknown(bridge, tamper):
    bridge.reply = lambda request: (200, {'success': False, 'code': 'busy', 'no_write_dispatched': True})
    bridge.tamper = tamper
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'unknown' and caught.value.http_status == 503 and not caught.value.no_write
    assert len(bridge.calls) == 1


@pytest.mark.parametrize('field,value', [('database', 'AraqichTest_20261105'), ('environment', 'isolated-test'), ('environment', None)])
def test_wrong_target_in_reply_is_unknown(bridge, field, value):
    result = {**IDENT, 'status': 'complete', 'steps': []}
    if value is None:
        del result[field]
    else:
        result[field] = value
    bridge.reply = lambda request: (200, {'success': True, 'result': result})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'status', 'id': ORDER})
    assert caught.value.code == 'unknown'


def test_expected_target_is_configurable(bridge):
    identity = {'database': 'AraqichTest_20261105', 'environment': 'isolated-test'}
    bridge.reply = lambda request: (200, {'success': True, 'result': {**identity, 'status': 'complete'}})
    remote = remote_client(bridge, **identity)
    assert remote.call({'action': 'status', 'id': ORDER})['status'] == 'complete'


@pytest.mark.parametrize('payload', [b'not json', b'[1,2]', b'"text"', '{"success":true,"result":{}}'.encode('utf-8'), b''])
def test_malformed_signed_reply_is_unknown(bridge, payload):
    bridge.reply = lambda request: (200, payload)
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'status', 'id': ORDER})
    assert caught.value.code == 'unknown'


def test_connection_refused_is_unknown():
    with pytest.raises(ec.ServiceError) as caught:
        ec.RemoteNative('http://127.0.0.1:9', SECRET, read_timeout=2).call({'action': 'options'})
    assert caught.value.code == 'unknown' and caught.value.http_status == 503


def test_read_timeout_is_unknown_and_not_retried(bridge):
    bridge.delay = 1
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge, read_timeout=0.2).call({'action': 'options'})
    assert caught.value.code == 'unknown' and len(bridge.calls) == 1


def test_write_actions_use_the_longer_timeout(bridge):
    bridge.delay = 0.5
    remote = remote_client(bridge, read_timeout=0.1, write_timeout=10)
    assert remote.call({'action': 'execute', 'id': ORDER})['status'] == 'complete'
    with pytest.raises(ec.ServiceError):
        remote.call({'action': 'status', 'id': ORDER})
    assert remote.read_timeout == 0.1 and remote.write_timeout == 10


def test_default_timeouts_match_the_bridge_limits():
    remote = ec.RemoteNative('http://127.0.0.1:9', SECRET)
    assert 120 < remote.read_timeout <= 170 and 900 < remote.write_timeout <= 960


def test_redirect_is_not_followed(bridge):
    bridge.reply = lambda request: (302, {'success': False}, [('Location', '/v1/other')])
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'status', 'id': ORDER})
    assert caught.value.code == 'unknown' and len(bridge.calls) == 1


def test_environment_proxy_is_ignored(bridge, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:9')
    assert remote_client(bridge).call({'action': 'options'})['warehouses'] == []


def service_for(bridge, tmp_path):
    return ec.ChainService(tmp_path / 'journal.sqlite3', remote_client(bridge), quarantine=tmp_path / 'QUARANTINED.json')


def claims(service):
    with sqlite3.connect(service.journal) as db:
        return db.execute('SELECT database_id,order_isn FROM order_claims').fetchall()


def test_claims_and_status_use_the_bridge_database(bridge, tmp_path):
    service = service_for(bridge, tmp_path)
    token = service.preview('qa', plan())['id']
    assert service.execute(token, 'qa', plan())['status'] == 'complete'
    assert service.database == 'SalesManagement'
    assert claims(service) == [('SalesManagement', ORDER)]


def test_writes_disabled_releases_claim_and_records_refusal(bridge, tmp_path):
    service = service_for(bridge, tmp_path)
    token = service.preview('qa', plan())['id']
    original = bridge.reply
    bridge.reply = lambda request: ((200, {'success': False, 'code': 'writes_disabled', 'no_write_dispatched': True})
                                    if request['action'] == 'execute' else original(request))
    with pytest.raises(ec.ServiceError) as caught:
        service.execute(token, 'qa', plan())
    assert caught.value.code == 'writes_disabled' and caught.value.no_write
    assert claims(service) == []
    refused = service.status(token, 'qa')
    assert refused['status'] == 'refused' and refused['no_write_dispatched'] and refused['database'] == 'SalesManagement'
    assert sum(json.loads(c['body'])['action'] == 'execute' for c in bridge.calls) == 1


def test_lost_reply_keeps_claim_and_requires_readback(bridge, tmp_path):
    service = service_for(bridge, tmp_path)
    token = service.preview('qa', plan())['id']
    bridge.tamper = lambda status, body, signature: (status, body, None)
    with pytest.raises(ec.ServiceError) as caught:
        service.execute(token, 'qa', plan())
    assert caught.value.code == 'unknown' and not caught.value.no_write
    bridge.tamper = None
    assert claims(service) == [('SalesManagement', ORDER)]
    assert service.get(token, 'qa')['status'] == 'unknown'
    assert service.execute(token, 'qa', plan())['status'] == 'complete'
    assert sum(json.loads(c['body'])['action'] == 'execute' for c in bridge.calls) == 1


def test_default_service_keeps_local_database(tmp_path):
    assert ec.LocalNative.database == ec.DATABASE
    assert ec.ChainService(tmp_path / 'j.sqlite3').database == ec.DATABASE


@pytest.fixture
def bridge_env(monkeypatch, tmp_path):
    monkeypatch.delenv('ROUTES_ERP_CHAIN_LOCAL_ROOT', raising=False)
    secret = tmp_path / 'bridge.secret'
    secret.write_text('  shared-key\n', encoding='utf-8')
    values = {'ROUTES_ERP_CHAIN_BRIDGE_URL': 'http://192.168.1.50:8765',
              'ROUTES_ERP_CHAIN_BRIDGE_SECRET_FILE': str(secret),
              'ROUTES_ERP_CHAIN_JOURNAL': str(tmp_path / 'data' / 'bridge-journal.sqlite3')}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_all_three_settings_enable_bridge_mode(bridge_env):
    service = ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;')
    assert isinstance(service.native, ec.RemoteNative)
    assert service.native.secret == b'shared-key' and service.database == 'SalesManagement'
    assert service.native.environment == 'production'
    assert service.quarantine.name == 'QUARANTINED.json'
    assert service.quarantine.parent == Path(bridge_env['ROUTES_ERP_CHAIN_JOURNAL']).parent


@pytest.mark.parametrize('missing', ec.BRIDGE_ENV)
def test_partial_settings_leave_the_chain_disabled(bridge_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    assert ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;') is None


def test_no_settings_leave_the_chain_disabled(monkeypatch):
    for key in ('ROUTES_ERP_CHAIN_LOCAL_ROOT', *ec.BRIDGE_ENV):
        monkeypatch.delenv(key, raising=False)
    assert ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;') is None


@pytest.mark.parametrize('url', ['http://8.8.8.8:8765', 'http://bridge.example.com:8765', 'http://192.168.1.50:8765/prefix',
                                 'http://user:pw@192.168.1.50:8765', 'ftp://192.168.1.50:8765', 'http://192.168.1.50:8765/?x=1'])
def test_bad_bridge_url_disables_the_chain_without_stopping_the_site(bridge_env, monkeypatch, caplog, url):
    monkeypatch.setenv('ROUTES_ERP_CHAIN_BRIDGE_URL', url)
    with caplog.at_level(logging.ERROR, logger=ec.logger.name):
        assert ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;') is None
    assert 'ERP chain bridge disabled' in caplog.text


@pytest.mark.parametrize('url', ['http://127.0.0.1:8765', 'http://localhost:8765', 'http://[::1]:8765', 'http://10.0.0.5:8765/'])
def test_loopback_and_private_hosts_are_accepted(bridge_env, monkeypatch, url):
    monkeypatch.setenv('ROUTES_ERP_CHAIN_BRIDGE_URL', url)
    assert isinstance(ec.from_environment('').native, ec.RemoteNative)


def test_local_and_bridge_together_are_an_error(bridge_env, monkeypatch):
    monkeypatch.setenv('ROUTES_ERP_CHAIN_LOCAL_ROOT', str(ec.ROOT))
    with pytest.raises(RuntimeError, match='not both'):
        ec.from_environment('SERVER=192.168.1.4;DATABASE=SalesManagement;')


def test_empty_or_missing_secret_file_disables_the_chain(bridge_env, monkeypatch, tmp_path):
    Path(bridge_env['ROUTES_ERP_CHAIN_BRIDGE_SECRET_FILE']).write_text('\n', encoding='utf-8')
    assert ec.from_environment('') is None
    monkeypatch.setenv('ROUTES_ERP_CHAIN_BRIDGE_SECRET_FILE', str(tmp_path / 'absent.secret'))
    assert ec.from_environment('') is None


def test_fallback_text_does_not_claim_the_operation_was_not_performed():
    text = str(ec.ServiceError('some_new_code'))
    assert 'չի կատարվել' not in text and 'հաստատված չէ' in text


def test_too_many_text_has_no_fixed_number():
    assert not any(ch.isdigit() for ch in ec.ERRORS['too_many'])


@pytest.mark.parametrize('status,code', [(403, 'forbidden'), (413, 'body_too_large'), (401, 'stale_timestamp'), (401, 'bad_signature')])
def test_pre_auth_refusal_cannot_be_read_as_no_write(bridge, status, code):
    bridge.pre_auth = (status, code)
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'unknown' and caught.value.http_status == 503 and not caught.value.no_write
    assert bridge.calls == []


def test_stale_client_clock_becomes_unknown(bridge, monkeypatch):
    real = time.time
    monkeypatch.setattr(ec.time, 'time', lambda: real() - 600)
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    monkeypatch.undo()
    assert caught.value.code == 'unknown' and not caught.value.no_write and bridge.calls == []


def test_replayed_nonce_becomes_unknown(bridge, monkeypatch):
    monkeypatch.setattr(ec.secrets, 'token_hex', lambda n: 'a' * (2 * n))
    remote = remote_client(bridge)
    remote.call({'action': 'options'})
    with pytest.raises(ec.ServiceError) as caught:
        remote.call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == 'unknown' and not caught.value.no_write and len(bridge.calls) == 1


@pytest.mark.parametrize('code', ['stopping', 'unknown_endpoint', 'unknown_action', 'spawn_failed', 'busy', 'writes_disabled', 'too_many'])
def test_correctly_signed_refusal_after_auth_is_honoured_for_writes(bridge, code):
    bridge.reply = lambda request: (200, {'success': False, 'code': code, 'no_write_dispatched': True})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': 'execute', 'id': ORDER})
    assert caught.value.code == code and caught.value.no_write


@pytest.mark.parametrize('action', ['options', 'preview', 'status', 'reconcile-preview'])
def test_busy_on_a_read_action_does_not_claim_no_write(bridge, action):
    bridge.reply = lambda request: (200, {'success': False, 'code': 'busy', 'no_write_dispatched': True})
    with pytest.raises(ec.ServiceError) as caught:
        remote_client(bridge).call({'action': action, 'id': ORDER, 'plan': plan()})
    assert caught.value.code == 'busy' and not caught.value.no_write


@pytest.mark.parametrize('action,expected', [('status', False), ('execute', True), ('reconcile-execute', True)])
def test_local_native_honours_no_write_only_for_writes(monkeypatch, action, expected):
    monkeypatch.setattr(ec.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0,
        stdout=json.dumps({'success': False, 'code': 'busy', 'no_write_dispatched': True}), stderr=''))
    with pytest.raises(ec.ServiceError) as caught:
        ec.LocalNative().call({'action': action, 'id': ORDER})
    assert caught.value.no_write is expected


@pytest.fixture
def store(tmp_path):
    routes = Store(tmp_path / 'routes.sqlite3')
    assert routes.save_dispatch(plan()['day'], {}, 'qa') == 1
    return routes


def leases(store):
    return store._read(lambda db: db.execute('SELECT COUNT(*) FROM erp_chain_plan_lease').fetchone()[0])


def started(bridge, tmp_path, store, execute=None):
    """Предпросмотр и execute с потерянным ответом: замок и заказы заняты."""
    service = service_for(bridge, tmp_path)
    token = service.preview('qa', plan())['id']
    if execute is None:
        bridge.tamper = lambda status, body, signature: (status, body, None)
        with pytest.raises(ec.ServiceError):
            service.execute(token, 'qa', plan(), plan_store=store)
        bridge.tamper = None
    else:
        original = bridge.reply
        bridge.reply = lambda request: execute if request['action'] == 'execute' else original(request)
        service.execute(token, 'qa', plan(), plan_store=store)
    return service, token


NATIVE_NOT_FOUND = (200, {'success': False, 'code': 'not_found', 'no_write_dispatched': True})


def test_status_busy_is_neutral_and_keeps_everything(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: (200, {'success': False, 'code': 'busy', 'no_write_dispatched': True})
    result = service.status(token, 'qa', plan_store=store)
    assert result['status'] == 'running' and 'no_write_dispatched' not in result and result['steps'] == []
    assert leases(store) == 1 and claims(service) == [('SalesManagement', ORDER)]
    assert service.get(token, 'qa')['status'] == 'unknown'


NATIVE_EXPIRED = (200, {'success': False, 'code': 'no_operation_expired', 'no_write_dispatched': True})


@pytest.mark.parametrize('row_status', ['running', 'unknown'])
def test_native_proof_releases_plan_and_claims(bridge, tmp_path, store, row_status):
    service, token = started(bridge, tmp_path, store)
    with sqlite3.connect(service.journal) as db:
        db.execute('UPDATE previews SET status=? WHERE id=?', (row_status, token))
    bridge.reply = lambda request: NATIVE_EXPIRED
    result = service.status(token, 'qa', plan_store=store)
    assert result['status'] == 'refused' and result['no_write_dispatched'] and result['error_code'] == 'expired'
    assert leases(store) == 0 and claims(service) == [] and service.get(token, 'qa')['status'] == 'refused'
    before = len(bridge.calls)
    assert service.status(token, 'qa', plan_store=store)['status'] == 'refused'
    assert len(bridge.calls) == before


def test_unknown_row_with_lost_journal_stays_locked_whatever_the_age(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    with sqlite3.connect(service.journal) as db:
        row = json.loads(db.execute('SELECT native_preview FROM previews WHERE id=?', (token,)).fetchone()[0])
        row['created_utc'] = '2020-01-01T00:00:00.0000000Z'
        db.execute('UPDATE previews SET native_preview=? WHERE id=?', (json.dumps(row), token))
    bridge.reply = lambda request: NATIVE_NOT_FOUND
    result = service.status(token, 'qa', plan_store=store)
    assert result['status'] == 'unknown' and 'no_write_dispatched' not in result
    assert leases(store) == 1 and claims(service) == [('SalesManagement', ORDER)]


@pytest.mark.parametrize('status', ['partial', 'complete'])
def test_native_proof_is_ignored_for_a_recorded_write(bridge, tmp_path, store, status):
    done = (200, {'success': True, 'result': {**IDENT, 'status': status, 'steps': []}})
    service, token = started(bridge, tmp_path, store, execute=done)
    with sqlite3.connect(service.journal) as db:
        db.execute('UPDATE previews SET status=? WHERE id=?', (status, token))
    bridge.reply = lambda request: NATIVE_EXPIRED
    result = service.status(token, 'qa', plan_store=store)
    assert result['status'] == 'unknown' and 'no_write_dispatched' not in result
    assert claims(service) == [('SalesManagement', ORDER)]


def test_native_refused_releases_plan_but_keeps_claims_on_status(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: (200, {'success': True, 'result': {**IDENT, 'status': 'refused', 'steps': []}})
    assert service.status(token, 'qa', plan_store=store)['status'] == 'refused'
    assert leases(store) == 0 and claims(service) == [('SalesManagement', ORDER)]


def test_native_refused_releases_plan_but_keeps_claims_on_execute(bridge, tmp_path, store):
    done = (200, {'success': True, 'result': {**IDENT, 'status': 'refused', 'steps': []}})
    service, _ = started(bridge, tmp_path, store, execute=done)
    assert leases(store) == 0 and claims(service) == [('SalesManagement', ORDER)]


def test_reconcile_over_the_order_limit_keeps_claims_and_plan_lock(bridge, tmp_path, store):
    partial = (200, {'success': True, 'result': {**IDENT, 'status': 'partial', 'steps': []}})
    service, token = started(bridge, tmp_path, store, execute=partial)
    assert leases(store) == 1
    review_id = str(uuid4())

    def reply(request):
        if request['action'] == 'reconcile-preview':
            return 200, {'success': True, 'result': {**IDENT, 'id': token, 'review_id': review_id, 'can_resume': True, 'missing_count': 2, 'missing_sales': []}}
        return 200, {'success': False, 'code': 'too_many', 'no_write_dispatched': True}
    bridge.reply = reply
    service.reconciliation_preview(token, 'qa')
    with pytest.raises(ec.ServiceError) as caught:
        service.reconcile(review_id, 'qa', 'Փաստաթղթերի ստուգումը կատարված է', plan_store=store)
    assert caught.value.code == 'too_many' and caught.value.no_write
    assert leases(store) == 1 and claims(service) == [('SalesManagement', ORDER)]


def test_release_plan_needs_a_reason(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    for reason in (None, '', 'short', 'x' * 501):
        with pytest.raises(ec.ServiceError) as caught:
            service.release_plan(token, 'qa', reason, plan_store=store)
        assert caught.value.code == 'reason'
    assert leases(store) == 1


def audit_rows(service):
    with sqlite3.connect(service.journal) as db:
        return db.execute('SELECT operation_id,actor,reason,released_at FROM plan_lease_releases').fetchall()


REASON = 'ՀԾ-ում փաստաթղթերը ձեռքով ստուգված են'


def test_release_plan_frees_only_the_plan_and_is_audited(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: NATIVE_NOT_FOUND
    result = service.release_plan(token, 'qa', '  ' + REASON + ' ', plan_store=store)
    assert result['status'] == 'unknown' and result['plan_released']
    assert leases(store) == 0 and claims(service) == [('SalesManagement', ORDER)]
    rows = audit_rows(service)
    assert len(rows) == 1 and rows[0][:3] == (token, 'qa', REASON) and rows[0][3]
    assert service.status(token, 'qa', plan_store=store)['plan_released']


@pytest.mark.parametrize('native_status', ['partial', 'unknown'])
def test_release_plan_accepts_native_stuck_states(bridge, tmp_path, store, native_status):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: (200, {'success': True, 'result': {**IDENT, 'id': token, 'status': native_status, 'steps': []}})
    assert service.release_plan(token, 'qa', REASON, plan_store=store)['plan_released']
    assert leases(store) == 0 and claims(service) == [('SalesManagement', ORDER)]


def test_release_plan_refused_while_native_is_busy(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: (200, {'success': False, 'code': 'busy', 'no_write_dispatched': True})
    with pytest.raises(ec.ServiceError) as caught:
        service.release_plan(token, 'qa', REASON, plan_store=store)
    assert caught.value.code == 'operation_active' and leases(store) == 1 and audit_rows(service) == []


def test_release_plan_refused_when_native_cannot_be_asked(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.tamper = lambda status, body, signature: (status, body, None)
    with pytest.raises(ec.ServiceError) as caught:
        service.release_plan(token, 'qa', REASON, plan_store=store)
    assert caught.value.code == 'unknown' and leases(store) == 1 and audit_rows(service) == []


@pytest.mark.parametrize('native_status', ['complete', 'refused'])
def test_release_plan_refused_for_final_outcomes(bridge, tmp_path, store, native_status):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: (200, {'success': True, 'result': {**IDENT, 'id': token, 'status': native_status, 'steps': []}})
    with pytest.raises(ec.ServiceError) as caught:
        service.release_plan(token, 'qa', REASON, plan_store=store)
    assert caught.value.code == 'release_not_allowed' and audit_rows(service) == []


def test_release_plan_is_bound_to_the_original_actor(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    with pytest.raises(ec.ServiceError) as caught:
        service.release_plan(token, 'someone-else', REASON, plan_store=store)
    assert caught.value.http_status == 404 and leases(store) == 1


@pytest.fixture
def stuck(web):
    token = preview(web).get_json()['result']['id']
    native = web.state.erp_chain.native
    original = native.call

    def call(request):
        result = original(request)
        if request['action'] == 'execute':
            result['status'] = 'partial'
        return result
    native.call = call
    response = web.post('/api/routes/dispatch/erp-chain/execute', json={'id': token, 'csrf': web.chain_csrf})
    assert response.get_json()['result']['status'] == 'partial'
    web.stuck_token = token
    return web


def release_request(web, headers=None, **changes):
    return web.post('/api/routes/dispatch/erp-chain/release-plan',
                    json={'id': web.stuck_token, 'reason': REASON, 'csrf': web.chain_csrf, **changes}, headers=headers or {})


def web_leases(web):
    return web.state.store._read(lambda db: db.execute('SELECT COUNT(*) FROM erp_chain_plan_lease').fetchone()[0])


def test_superadmin_release_route_frees_the_plan(stuck):
    response = release_request(stuck)
    assert response.status_code == 200 and response.get_json()['result']['plan_released']
    assert web_leases(stuck) == 0
    assert sum(x['action'] == 'execute' for x in stuck.state.erp_chain.native.calls) == 1


def test_release_route_refuses_without_superadmin_approval(stuck):
    assert release_request(stuck, headers={'X-Superadmin-Password': ''}).status_code == 403
    assert web_leases(stuck) == 1 and audit_rows(stuck.state.erp_chain) == []


def test_release_route_refuses_bad_csrf_and_non_admin(stuck):
    assert release_request(stuck, csrf='wrong').status_code == 403
    stuck.role['value'] = 'garage'
    assert release_request(stuck).status_code == 403
    assert web_leases(stuck) == 1 and audit_rows(stuck.state.erp_chain) == []


def test_release_route_refuses_while_busy(stuck):
    native = stuck.state.erp_chain.native
    original = native.call

    def call(request):
        if request['action'] == 'status':
            raise ec.ServiceError('busy')
        return original(request)
    native.call = call
    response = release_request(stuck)
    assert response.status_code == 409 and response.get_json()['code'] == 'operation_active'
    assert web_leases(stuck) == 1 and audit_rows(stuck.state.erp_chain) == []


def test_busy_text_is_neutral_unless_no_write_is_proven():
    assert 'չի կատարվել' not in str(ec.ServiceError('busy'))
    assert 'չի կատարվել' in str(ec.ServiceError('busy', no_write=True))


def test_trucks_guard_applies_only_to_today_or_later(store):
    store._transaction(lambda db: db.execute("INSERT INTO trucks(car_code,updated_at) VALUES('CAR1','x')"), 'test')
    assert guard.acquire(store, plan(), str(uuid4()))
    store._transaction(lambda db: db.execute("UPDATE erp_chain_plan_lease SET day='2001-01-01'"), 'test')
    store._transaction(lambda db: db.execute("UPDATE trucks SET active=0 WHERE car_code='CAR1'"), 'test')
    store._transaction(lambda db: db.execute("UPDATE erp_chain_plan_lease SET day='2999-01-01'"), 'test')
    with pytest.raises(guard.PlanLocked):
        store._transaction(lambda db: db.execute("UPDATE trucks SET active=1 WHERE car_code='CAR1'"), 'test')


def test_acquire_replaces_old_trigger_bodies(store):
    first = str(uuid4())
    assert guard.acquire(store, plan(), first)  # триггеры появляются с первым замком
    guard.release(store, first)
    old = "CREATE TRIGGER erp_chain_guard_trucks_update AFTER UPDATE ON trucks BEGIN SELECT 1; END"
    def swap(db):
        db.execute('DROP TRIGGER erp_chain_guard_trucks_update')
        db.execute(old)
    store._transaction(swap, 'test')
    assert guard.acquire(store, plan(), str(uuid4()))
    sql = store._read(lambda db: db.execute("SELECT sql FROM sqlite_master WHERE name='erp_chain_guard_trucks_update'").fetchone()[0])
    assert "date('now','+4 hours')" in sql


def test_plan_released_needs_audit_row_and_no_lease(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: NATIVE_NOT_FOUND
    with sqlite3.connect(service.journal) as db:  # аудит записан, а замок не снят
        db.execute('INSERT INTO plan_lease_releases(operation_id,actor,reason) VALUES(?,?,?)', (token, 'qa', REASON))
    assert 'plan_released' not in service.status(token, 'qa', plan_store=store)
    assert 'plan_released' not in service.status(token, 'qa')
    guard.release(store, token)
    assert service.status(token, 'qa', plan_store=store)['plan_released']


def test_plan_released_is_not_reported_without_audit_row(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    bridge.reply = lambda request: NATIVE_NOT_FOUND
    guard.release(store, token)
    assert 'plan_released' not in service.status(token, 'qa', plan_store=store)


def test_recent_keeps_runs_holding_a_lease_of_any_day(bridge, tmp_path, store):
    service, token = started(bridge, tmp_path, store)
    assert [row['id'] for row in service.recent('qa', '2026-11-20', plan_store=store)] == [token]
    assert service.recent('other', '2026-11-20', plan_store=store) == []
    guard.release(store, token)
    assert service.recent('qa', '2026-11-20', plan_store=store) == []


def test_recent_shows_leased_run_that_never_reached_native(bridge, tmp_path, store):
    service = service_for(bridge, tmp_path)
    token = service.preview('qa', plan())['id']
    assert service.recent('qa', plan()['day'], plan_store=store) == []
    assert guard.acquire(store, plan(), token)
    assert [row['id'] for row in service.recent('qa', plan()['day'], plan_store=store)] == [token]


def test_options_lists_stuck_run_of_another_day_before_this_day_is_sent(stuck):
    stuck.state.store._transaction(lambda db: db.execute("UPDATE erp_chain_plan_lease SET day='2000-01-01'"), 'test')
    with sqlite3.connect(stuck.state.erp_chain.journal) as db:
        db.execute("UPDATE previews SET day='2000-01-01'")
    tomorrow = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    body = stuck.get('/api/routes/dispatch/erp-chain/options?date=' + tomorrow).get_json()
    assert body['trips'] == [] and [row['id'] for row in body['recent']] == [stuck.stuck_token]
    assert release_request(stuck).status_code == 200
    assert stuck.get('/api/routes/dispatch/erp-chain/options?date=' + tomorrow).status_code == 400
