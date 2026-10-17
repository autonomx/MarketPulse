"""S3-compatible BlobStore (AWS S3, MinIO, ...) over httpx with SigV4 (WP-5.8).

No SDK dependency: requests are signed with AWS Signature Version 4, path-style
addressing (``{endpoint}/{bucket}/{key}``), which every S3-compatible server
accepts. Integrity matches LocalBlobStore: content is hashed with SHA-256 and
the digest is stored as object metadata and verified on read.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
from typing import IO, Any, BinaryIO
from urllib.parse import quote, urlsplit

import httpx

from .blob_store import BlobIntegrityError, InvalidBlobKey, normalize_blob_key

_ALGORITHM = "AWS4-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_CHECKSUM_META = "x-amz-meta-omnix-sha256"
_CHUNK = 1024 * 1024
_SPOOL_BYTES = 8 * 1024 * 1024


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str = "s3") -> bytes:
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{_uri_encode(name, keep_slash=False)}={_uri_encode(value, keep_slash=False)}"
        for name, value in sorted(params.items())
    )


@dataclass(frozen=True, slots=True)
class S3Settings:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"
    prefix: str = ""
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("S3 endpoint must be an http(s) origin without a path")
        if not self.bucket or "/" in self.bucket:
            raise ValueError("S3 bucket name is required and must not contain '/'")
        if not self.access_key_id or not self.secret_access_key:
            raise ValueError("S3 credentials are required")


class SigV4Signer:
    def __init__(self, settings: S3Settings) -> None:
        self.settings = settings
        self.host = urlsplit(settings.endpoint).netloc

    def _scope(self, date: str) -> str:
        return f"{date}/{self.settings.region}/s3/aws4_request"

    def sign_headers(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        payload_sha256: str,
        now: datetime | None = None,
    ) -> dict[str, str]:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        signed = {name.lower(): " ".join(str(value).split()) for name, value in (headers or {}).items()}
        signed.update({"host": self.host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256})
        names = sorted(signed)
        canonical_request = "\n".join(
            [
                method.upper(),
                _uri_encode(path, keep_slash=True),
                canonical_query(query or {}),
                "".join(f"{name}:{signed[name]}\n" for name in names),
                ";".join(names),
                payload_sha256,
            ]
        )
        string_to_sign = "\n".join(
            [_ALGORITHM, amz_date, self._scope(date), hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
        )
        signature = hmac.new(
            signing_key(self.settings.secret_access_key, date, self.settings.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            f"{_ALGORITHM} Credential={self.settings.access_key_id}/{self._scope(date)}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        )
        return {**{name: value for name, value in signed.items() if name != "host"}, "authorization": authorization}

    def presign(self, method: str, path: str, *, expires_seconds: int, now: datetime | None = None) -> str:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        query = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self.settings.access_key_id}/{self._scope(date)}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(int(expires_seconds)),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_request = "\n".join(
            [
                method.upper(),
                _uri_encode(path, keep_slash=True),
                canonical_query(query),
                f"host:{self.host}\n",
                "host",
                "UNSIGNED-PAYLOAD",
            ]
        )
        string_to_sign = "\n".join(
            [_ALGORITHM, amz_date, self._scope(date), hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
        )
        signature = hmac.new(
            signing_key(self.settings.secret_access_key, date, self.settings.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return (
            f"{self.settings.endpoint.rstrip('/')}{_uri_encode(path, keep_slash=True)}"
            f"?{canonical_query(query)}&X-Amz-Signature={signature}"
        )


class S3BlobStore:
    """BlobStore over an S3-compatible bucket; records carry no local path."""

    provider = "s3"

    def __init__(self, settings: S3Settings, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.signer = SigV4Signer(settings)
        self._client = client or httpx.Client(timeout=settings.timeout_seconds, follow_redirects=False)

    # Addressing -------------------------------------------------------------

    def _object_path(self, storage_key: str) -> str:
        key = normalize_blob_key(storage_key)
        prefix = self.settings.prefix.strip("/")
        full = f"{prefix}/{key}" if prefix else key
        return f"/{self.settings.bucket}/{full}"

    def _url(self, path: str) -> str:
        return f"{self.settings.endpoint.rstrip('/')}{_uri_encode(path, keep_slash=True)}"

    def _request(
        self,
        method: str,
        storage_key: str,
        *,
        content: Any = None,
        payload_sha256: str = _EMPTY_SHA256,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        path = self._object_path(storage_key)
        signed = self.signer.sign_headers(method, path, headers=headers, payload_sha256=payload_sha256)
        request = self._client.build_request(method, self._url(path), headers=signed, content=content)
        return self._client.send(request, stream=stream)

    # Metadata ---------------------------------------------------------------

    def _head(self, storage_key: str) -> dict[str, str] | None:
        response = self._request("HEAD", storage_key)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return {name.lower(): value for name, value in response.headers.items()}

    def exists(self, storage_key: str) -> bool:
        return self._head(storage_key) is not None

    def _record(self, storage_key: str, checksum: str, size: int, *, created: bool) -> dict[str, Any]:
        return {
            "storage_provider": self.provider,
            "storage_key": normalize_blob_key(storage_key),
            "byte_size": int(size),
            "checksum_sha256": checksum,
            "created": created,
        }

    # Writes -----------------------------------------------------------------

    def _put_spooled(self, storage_key: str, handle: BinaryIO, checksum: str, size: int, content_type: str | None) -> dict[str, Any]:
        existing = self._head(storage_key)
        if existing is not None and existing.get(_CHECKSUM_META) == checksum:
            return self._record(storage_key, checksum, size, created=False)
        headers = {
            _CHECKSUM_META: checksum,
            "content-length": str(size),
            "content-type": content_type or "application/octet-stream",
        }
        handle.seek(0)

        def body() -> Iterator[bytes]:
            while chunk := handle.read(_CHUNK):
                yield chunk

        response = self._request("PUT", storage_key, content=body(), payload_sha256=checksum, headers=headers)
        response.raise_for_status()
        return self._record(storage_key, checksum, size, created=True)

    def put_stream(self, storage_key: str, stream: BinaryIO, *, content_type: str | None = None, max_bytes: int | None = None) -> dict[str, Any]:
        normalize_blob_key(storage_key)
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES) as spool:
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise ValueError("blob exceeds the permitted size")
                digest.update(chunk)
                spool.write(chunk)
            return self._put_spooled(storage_key, spool, digest.hexdigest(), size, content_type)  # type: ignore[arg-type]

    def put_bytes(self, storage_key: str, content: bytes) -> dict[str, Any]:
        if not isinstance(content, bytes):
            raise TypeError("BlobStore content must be bytes")
        import io

        return self.put_stream(storage_key, io.BytesIO(content))

    def put_file(self, storage_key: str, source: str | Path) -> dict[str, Any]:
        with Path(source).open("rb") as reader:
            return self.put_stream(storage_key, reader)

    def delete(self, storage_key: str) -> bool:
        if self._head(storage_key) is None:
            return False
        response = self._request("DELETE", storage_key)
        response.raise_for_status()
        return True

    # Reads ------------------------------------------------------------------

    def open(self, storage_key: str) -> BinaryIO:
        """Download into a spooled temporary file (bounded memory), rewound."""
        spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)
        try:
            self._download_into(storage_key, spool, expected_checksum=None)
            spool.seek(0)
            return spool  # type: ignore[return-value]
        except Exception:
            spool.close()
            raise

    def _download_into(self, storage_key: str, writer: IO[bytes], *, expected_checksum: str | None) -> str:
        response = self._request("GET", storage_key, stream=True)
        try:
            if response.status_code == 404:
                raise FileNotFoundError(storage_key)
            response.raise_for_status()
            digest = hashlib.sha256()
            for chunk in response.iter_bytes(_CHUNK):
                digest.update(chunk)
                writer.write(chunk)
        finally:
            response.close()
        actual = digest.hexdigest()
        if expected_checksum is not None and actual != expected_checksum:
            raise BlobIntegrityError(
                f"blob checksum mismatch for {storage_key}: expected {expected_checksum}, got {actual}"
            )
        return actual

    def read_bytes(self, storage_key: str, *, expected_checksum: str | None = None) -> bytes:
        import io

        buffer = io.BytesIO()
        self._download_into(storage_key, buffer, expected_checksum=expected_checksum)
        return buffer.getvalue()

    def open_verified(self, storage_key: str, *, expected_checksum: str) -> BinaryIO:
        spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)
        try:
            self._download_into(storage_key, spool, expected_checksum=expected_checksum)
            spool.seek(0)
            return spool  # type: ignore[return-value]
        except Exception:
            spool.close()
            raise

    def copy_verified_to(self, storage_key: str, destination: str | Path, *, expected_checksum: str) -> None:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp", delete=False
            ) as writer:
                temporary = writer.name
                self._download_into(storage_key, writer, expected_checksum=expected_checksum)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def stage_verified_to(self, storage_key: str, destination: str | Path, *, expected_checksum: str) -> None:
        self.copy_verified_to(storage_key, destination, expected_checksum=expected_checksum)

    def scratch_dir(self) -> Path | None:
        """No local blob volume: stage in the system temporary directory."""
        return None

    def presign_get(self, storage_key: str, ttl_seconds: int = 300) -> str:
        if not 1 <= int(ttl_seconds) <= 604800:
            raise ValueError("presigned URL lifetime must be between 1 second and 7 days")
        return self.signer.presign("GET", self._object_path(storage_key), expires_seconds=int(ttl_seconds))

    def close(self) -> None:
        self._client.close()


__all__ = ["InvalidBlobKey", "S3BlobStore", "S3Settings", "SigV4Signer", "canonical_query", "signing_key"]
