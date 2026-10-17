from __future__ import annotations
from app.config.env import env_str

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, BinaryIO

from app.runtime.paths import resources_data_root


class InvalidBlobKey(ValueError):
    pass


class BlobIntegrityError(RuntimeError):
    pass


def normalize_blob_key(storage_key: str) -> str:
    """Validate a relative ``a/b/c`` key shared by every BlobStore backend."""
    normalized = str(storage_key).strip().replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        raise InvalidBlobKey("storage key must be a non-empty relative path")
    if any(piece in {"", ".", ".."} for piece in normalized.split("/")):
        raise InvalidBlobKey("storage key contains an unsafe path segment")
    return normalized


def default_blob_root() -> Path:
    override = (env_str("OMNIX_BLOB_ROOT") or "").strip()
    return Path(override) if override else resources_data_root() / "blobs"


class LocalBlobStore:
    """Atomic filesystem BlobStore for local and offline Omnix deployments."""

    provider = "local-filesystem"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = (Path(root) if root is not None else default_blob_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, storage_key: str, content: bytes) -> dict[str, Any]:
        if not isinstance(content, bytes):
            raise TypeError("BlobStore content must be bytes")
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256(content).hexdigest()
        if path.is_file():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() == checksum:
                return self._record(storage_key, path, checksum, len(content), created=False)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return self._record(storage_key, path, checksum, len(content), created=True)

    def read_bytes(self, storage_key: str, *, expected_checksum: str | None = None) -> bytes:
        path = self._path(storage_key)
        content = path.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if expected_checksum is not None and actual != expected_checksum:
            raise BlobIntegrityError(
                f"blob checksum mismatch for {storage_key}: expected {expected_checksum}, got {actual}"
            )
        return content

    def open_verified(self, storage_key: str, *, expected_checksum: str) -> BinaryIO:
        """Return a checked, rewound file handle for bounded-memory downloads."""
        handle = self._path(storage_key).open("rb")
        try:
            digest = hashlib.sha256()
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != expected_checksum:
                raise BlobIntegrityError(f"blob checksum mismatch for {storage_key}")
            handle.seek(0)
            return handle
        except Exception:
            handle.close()
            raise

    def copy_verified_to(
        self, storage_key: str, destination: str | Path, *, expected_checksum: str,
    ) -> None:
        """Copy a blob to a local staging file without loading it into memory."""
        source = self._path(storage_key)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with source.open("rb") as reader, tempfile.NamedTemporaryFile(
                mode="wb", dir=str(target.parent), prefix=f".{target.name}.",
                suffix=".tmp", delete=False,
            ) as writer:
                temporary = writer.name
                digest = hashlib.sha256()
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if digest.hexdigest() != expected_checksum:
                raise BlobIntegrityError(f"blob checksum mismatch for {storage_key}")
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def stage_verified_to(
        self, storage_key: str, destination: str | Path, *, expected_checksum: str,
    ) -> None:
        """Stage an immutable local blob with a hard link when the volume permits."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(self._path(storage_key), target)
        except OSError:
            self.copy_verified_to(storage_key, target, expected_checksum=expected_checksum)
            return
        try:
            with target.open("rb") as reader:
                digest = hashlib.sha256()
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != expected_checksum:
                raise BlobIntegrityError(f"blob checksum mismatch for {storage_key}")
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def put_file(self, storage_key: str, source: str | Path) -> dict[str, Any]:
        """Store a generated file atomically with bounded memory usage."""
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with Path(source).open("rb") as reader, tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), prefix=f".{path.name}.",
                suffix=".tmp", delete=False,
            ) as writer:
                temporary = writer.name
                digest = hashlib.sha256()
                size = 0
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            checksum = digest.hexdigest()
            if path.is_file():
                old = hashlib.sha256()
                with path.open("rb") as existing:
                    while chunk := existing.read(1024 * 1024):
                        old.update(chunk)
                if old.hexdigest() == checksum:
                    return self._record(storage_key, path, checksum, size, created=False)
            os.replace(temporary, path)
            temporary = None
            return self._record(storage_key, path, checksum, size, created=True)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def delete(self, storage_key: str) -> bool:
        path = self._path(storage_key)
        if not path.exists():
            return False
        path.unlink()
        self._remove_empty_parents(path.parent)
        return True

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def put_stream(
        self,
        storage_key: str,
        stream: BinaryIO,
        *,
        content_type: str | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Store a stream atomically with bounded memory and an optional size cap."""
        del content_type
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as writer:
                temporary = writer.name
                digest = hashlib.sha256()
                size = 0
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("blob exceeds the permitted size")
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, path)
            temporary = None
            return self._record(storage_key, path, digest.hexdigest(), size, created=True)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def open(self, storage_key: str) -> BinaryIO:
        """Open a blob for streaming reads."""
        return self._path(storage_key).open("rb")

    def presign_get(self, storage_key: str, ttl_seconds: int = 300) -> str | None:
        """Local blobs have no directly reachable URL; serve them via the gateway."""
        del storage_key, ttl_seconds
        return None

    def scratch_dir(self) -> Path | None:
        """Staging directory on the blob volume, so staging can hard-link."""
        return self.root

    def local_path(self, storage_key: str) -> Path:
        """Filesystem path, for tools that need one; S3 callers stage a copy."""
        return self._path(storage_key)

    def _path(self, storage_key: str) -> Path:
        pieces = normalize_blob_key(storage_key).split("/")
        candidate = self.root.joinpath(*pieces).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise InvalidBlobKey("storage key escapes BlobStore root") from exc
        return candidate

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self.root:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    def _record(
        self,
        storage_key: str,
        path: Path,
        checksum: str,
        byte_size: int,
        *,
        created: bool,
    ) -> dict[str, Any]:
        return {
            "storage_provider": self.provider,
            "storage_key": str(storage_key).replace("\\", "/"),
            "byte_size": int(byte_size),
            "checksum_sha256": checksum,
            "path": str(path),
            "created": created,
        }


_DEFAULT_STORE_LOCK = threading.Lock()
_DEFAULT_STORE: Any = None


def blob_backend() -> str:
    backend = (env_str("OMNIX_BLOB_BACKEND", "local") or "local").strip().lower()
    if backend not in {"local", "s3"}:
        raise ValueError("OMNIX_BLOB_BACKEND must be 'local' or 's3'")
    return backend


def create_blob_store() -> Any:
    """Build the configured BlobStore (WP-5.8); local filesystem by default."""
    if blob_backend() == "local":
        return LocalBlobStore()
    from app.config.env import env_int

    from .s3_blob_store import S3BlobStore, S3Settings

    return S3BlobStore(
        S3Settings(
            endpoint=(env_str("OMNIX_S3_ENDPOINT", "") or "").strip(),
            bucket=(env_str("OMNIX_S3_BUCKET", "") or "").strip(),
            access_key_id=(env_str("OMNIX_S3_ACCESS_KEY_ID", "") or "").strip(),
            secret_access_key=(env_str("OMNIX_S3_SECRET_ACCESS_KEY", "") or "").strip(),
            region=(env_str("OMNIX_S3_REGION", "us-east-1") or "us-east-1").strip(),
            prefix=(env_str("OMNIX_S3_PREFIX", "") or "").strip(),
            timeout_seconds=float(env_int("OMNIX_S3_TIMEOUT_SECONDS", 60, minimum=1, maximum=3600)),
        )
    )


def default_blob_store() -> Any:
    """The process's shared BlobStore; one HTTP connection pool for S3."""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = create_blob_store()
        return _DEFAULT_STORE


def reset_default_blob_store_for_tests() -> None:
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        _DEFAULT_STORE = None
