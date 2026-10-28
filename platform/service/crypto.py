"""Code digests, reversible code storage and login tokens.

Two representations of a six digit code exist on purpose:

``digest``  one way, used to verify a login attempt. Never leaves the server.
``cipher``  reversible with the server secret, so the admin console can keep
             showing the current code to hand over to a contestant.

The server secret comes from the environment or from a key file that sits
outside the SQLite database, so a database copy alone cannot reveal codes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_secret_cache: bytes | None = None


def set_secret(secret: bytes) -> None:
    global _secret_cache
    _secret_cache = secret


def get_secret() -> bytes:
    if _secret_cache is None:
        from .config import settings

        set_secret(settings().server_secret())
    assert _secret_cache is not None
    return _secret_cache


def _keystream(nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(get_secret(), nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def code_digest(code: str) -> str:
    """One-way digest of a six digit code, safe to store and to index."""
    return hmac.new(get_secret(), b'code:' + code.encode('utf-8'), hashlib.sha256).hexdigest()


def encrypt_code(code: str) -> str:
    """Reversible storage of the current code so the admin console can show it."""
    nonce = secrets.token_bytes(16)
    plain = code.encode('utf-8')
    cipher = bytes(a ^ b for a, b in zip(plain, _keystream(nonce, len(plain))))
    tag = hmac.new(get_secret(), b'tag:' + nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.b64encode(nonce + tag + cipher).decode('ascii')


def decrypt_code(blob: str) -> str:
    raw = base64.b64decode(blob.encode('ascii'))
    nonce, tag, cipher = raw[:16], raw[16:32], raw[32:]
    expected = hmac.new(get_secret(), b'tag:' + nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError('code ciphertext failed integrity check')
    plain = bytes(a ^ b for a, b in zip(cipher, _keystream(nonce, len(cipher))))
    return plain.decode('utf-8')


def token_digest(token: str) -> str:
    return hmac.new(get_secret(), b'token:' + token.encode('utf-8'), hashlib.sha256).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_six_digit_code() -> str:
    """A six digit string; leading zeros are meaningful and must be preserved."""
    return f'{secrets.randbelow(1_000_000):06d}'


def payload_digest(value: object) -> str:
    """Stable digest of a submitted payload, used to detect conflicting retries."""
    import json

    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()
