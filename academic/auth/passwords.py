"""Verify existing password hashes; this module never creates credentials.

Accepted formats:
* ASP.NET Identity V2 (marker 0, PBKDF2-SHA1, 1,000 rounds) and V3
  (marker 1, PBKDF2-SHA1/SHA256/SHA512, network-order metadata).
* bcrypt $2a$/$2b$/$2y$, costs 4–14 and passwords up to 72 UTF-8 bytes.
* Argon2 PHC strings (argon2id/argon2i/argon2d, version 19), at most
  128 MiB memory, 8 iterations and 8 lanes.
* Django pbkdf2_sha256 / pbkdf2_sha1.
* Werkzeug pbkdf2:sha256 / pbkdf2:sha512 with explicit iterations.

PBKDF2 accepts at most 2,000,000 rounds. Password/hash sizes and hash
parameters are bounded before calling a KDF. At most two verifications
run concurrently in a process. Unsupported or malformed hashes fail closed;
plaintext, unsalted hashes and unsupported legacy formats are rejected.

Format references:
https://github.com/dotnet/aspnetcore/blob/main/src/Identity/Extensions.Core/src/PasswordHasher.cs
https://github.com/django/django/blob/main/django/contrib/auth/hashers.py
"""

import base64
import binascii
import hashlib
import hmac
import re
import struct
from threading import BoundedSemaphore

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error


MAX_PBKDF2_ITERATIONS = 2_000_000
MAX_PASSWORD_BYTES = 4096
MAX_HASH_CHARS = 4096
_KDF_SLOTS = BoundedSemaphore(2)
_ARGON2 = PasswordHasher()
_BCRYPT = re.compile(r"\$2[aby]\$(\d{2})\$[./A-Za-z0-9]{53}\Z")
_ARGON = re.compile(
    r"\$argon2(?:id|i|d)\$v=19\$m=(\d+),t=(\d+),p=(\d+)"
    r"\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)\Z"
)


def _decode(value: str, *, unpadded: bool = False) -> bytes:
    if unpadded:
        value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=True)


def _pbkdf2(password: bytes, salt: bytes, expected: bytes, rounds: int, digest: str) -> bool:
    if not 1 <= rounds <= MAX_PBKDF2_ITERATIONS:
        return False
    if not 1 <= len(salt) <= 1024 or not 16 <= len(expected) <= 128:
        return False
    actual = hashlib.pbkdf2_hmac(digest, password, salt, rounds, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def _identity(password: bytes, encoded: str) -> bool:
    raw = _decode(encoded)
    if len(raw) == 49 and raw[0] == 0:
        return _pbkdf2(password, raw[1:17], raw[17:], 1000, "sha1")
    if len(raw) < 45 or raw[0] != 1:
        return False
    prf, rounds, salt_length = struct.unpack(">III", raw[1:13])
    if prf not in (0, 1, 2) or not 16 <= salt_length <= 1024:
        return False
    digest = {0: "sha1", 1: "sha256", 2: "sha512"}[prf]
    return _pbkdf2(password, raw[13:13 + salt_length], raw[13 + salt_length:], rounds, digest)


def _django(password: bytes, encoded: str) -> bool:
    algorithm, count, salt, result = encoded.split("$")
    digest = {"pbkdf2_sha256": "sha256", "pbkdf2_sha1": "sha1"}[algorithm]
    expected = _decode(result)
    if len(expected) != hashlib.new(digest).digest_size:
        return False
    return _pbkdf2(password, salt.encode("utf-8"), expected, int(count), digest)


def _werkzeug(password: bytes, encoded: str) -> bool:
    method, salt, result = encoded.split("$")
    algorithm, digest, count = method.split(":")
    if algorithm != "pbkdf2" or digest not in ("sha256", "sha512"):
        return False
    expected = bytes.fromhex(result)
    if len(expected) != hashlib.new(digest).digest_size:
        return False
    return _pbkdf2(password, salt.encode("utf-8"), expected, int(count), digest)


def _verify(password: bytes, encoded: str) -> bool:
    if encoded.startswith("$2"):
        match = _BCRYPT.fullmatch(encoded)
        if not match or not 4 <= int(match.group(1)) <= 14 or len(password) > 72:
            return False
        return bcrypt.checkpw(password, encoded.encode("ascii"))
    if encoded.startswith("$argon2"):
        match = _ARGON.fullmatch(encoded)
        if not match:
            return False
        memory, rounds, lanes = map(int, match.group(1, 2, 3))
        if not 1 <= rounds <= 8 or not 1 <= lanes <= 8 or not 8 * lanes <= memory <= 131072:
            return False
        salt, key = (_decode(part, unpadded=True) for part in match.group(4, 5))
        if not 8 <= len(salt) <= 1024 or not 16 <= len(key) <= 128:
            return False
        return _ARGON2.verify(encoded, password)
    if encoded.startswith(("pbkdf2_sha256$", "pbkdf2_sha1$")):
        return _django(password, encoded)
    if encoded.startswith("pbkdf2:"):
        return _werkzeug(password, encoded)
    return _identity(password, encoded)


def verify_password(password: str, encoded: str) -> bool:
    """Return False for a mismatch, malformed/unsupported hash or exceeded limits."""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    if not password or len(password) > MAX_PASSWORD_BYTES or not encoded or len(encoded) > MAX_HASH_CHARS:
        return False
    try:
        raw_password = password.encode("utf-8")
        if len(raw_password) > MAX_PASSWORD_BYTES:
            return False
        if not _KDF_SLOTS.acquire(blocking=False):
            return False
        try:
            return bool(_verify(raw_password, encoded))
        finally:
            _KDF_SLOTS.release()
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, Argon2Error, OverflowError):
        return False
