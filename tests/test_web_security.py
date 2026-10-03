"""CSRF проверяется на фиктивном cookie-приложении, без SQL и рабочего состояния."""
from flask import Flask, jsonify, render_template_string, session
import pytest
from courier import API_PREFIX
from courier.web_security import init_web_security


# Реальный courier.API_PREFIX — со слэшем на конце; с ним вход терминала получал 403 csrf.
@pytest.fixture(params=[API_PREFIX, '/api/courier/v1'])
def client(request):
    app = Flask(__name__)
    app.secret_key = 'isolated-test-secret'
    app.config['TESTING'] = True
    init_web_security(app, request.param)
    @app.get('/form')
    def form():
        return render_template_string('{{ csrf_token() }}')
    @app.post('/settings')
    def settings():
        session['saved'] = True
        return jsonify(success=True)
    @app.post('/api/courier/v1/events')
    def terminal():
        return jsonify(success=True)
    @app.post('/api/courier/v1/login')
    def terminal_login():
        return jsonify(success=True)
    @app.post('/api/courier/v10/events')
    def lookalike():
        return jsonify(success=True)
    return app.test_client()


@pytest.mark.parametrize('content_type', ['application/json', 'text/plain', 'application/x-www-form-urlencoded'])
def test_missing_token_cannot_mutate(client, content_type):
    client.get('/form')
    assert client.post('/settings', data='{}', content_type=content_type).status_code == 403
    with client.session_transaction() as s:
        assert not s.get('saved')


def test_header_token_and_same_origin_work(client):
    token = client.get('/form').get_data(as_text=True)
    assert client.post('/settings', json={}, headers={'X-CSRF-Token': token, 'Origin': 'http://localhost'}).status_code == 200


@pytest.mark.parametrize('origin', ['http://attacker.invalid', 'null', 'http://[invalid', 'https://localhost'])
def test_foreign_or_malformed_origin_rejected_even_with_token(client, origin):
    token = client.get('/form').get_data(as_text=True)
    assert client.post('/settings', json={}, headers={'X-CSRF-Token': token, 'Origin': origin}).status_code == 403


def test_token_bound_to_cookie_session(client):
    token = client.get('/form').get_data(as_text=True)
    with client.session_transaction() as s:
        s.clear()
    assert client.post('/settings', data={'csrf_token': token}).status_code == 403


def test_html_form_and_bearer_api_exemption(client):
    token = client.get('/form').get_data(as_text=True)
    assert client.post('/settings', data={'csrf_token': token}).status_code == 200
    assert client.post('/api/courier/v1/events', json={}).status_code == 200
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}).status_code == 200


def test_exemption_stops_at_prefix_boundary(client):
    assert client.post('/api/courier/v10/events', json={}).status_code == 403


def test_security_headers(client):
    response = client.get('/form')
    assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert "frame-ancestors 'self'" in response.headers['Content-Security-Policy']
    assert response.headers['Cache-Control'] == 'no-store'
