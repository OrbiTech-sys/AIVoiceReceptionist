"""Password hashing, session tokens, secret encryption, phone normalisation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import phonenumbers
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_ph = PasswordHasher()  # Argon2id with library defaults


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# A pre-computed hash so login timing does not reveal whether an e-mail exists.
DUMMY_HASH = _ph.hash("not-a-real-password-" + secrets.token_hex(8))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _fernet() -> Fernet:
    key = hashlib.sha256(("vr-secrets:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("secret could not be decrypted (wrong VR_SECRET_KEY?)")


def normalize_phone(raw: str | None, default_region: str = "US") -> str | None:
    """Return an E.164 phone number, or None if the input is not a valid number."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        num = phonenumbers.parse(raw, None if raw.startswith("+") else default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def format_phone(e164: str | None) -> str:
    if not e164:
        return ""
    try:
        return phonenumbers.format_number(phonenumbers.parse(e164, None), phonenumbers.PhoneNumberFormat.NATIONAL)
    except phonenumbers.NumberParseException:
        return e164
