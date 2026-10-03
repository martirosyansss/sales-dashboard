"""Защита офисных форм: CSRF для cookie-сессии и безопасные браузерные заголовки."""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, session

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
CSRF_SESSION_KEY = '_office_csrf'


def init_web_security(app: Flask, terminal_api_prefix: str) -> None:
    """API терминалов использует Bearer, офис — cookie; CSRF применяется только к офису."""
    if app.extensions.get('office_web_security'):
        return
    app.extensions['office_web_security'] = True

    def csrf_token() -> str:
        token = session.get(CSRF_SESSION_KEY)
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            session[CSRF_SESSION_KEY] = token
        return token

    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def protect_office_mutations() -> Response | tuple[Response, int] | None:
        if request.method in SAFE_METHODS or request.path.startswith(terminal_api_prefix + '/'):
            return None
        origin = request.headers.get('Origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return csrf_error()
            # За доверенным ProxyFix scheme/host должны соответствовать браузерному origin.
            if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != request.host.lower() \
                    or parsed.scheme != request.scheme or parsed.path not in ('', '/'):
                return csrf_error()
        expected = session.get(CSRF_SESSION_KEY)
        supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        if not isinstance(expected, str) or not isinstance(supplied, str) or len(supplied) > 128 \
                or not hmac.compare_digest(expected.encode('utf-8'), supplied.encode('utf-8')):
            return csrf_error()
        return None

    def csrf_error() -> tuple[Response, int]:
        response = jsonify({'success': False, 'error': 'csrf',
                            'message': 'Сессия формы устарела. Обновите страницу и повторите действие.'})
        response.headers['Cache-Control'] = 'no-store'
        return response, 403

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        # Эти директивы совместимы с текущими inline-скриптами и Alpine.
        # Строгий script-src вводится отдельно после устранения inline/eval, чтобы не ломать офис.
        response.headers.setdefault('Content-Security-Policy',
                                    "base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self'")
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response
