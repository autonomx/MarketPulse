"""Web Push message encryption (RFC 8291, aes128gcm per RFC 8188) and VAPID authorization (RFC 8292), for TVP-0.5c.

Built on ``cryptography`` (already a dependency) rather than a web-push package. A push message is encrypted for one
browser subscription with its ``p256dh`` public key and ``auth`` secret; only that browser can read it. The VAPID key
pair identifies Omnix to the push services; browsers subscribe with its public key.
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
# Push services accept 4 KiB; the encryption header and tag take 103 bytes of it.
MAX_PLAINTEXT = RECORD_SIZE - 103


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt: bytes, info: bytes, length: int, ikm: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_push(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """``plaintext`` encrypted for one subscription: the aes128gcm body of a push request (one record)."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError(f"a push message holds at most {MAX_PLAINTEXT} bytes")
    receiver_point = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(receiver_point) != 65 or len(auth_secret) < 16:
        raise ValueError("invalid push subscription keys")
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_point)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    sender_point = _point(sender_key.public_key())
    shared = sender_key.exchange(ec.ECDH(), receiver)
    ikm = _hkdf(auth_secret, b"WebPush: info\x00" + receiver_point + sender_point, 32, shared)
    salt = salt or os.urandom(16)
    key = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    # One record: the content, then the last-record delimiter (no padding).
    ciphertext = AESGCM(key).encrypt(nonce, plaintext + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_point)]) + sender_point + ciphertext


def decrypt_push(body: bytes, receiver_key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The browser's side of ``encrypt_push`` (for tests and diagnostics)."""
    salt, key_length = body[:16], body[20]
    sender_point = body[21:21 + key_length]
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_point)
    shared = receiver_key.exchange(ec.ECDH(), sender)
    receiver_point = _point(receiver_key.public_key())
    ikm = _hkdf(b64url_decode(auth), b"WebPush: info\x00" + receiver_point + sender_point, 32, shared)
    key = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    record = AESGCM(key).decrypt(nonce, body[21 + key_length:], None)
    return record[: record.rindex(b"\x02")]


def new_vapid_key() -> str:
    """A new VAPID private key, as PEM text for the secret store."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii")


def load_vapid_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("the VAPID key must be a P-256 key")
    return key


def vapid_public_key(key: ec.EllipticCurvePrivateKey) -> str:
    """The application server key browsers subscribe with (base64url of the uncompressed point)."""
    return b64url(_point(key.public_key()))


def vapid_authorization(endpoint: str, key: ec.EllipticCurvePrivateKey, subject: str, *, now: float | None = None, ttl: int = 12 * 3600) -> str:
    """The ``Authorization`` header of a push request: a JWT (ES256) for the endpoint's origin, and the public key."""
    parts = urlsplit(endpoint)
    audience = f"{parts.scheme}://{parts.netloc}"
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps({"aud": audience, "exp": int((now or time.time()) + ttl), "sub": subject}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={vapid_public_key(key)}"


__all__ = [
    "MAX_PLAINTEXT",
    "b64url",
    "b64url_decode",
    "decrypt_push",
    "encrypt_push",
    "load_vapid_key",
    "new_vapid_key",
    "vapid_authorization",
    "vapid_public_key",
]
