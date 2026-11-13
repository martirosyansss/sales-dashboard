"""Подтверждение Superadmin для изменения закреплений и допуска машин."""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

from flask import current_app, g, has_request_context, request, session
from werkzeug.security import check_password_hash


class VehicleApprovalError(Exception):
    def __init__(self, message: str, status: int = 403, *, invalid: bool = False):
        super().__init__(message)
        self.status = status
        self.invalid = invalid


def require_vehicle_approval() -> None:
    # Служебные задачи вне HTTP не используют пароль офисной формы.
    if not has_request_context() or g.get('_vehicle_superadmin_approved'):
        return
    password_hash = current_app.config.get('ROUTES_SUPERADMIN_PASSWORD_HASH') or os.environ.get(
        'ROUTES_SUPERADMIN_PASSWORD_HASH', '')
    if not password_hash:
        raise VehicleApprovalError('Superadmin-ի հաստատումը կարգավորված չէ։ Դիմեք ադմինիստրատորին։', 503)
    supplied = request.headers.get('X-Superadmin-Password', '')
    if not supplied:
        raise VehicleApprovalError('Փոփոխությունը պահպանելու համար մուտքագրեք Superadmin-ի գաղտնաբառը։')
    limiter = current_app.extensions.setdefault('vehicle_approval_attempts', (threading.Lock(), OrderedDict()))
    lock, attempts = limiter
    key = (str(session.get('username') or '')[:128], (request.remote_addr or '')[:64])
    now = time.monotonic()
    with lock:
        while attempts and next(iter(attempts.values()))[0] <= now - 300:
            attempts.popitem(last=False)
        started, failures = attempts.get(key, (now, 0))
        if failures >= 5:
            raise VehicleApprovalError('Շատ սխալ փորձեր։ Փորձեք կրկին 5 րոպե անց։', 429)
        valid = False
        if len(supplied) <= 128:
            try:
                valid = check_password_hash(password_hash, supplied)
            except (ValueError, TypeError):
                raise VehicleApprovalError('Superadmin-ի հաստատումը կարգավորված չէ։ Դիմեք ադմինիստրատորին։', 503) from None
        if not valid:
            attempts[key] = (started, failures + 1)
            # Ограничиваем число ключей; срок блокировки отсчитывается от первой ошибки.
            ordered = sorted(attempts.items(), key=lambda item: item[1][0])
            attempts.clear()
            attempts.update(ordered)
            while len(attempts) > 1024:
                attempts.popitem(last=False)
            raise VehicleApprovalError('Superadmin-ի գաղտնաբառը սխալ է։ Փորձեք կրկին։', invalid=True)
        attempts.pop(key, None)
    g._vehicle_superadmin_approved = True
