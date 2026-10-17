"""Download and verify the pinned model files in ``catalog.json``."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).with_name("catalog.json")
_REVISION_LENGTH = 40


class ModelDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelEntry:
    id: str
    repo: str
    revision: str
    service: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, ModelEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    catalog: dict[str, ModelEntry] = {}
    for model_id, entry in raw["models"].items():
        revision = str(entry["revision"])
        if len(revision) != _REVISION_LENGTH or any(char not in "0123456789abcdef" for char in revision):
            raise ModelDownloadError(f"{model_id}: revision must be a full commit hash")
        files = tuple(
            ModelFile(path=name, sha256=str(item["sha256"]), size=int(item["size"]))
            for name, item in sorted(entry["files"].items())
        )
        if not files:
            raise ModelDownloadError(f"{model_id}: no files listed")
        catalog[model_id] = ModelEntry(model_id, str(entry["repo"]), revision, str(entry["service"]), files)
    return catalog


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_root(cache_dir: str | Path | None = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def _repo_cache(root: Path, repo: str) -> Path:
    return root / ("models--" + repo.replace("/", "--"))


def _snapshot_file(root: Path, entry: ModelEntry, item: ModelFile) -> Path:
    return _repo_cache(root, entry.repo) / "snapshots" / entry.revision / item.path


def verify(entry: ModelEntry, *, cache_dir: str | Path | None = None) -> list[str]:
    """Problems with the cached copy of ``entry`` (empty when it is complete and intact)."""
    root = cache_root(cache_dir)
    problems = []
    for item in entry.files:
        target = _snapshot_file(root, entry, item)
        if not target.is_file():
            problems.append(f"{item.path}: missing")
        elif sha256_file(target) != item.sha256:
            problems.append(f"{item.path}: checksum mismatch")
    ref = _repo_cache(root, entry.repo) / "refs" / "main"
    if not ref.is_file() or ref.read_text(encoding="utf-8").strip() != entry.revision:
        problems.append("refs/main: not the pinned revision")
    return problems


Fetch = Callable[..., str]


def _hub_fetch(**kwargs: Any) -> str:
    from huggingface_hub import hf_hub_download

    return str(hf_hub_download(**kwargs))


def download(entry: ModelEntry, *, cache_dir: str | Path | None = None, fetch: Fetch = _hub_fetch) -> Path:
    """Fetch the pinned files, refusing any whose digest differs; returns the snapshot directory."""
    root = cache_root(cache_dir)
    for item in entry.files:
        target = _snapshot_file(root, entry, item)
        if target.is_file() and sha256_file(target) == item.sha256:
            continue
        fetched = Path(fetch(repo_id=entry.repo, filename=item.path, revision=entry.revision, cache_dir=str(root)))
        actual = sha256_file(fetched)
        if actual != item.sha256:
            _discard(fetched)
            raise ModelDownloadError(f"{entry.id}: {item.path} has SHA-256 {actual}, expected {item.sha256}")
        logger.info("verified %s %s", entry.id, item.path)
    # Services load by repository id; offline, the cache resolves "main" through this ref.
    ref = _repo_cache(root, entry.repo) / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(entry.revision, encoding="utf-8")
    return _repo_cache(root, entry.repo) / "snapshots" / entry.revision


def _discard(path: Path) -> None:
    blob = path.resolve()
    for candidate in {path, blob}:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def select(catalog: dict[str, ModelEntry], ids: Iterable[str], service: str | None) -> list[ModelEntry]:
    chosen = [entry for entry in catalog.values() if service is not None and entry.service == service]
    for model_id in ids:
        if model_id not in catalog:
            raise ModelDownloadError(f"unknown model {model_id!r}; known: {', '.join(sorted(catalog))}")
        if catalog[model_id] not in chosen:
            chosen.append(catalog[model_id])
    if not chosen:
        raise ModelDownloadError("name a model id or --service")
    return chosen


def pin(model_id: str, repo: str, service: str, include: list[str], *, revision: str = "main") -> dict[str, Any]:
    """Maintainer tool: resolve a commit and record the digests of the listed files."""
    from huggingface_hub import HfApi, hf_hub_download

    info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    if info.sha is None:
        raise ModelDownloadError(f"{repo}: no commit for {revision}")
    siblings = {item.rfilename: item for item in info.siblings or []}
    missing = [name for name in include if name not in siblings]
    if missing:
        raise ModelDownloadError(f"{repo}@{info.sha}: missing files {missing}")
    files: dict[str, dict[str, Any]] = {}
    for name in include:
        sibling = siblings[name]
        lfs = getattr(sibling, "lfs", None)
        if lfs is not None:
            files[name] = {"sha256": lfs.sha256, "size": int(lfs.size)}
        else:
            local = Path(hf_hub_download(repo_id=repo, filename=name, revision=info.sha))
            files[name] = {"sha256": sha256_file(local), "size": local.stat().st_size}
    return {model_id: {"repo": repo, "revision": info.sha, "service": service, "files": files}}
