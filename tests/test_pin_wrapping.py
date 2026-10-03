"""Миграция старого PBKDF2 в HMAC-обёртку без знания реального PIN."""
import pytest
import werkzeug.security as wz
from courier.security import check_pin, hash_outdated, pepper_from_env, wrap_pin_hash
from courier.store import Store


def test_post_hash_pepper_requires_key_and_correct_pin(monkeypatch):
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'isolated-pepper-key')
    pepper = pepper_from_env()
    old = wz.generate_password_hash('4321', method='pbkdf2:sha256:1000')
    wrapped = wrap_pin_hash(old, pepper)
    assert old.split('$')[-1] not in wrapped
    assert check_pin(wrapped, '4321', pepper)
    assert not check_pin(wrapped, '0000', pepper)
    assert not check_pin(wrapped, '4321', None)
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'different-isolated-pepper')
    assert not check_pin(wrapped, '4321', pepper_from_env())
    assert hash_outdated(wrapped)


def test_wrapped_salt_and_iterations_are_authenticated(monkeypatch):
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'isolated-pepper-key')
    pepper = pepper_from_env()
    wrapped = wrap_pin_hash(wz.generate_password_hash('4321', method='pbkdf2:sha256:1000'), pepper)
    assert not check_pin(wrapped.replace(':1000$', ':1001$'), '4321', pepper)
    method, salt, mac = wrapped.split('$')
    assert not check_pin(method + '$different-salt$' + mac, '4321', pepper)
    assert not check_pin('wrapped-pbkdf2:sha256:999999999$x$y', '4321', pepper)


def test_wrapped_store_fallback_and_upgrade_without_session_revocation(tmp_path, monkeypatch):
    monkeypatch.delenv('COURIER_PIN_PEPPER', raising=False)
    monkeypatch.delenv('COURIER_PIN_PEPPER_OLD', raising=False)
    monkeypatch.setattr(wz, 'DEFAULT_PBKDF2_ITERATIONS', 1000)
    old_store = Store(str(tmp_path / 'courier.db'))
    did = old_store.save_driver(None, 'Test driver', True, '4321', 'audit')
    row = old_store._read(lambda c: c.execute('SELECT pin_hash FROM drivers WHERE id=?', (did,)).fetchone())
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'isolated-pepper-key')
    pepper = pepper_from_env()
    old_store._transaction(lambda c: c.execute('UPDATE drivers SET pin_hash=?,pin_scheme=?,pin_tag=NULL WHERE id=?',
                                              (wrap_pin_hash(row[0], pepper), pepper.scheme, did)), 'test migration')
    store = Store(old_store.path)
    assert [d.id for d in store.match_pin('4321')] == [did]
    assert store.match_pin('0000') == []
    updated = store._read(lambda c: c.execute('SELECT pin_hash,pin_scheme FROM drivers WHERE id=?', (did,)).fetchone())
    assert not updated[0].startswith('wrapped-')
    assert updated[1] == pepper.scheme


def test_unsupported_hash_refused_without_guessing_pin(monkeypatch):
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'isolated-pepper-key')
    with pytest.raises(ValueError):
        wrap_pin_hash('unknown$format$digest', pepper_from_env())
