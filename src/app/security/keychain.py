"""Protected secrets on macOS: AES-GCM with a key held in the user's login Keychain (MarketPulse S-8).

Windows protects Omnix's credential files with DPAPI (provider_secret_store). On macOS the same files are encrypted
with a random 256-bit key that lives in the login Keychain as a generic password (service ``OMNIX_KEYCHAIN_SERVICE``,
default "Omnix"; account "secret-file-key"), so only this user's session can read them, as with DPAPI. The Keychain
is reached through the Security framework with ctypes: the key never appears on a command line.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import os
import secrets
import sys
from typing import Protocol

from app.config.env import env_str

PREFIX = b"OMNIX_KEYCHAIN_AESGCM_V1\n"
KEY_ACCOUNT = "secret-file-key"
_NONCE_BYTES = 12


class KeychainUnavailable(RuntimeError):
    pass


class KeychainBackend(Protocol):
    def find(self, service: str, account: str) -> bytes | None: ...

    def add(self, service: str, account: str, secret: bytes) -> None: ...


class SecurityFrameworkKeychain:
    """The login Keychain through Security.framework's generic-password calls."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise KeychainUnavailable("the Keychain exists only on macOS")
        path = ctypes.util.find_library("Security")
        if not path:
            raise KeychainUnavailable("Security.framework not found")
        self._security = ctypes.cdll.LoadLibrary(path)
        self._security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self._security.SecKeychainItemFreeContent.restype = ctypes.c_int32

    def find(self, service: str, account: str) -> bytes | None:
        service_bytes, account_bytes = service.encode(), account.encode()
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        status = self._security.SecKeychainFindGenericPassword(
            None, ctypes.c_uint32(len(service_bytes)), service_bytes, ctypes.c_uint32(len(account_bytes)), account_bytes,
            ctypes.byref(length), ctypes.byref(data), None,
        )
        if status == -25300:  # errSecItemNotFound
            return None
        if status != 0:
            raise KeychainUnavailable(f"Keychain read failed (OSStatus {status})")
        try:
            return ctypes.string_at(data, length.value)
        finally:
            self._security.SecKeychainItemFreeContent(None, data)

    def add(self, service: str, account: str, secret: bytes) -> None:
        service_bytes, account_bytes = service.encode(), account.encode()
        status = self._security.SecKeychainAddGenericPassword(
            None, ctypes.c_uint32(len(service_bytes)), service_bytes, ctypes.c_uint32(len(account_bytes)), account_bytes,
            ctypes.c_uint32(len(secret)), secret, None,
        )
        if status != 0:
            raise KeychainUnavailable(f"Keychain write failed (OSStatus {status})")


_backend: KeychainBackend | None = None


def install_keychain_backend(backend: KeychainBackend | None) -> None:
    """Replace the Keychain (tests); ``None`` returns to the real one."""
    global _backend
    _backend = backend


def keychain() -> KeychainBackend:
    global _backend
    if _backend is None:
        _backend = SecurityFrameworkKeychain()
    return _backend


def keychain_available() -> bool:
    if _backend is not None:
        return True
    if sys.platform != "darwin" or os.environ.get("OMNIX_KEYCHAIN_DISABLED") == "1":
        return False
    try:
        keychain()
    except KeychainUnavailable:
        return False
    return True


def _service() -> str:
    return (env_str("OMNIX_KEYCHAIN_SERVICE", "Omnix") or "Omnix").strip() or "Omnix"


def file_key() -> bytes:
    """The 256-bit file key, created in the Keychain on first use."""
    backend = keychain()
    stored = backend.find(_service(), KEY_ACCOUNT)
    if stored is not None:
        key = base64.b64decode(stored)
        if len(key) != 32:
            raise KeychainUnavailable("the Keychain's file key is malformed")
        return key
    key = secrets.token_bytes(32)
    backend.add(_service(), KEY_ACCOUNT, base64.b64encode(key))
    return key


def protect(value: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(_NONCE_BYTES)
    return PREFIX + nonce + AESGCM(file_key()).encrypt(nonce, value, PREFIX)


def unprotect(value: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not value.startswith(PREFIX):
        raise ValueError("not a Keychain-protected value")
    body = value[len(PREFIX):]
    nonce, ciphertext = body[:_NONCE_BYTES], body[_NONCE_BYTES:]
    return AESGCM(file_key()).decrypt(nonce, ciphertext, PREFIX)


__all__ = [
    "KeychainBackend", "KeychainUnavailable", "SecurityFrameworkKeychain", "file_key", "install_keychain_backend",
    "keychain", "keychain_available", "protect", "unprotect",
]
