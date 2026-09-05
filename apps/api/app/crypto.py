"""필드 암호화 — AES-GCM (ARCHITECTURE §6, feature-portfolio §10).

대상: 수량·단가·금액(정수). 종목 코드는 평문(조인·검색). DB에는 base64(nonce|ct) 텍스트로 저장.
키: ENCRYPTION_KEY 를 SHA-256 으로 32바이트 유도. 집계는 앱 레벨 복호 후 수행.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


def _key() -> bytes:
    return hashlib.sha256(get_settings().encryption_key.encode()).digest()


def encrypt_int(value: int) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, str(int(value)).encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_int(token: str) -> int:
    raw = base64.b64decode(token)
    pt = AESGCM(_key()).decrypt(raw[:12], raw[12:], None)
    return int(pt.decode())


class EncryptedBigInt(TypeDecorator):
    """정수 필드 암호화 컬럼 — 파이썬에서는 int, DB에는 암호문 텍스트."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_int(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_int(value)


def encrypt_text(value: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, value.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_text(token: str) -> str:
    raw = base64.b64decode(token)
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode()


class EncryptedText(TypeDecorator):
    """문자열 필드 암호화 컬럼 — 증권사 앱키·시크릿·계좌번호 저장용 (2026-09-05)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_text(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_text(value)
