# -*- coding: utf-8 -*-
"""Фото и подписи терминала (POST /photos): файлы в courier_photos/ рядом с базой, запись — в photos.

Инварианты: только JPEG/PNG по сигнатуре (а не по имени или Content-Type), ≤ MAX_PHOTO_BYTES;
id фото — uuid (имя файла строится из него, путь пользователя не используется); повтор уже записанного
id — duplicate, файл не трогается. Запись атомарна: временный файл → os.replace → строка в базе.
Пределы терминала за день (store.PHOTOS_PER_DAY, PHOTO_BYTES_PER_DAY) — в транзакции записи строки
(PhotoLimit; файл тогда удаляется). Офис смотрит фото только через /api/courier/admin/photos/<uuid>.
"""
from __future__ import annotations

import hashlib
import os
import uuid

from . import clock
from .store import PhotoLimit, Store

MAX_PHOTO_BYTES = 2 * 1024 * 1024
PHOTO_KINDS = ('photo', 'signature')
_SIGNATURES = ((b'\xff\xd8\xff', 'jpg'), (b'\x89PNG\r\n\x1a\n', 'png'))


def image_ext(head: bytes) -> str | None:
    """Расширение по сигнатуре файла: JPEG → jpg, PNG → png, иное — None."""
    for magic, ext in _SIGNATURES:
        if head.startswith(magic):
            return ext
    return None


def save_photo(store: Store, photo_id: str, event_id: str, kind: str, data: bytes, terminal_id: int) -> bool:
    """Сохранить проверенное фото. True — новое, False — такое id уже было (файл не тронут)."""
    ext = image_ext(data)
    if ext is None or not data or len(data) > MAX_PHOTO_BYTES or kind not in PHOTO_KINDS:
        raise ValueError('фото не прошло проверку')
    photo_id = str(uuid.UUID(photo_id))   # канонический вид: имя файла — только из uuid
    if store.has_photo(photo_id):
        return False
    rel = os.path.join(clock.today().strftime('%Y-%m'), f'{photo_id}.{ext}')
    path = os.path.join(store.photos_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{uuid.uuid4().hex}.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    try:
        return store.save_photo(photo_id, event_id, kind, rel.replace(os.sep, '/'), len(data),
                                hashlib.sha256(data).hexdigest(), terminal_id)
    except PhotoLimit:   # предел дня терминала (проверен в транзакции записи) — файл не оставляем
        os.remove(path)
        raise
