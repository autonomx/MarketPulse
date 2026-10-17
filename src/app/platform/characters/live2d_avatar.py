"""Governed Live2D catalog, local installer, and Character avatar activation."""
from __future__ import annotations

from app.assets.protocol import AssetStore
import hashlib
import hmac
import io
import json
import mimetypes
import posixpath
import ssl
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import certifi
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.assets.content import asset_available, materialize_asset
from app.assets import AssetRecord, AssetType, default_asset_store
from app.runtime.paths import resources_data_root

from .avatar_models import CharacterAvatarPack, UpsertCharacterAvatarPackRequest
from .avatar_service import CharacterAvatarService, default_character_avatar_service
from .repository import CharacterNotFoundError

OPEN_LLM_VTUBER_REVISION = "992309c0aa19845960228f880013d4685fde93b5"
OPEN_LLM_VTUBER_WEB_REVISION = "d176e7df2366952e3bacbf12cf9a8b18a4315932"
MAX_LIVE2D_FILE_BYTES = 64 * 1024 * 1024
# Whole-archive limits: a model is a few dozen files (ASVS 12.1.2).
MAX_LIVE2D_ARCHIVE_MEMBERS = 2_000
MAX_LIVE2D_ARCHIVE_BYTES = 512 * 1024 * 1024
_LIVE2D_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_RUNTIME_FILES = {
    "pixi.min.js": "https://cdnjs.cloudflare.com/ajax/libs/pixi.js/6.5.10/browser/pixi.min.js",
    "live2dcubismcore.min.js": (
        "https://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber-Web/"
        f"{OPEN_LLM_VTUBER_WEB_REVISION}/src/renderer/WebSDK/Core/live2dcubismcore.min.js"
    ),
    "cubism4.min.js": "https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js",
}
_CATALOG_INTERNAL_KEYS = {
    "entry_path",
    "mouth_parameter_ids",
    "mouth_form_parameter_ids",
    "archive_url",
    "archive_sha256",
    "archive_entry_path",
}

_LIVE2D_SAMPLE_MODEL_LICENSE_URL = "https://www.live2d.com/en/learn/sample/model-terms/"
_LIVE2D_RUNTIME_LICENSE_URL = (
    "https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html"
)
_LIVE2D_SAMPLE_LICENSE_SUMMARY = (
    "Live2D sample data. Separate Live2D terms apply; commercial rights depend on "
    "user or organization classification."
)


def _official_sample_entry(
    *,
    model_id: str,
    name: str,
    description: str,
    preview_url: str,
    source_url: str,
    entry_path: str,
    archive_url: str,
    archive_sha256: str,
    archive_entry_path: str,
    mouth_parameter_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": name,
        "description": description,
        "preview_url": preview_url,
        "repository": "Live2D/Cubism Sample Data",
        "revision": f"sha256:{archive_sha256}",
        "entry_path": entry_path,
        "source_url": source_url,
        "model_license_url": _LIVE2D_SAMPLE_MODEL_LICENSE_URL,
        "runtime_license_url": _LIVE2D_RUNTIME_LICENSE_URL,
        "license_summary": _LIVE2D_SAMPLE_LICENSE_SUMMARY,
        "mouth_parameter_ids": mouth_parameter_ids
        or ["PARAM_MOUTH_OPEN_Y", "ParamMouthOpenY", "ParamA"],
        "mouth_form_parameter_ids": ["PARAM_MOUTH_FORM", "ParamMouthForm"],
        "archive_url": archive_url,
        "archive_sha256": archive_sha256,
        "archive_entry_path": archive_entry_path,
    }

_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "open-llm-vtuber-mao-pro",
        "name": "Niziiro Mao (PRO)",
        "description": "Expressive front-facing Cubism 5 sample with idle motion, expressions, and VTuber-oriented face movement.",
        "preview_url": "https://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber/992309c0aa19845960228f880013d4685fde93b5/assets/i1.jpg",
        "repository": "Open-LLM-VTuber/Open-LLM-VTuber",
        "revision": OPEN_LLM_VTUBER_REVISION,
        "entry_path": "live2d-models/mao_pro/runtime/mao_pro.model3.json",
        "source_url": "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber",
        "model_license_url": "https://www.live2d.com/en/download/sample-data/",
        "runtime_license_url": "https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html",
        "license_summary": "Live2D sample data. Separate Live2D terms apply; commercial rights depend on user or organization classification.",
        "mouth_parameter_ids": ["ParamA", "ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y"],
        "mouth_form_parameter_ids": ["ParamMouthForm", "PARAM_MOUTH_FORM"],
    },
    {
        "id": "open-llm-vtuber-shizuku",
        "name": "Shizuku (PRO)",
        "description": "Classic expressive Live2D sample with multiple textures, idle motion, physics, pose, and lip-sync parameters.",
        "preview_url": "https://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber/992309c0aa19845960228f880013d4685fde93b5/assets/i2.jpg",
        "repository": "Open-LLM-VTuber/Open-LLM-VTuber",
        "revision": OPEN_LLM_VTUBER_REVISION,
        "entry_path": "live2d-models/shizuku/runtime/shizuku.model3.json",
        "source_url": "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber",
        "model_license_url": "https://www.live2d.com/en/download/sample-data/",
        "runtime_license_url": "https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html",
        "license_summary": "Live2D sample data. Separate Live2D terms apply; commercial rights depend on user or organization classification.",
        "mouth_parameter_ids": ["PARAM_MOUTH_OPEN_Y", "ParamMouthOpenY", "ParamA"],
        "mouth_form_parameter_ids": ["PARAM_MOUTH_FORM", "ParamMouthForm"],
    },
    _official_sample_entry(
        model_id="live2d-sample-haru",
        name="Haru",
        description="Full-featured avatar with idle and gesture motions, physics, pose, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-haru.jpg",
        source_url="https://www.live2d.com/en/learn/sample/haru/",
        entry_path="live2d-models/haru/runtime/haru.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/haru/haru_ja.zip",
        archive_sha256="3686daa9ed014d0d56c623ef66ba85132fbee3558d2e3e34a154d833c86cebdd",
        archive_entry_path="runtime/haru.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-hiyori-pro",
        name="Hiyori Momose (PRO)",
        description="Standard Cubism avatar with idle and gesture motions, physics, pose, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-hiyori.jpg",
        source_url="https://www.live2d.com/en/learn/sample/momose-hiyori/",
        entry_path="live2d-models/hiyori/runtime/hiyori_pro_t11.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/hiyori/hiyori_en.zip",
        archive_sha256="1e4254d561f2a151562aa67036d78e17e4ffee08869b8ce10ab6052a05e2b3a4",
        archive_entry_path="hiyori_pro/runtime/hiyori_pro_t11.model3.json",
        mouth_parameter_ids=["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "ParamA"],
    ),
    _official_sample_entry(
        model_id="live2d-sample-epsilon-pro",
        name="Epsilon (PRO)",
        description="Cubism 4 avatar with idle and gesture motions, expressions, physics, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-epsillon.jpg",
        source_url="https://www.live2d.com/en/learn/sample/epsilon/",
        entry_path="live2d-models/epsilon/runtime/Epsilon.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/epsilon/epsilon_ja.zip",
        archive_sha256="a2e4d747bb0fca4f5920637ac8acc350b08c46e3275344a273cb9c37d405f9e1",
        archive_entry_path="epsilon_pro/runtime/Epsilon.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-chitose",
        name="Chitose",
        description="Male avatar with idle and gesture motions, expressions, physics, pose, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-chitose.jpg",
        source_url="https://www.live2d.com/en/learn/sample/chitose/",
        entry_path="live2d-models/chitose/runtime/chitose.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/chitose/chitose_ja.zip",
        archive_sha256="8b3feeb65c79452ec64e02dc1776b2c56dee87d44f109be39fef8599e573285b",
        archive_entry_path="runtime/chitose.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-koharu",
        name="Koharu",
        description="Chibi girl avatar with idle motion, animated props and effects, physics, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-SD.png",
        source_url="https://www.live2d.com/en/learn/sample/koharu-haruto/",
        entry_path="live2d-models/koharu/runtime/koharu.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/koharu_haruto/koharu_haruto_ja.zip",
        archive_sha256="a0f677361ea94d8266e3c195ecdc6251e9e49529174315af0640e8eb5d170594",
        archive_entry_path="koharu/runtime/koharu.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-haruto",
        name="Haruto",
        description="Chibi boy avatar with idle motion, animated props and effects, physics, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-SD.png",
        source_url="https://www.live2d.com/en/learn/sample/koharu-haruto/",
        entry_path="live2d-models/haruto/runtime/haruto.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/koharu_haruto/koharu_haruto_ja.zip",
        archive_sha256="a0f677361ea94d8266e3c195ecdc6251e9e49529174315af0640e8eb5d170594",
        archive_entry_path="haruto/runtime/haruto.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-tororo",
        name="Tororo",
        description="White cat avatar with idle and gesture motions, physics, pose, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-th.png",
        source_url="https://www.live2d.com/en/learn/sample/tororo-hijiki/",
        entry_path="live2d-models/tororo/runtime/tororo.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/tororo_hijiki/tororo_hijiki_ja.zip",
        archive_sha256="401cf1596180d7f4752ebac653f01b8c05ff88b56bb84706389c0c105aff1dd8",
        archive_entry_path="tororo/runtime/tororo.model3.json",
    ),
    _official_sample_entry(
        model_id="live2d-sample-hijiki",
        name="Hijiki",
        description="Black cat avatar with idle and gesture motions, physics, pose, blink, and lip-sync.",
        preview_url="https://www.live2d.com/wp-content/uploads/2026/06/sample-img-th.png",
        source_url="https://www.live2d.com/en/learn/sample/tororo-hijiki/",
        entry_path="live2d-models/hijiki/runtime/hijiki.model3.json",
        archive_url="https://cubism.live2d.com/sample-data/bin/tororo_hijiki/tororo_hijiki_ja.zip",
        archive_sha256="401cf1596180d7f4752ebac653f01b8c05ff88b56bb84706389c0c105aff1dd8",
        archive_entry_path="hijiki/runtime/hijiki.model3.json",
    ),
)


class Live2DModelCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    preview_url: str
    repository: str
    revision: str
    source_url: str
    model_license_url: str
    runtime_license_url: str
    license_summary: str
    installed: bool = False
    selected: bool = False


class Live2DModelCatalogResponse(BaseModel):
    models: list[Live2DModelCatalogItem]
    runtime_installed: bool = False


class ActivateLive2DAvatarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=160)
    accept_live2d_runtime_terms: bool = False
    accept_model_terms: bool = False


class Live2DAvatarActionResponse(BaseModel):
    ok: bool = True
    character_id: str
    avatar_pack: CharacterAvatarPack | None = None
    downloaded: bool = False


DownloadBytes = Callable[[str], bytes]


class CharacterLive2DAvatarService:
    def __init__(
        self,
        *,
        avatar_service_factory: Callable[[], CharacterAvatarService] = default_character_avatar_service,
        asset_store_factory: Callable[[], AssetStore] = default_asset_store,
        data_root: str | Path | None = None,
        download_bytes: DownloadBytes | None = None,
    ) -> None:
        self.avatar_service_factory = avatar_service_factory
        self.asset_store_factory = asset_store_factory
        self.data_root = Path(data_root) if data_root is not None else resources_data_root() / "character_live2d"
        self.download_bytes = download_bytes or _download_bytes

    @property
    def runtime_root(self) -> Path:
        return self.data_root / "runtime"

    @property
    def models_root(self) -> Path:
        return self.data_root / "models"

    def catalog(self, character_id: str) -> Live2DModelCatalogResponse:
        current = self.avatar_service_factory().optional_get(character_id)
        store = self.asset_store_factory()
        models = []
        for entry in _MODEL_CATALOG:
            asset_id = _asset_id(entry["id"])
            asset = store.get_asset(asset_id)
            installed = bool(asset and asset_available(asset))
            models.append(
                Live2DModelCatalogItem(
                    **{
                        key: value
                        for key, value in entry.items()
                        if key not in _CATALOG_INTERNAL_KEYS
                    },
                    installed=installed,
                    selected=bool(current and current.renderer == "live2d" and current.rig_asset_id == asset_id),
                )
            )
        return Live2DModelCatalogResponse(
            models=models,
            runtime_installed=all((self.runtime_root / filename).is_file() for filename in _RUNTIME_FILES),
        )

    def activate(
        self,
        character_id: str,
        request: ActivateLive2DAvatarRequest,
    ) -> Live2DAvatarActionResponse:
        if not request.accept_live2d_runtime_terms or not request.accept_model_terms:
            raise ValueError("Live2D runtime and sample-model terms must be accepted before download or activation")
        entry = _catalog_entry(request.model_id)
        avatar_service = self.avatar_service_factory()
        current = avatar_service.optional_get(character_id)
        asset, downloaded = self._ensure_model_asset(entry)
        self._remember_previous_pack(asset, current, character_id)
        request_payload = UpsertCharacterAvatarPackRequest(
            expected_version=current.version if current else None,
            render_mode="viseme",
            renderer="live2d",
            rig_asset_id=asset.id,
            mouth_anchor={"x": 0.5, "y": 0.68},
        )
        pack = avatar_service.upsert(character_id, request_payload)
        return Live2DAvatarActionResponse(
            character_id=character_id,
            avatar_pack=pack,
            downloaded=downloaded,
        )

    def disable(self, character_id: str) -> Live2DAvatarActionResponse:
        avatar_service = self.avatar_service_factory()
        current = avatar_service.optional_get(character_id)
        if current is None:
            return Live2DAvatarActionResponse(character_id=character_id, avatar_pack=None)
        if current.renderer != "live2d" or not current.rig_asset_id:
            return Live2DAvatarActionResponse(character_id=character_id, avatar_pack=current)

        asset = self.asset_store_factory().get_asset(current.rig_asset_id)
        previous_payload = None
        if asset is not None:
            previous_payload = dict(asset.metadata or {}).get("previous_packs", {}).get(character_id)
        if isinstance(previous_payload, dict):
            previous_payload = dict(previous_payload)
            previous_payload.pop("character_id", None)
            previous_payload.pop("version", None)
            previous_payload.pop("created_at", None)
            previous_payload.pop("updated_at", None)
            previous_payload["expected_version"] = current.version
            restored = avatar_service.upsert(character_id, UpsertCharacterAvatarPackRequest.model_validate(previous_payload))
            return Live2DAvatarActionResponse(character_id=character_id, avatar_pack=restored)

        avatar_service.delete(character_id)
        return Live2DAvatarActionResponse(character_id=character_id, avatar_pack=None)

    def runtime_file(self, filename: str) -> Path:
        if filename not in _RUNTIME_FILES:
            raise FileNotFoundError(filename)
        path = self.runtime_root / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def model_file(self, asset_id: str, asset_path: str) -> Path:
        asset = self.asset_store_factory().get_asset(asset_id)
        if asset is None or asset.module != "character-live2d" or asset.type != AssetType.SETTINGS_ARTIFACT:
            raise FileNotFoundError(asset_id)
        root_value = str(dict(asset.metadata or {}).get("root_path") or "")
        # Live2D models are installed directory trees on local disk.
        root = Path(root_value) if root_value else materialize_asset(asset).parent
        root = root.resolve()
        requested = (root / _safe_relative_path(asset_path)).resolve()
        if requested != root and root not in requested.parents:
            raise FileNotFoundError(asset_path)
        if not requested.is_file():
            raise FileNotFoundError(asset_path)
        return requested

    def _ensure_model_asset(self, entry: dict[str, Any]) -> tuple[AssetRecord, bool]:
        self._ensure_runtime()
        asset_store = self.asset_store_factory()
        asset_id = _asset_id(entry["id"])
        existing = asset_store.get_asset(asset_id)
        if existing is not None and asset_available(existing):
            return existing, False

        model_root = self.models_root / entry["id"]
        entry_relative = _model_relative_path(entry)
        entry_target = model_root / entry_relative
        if entry.get("archive_url"):
            self._write_archive_model(entry, model_root, entry_relative)
        else:
            self._write_remote_model(entry, model_root, entry_relative)

        now = _utcnow()
        asset = AssetRecord(
            id=asset_id,
            module="character-live2d",
            type=AssetType.SETTINGS_ARTIFACT,
            mime_type="application/vnd.live2d.model3+json",
            storage_path=str(entry_target),
            metadata={
                "model_id": entry["id"],
                "display_name": entry["name"],
                "root_path": str(model_root),
                "entry_path": entry_relative,
                "repository": entry["repository"],
                "revision": entry["revision"],
                "source_url": entry["source_url"],
                "model_license_url": entry["model_license_url"],
                "runtime_license_url": entry["runtime_license_url"],
                "license_summary": entry["license_summary"],
                "mouth_parameter_ids": list(entry["mouth_parameter_ids"]),
                "mouth_form_parameter_ids": list(entry["mouth_form_parameter_ids"]),
                "runtime_files": list(_RUNTIME_FILES),
                "previous_packs": {},
                **(
                    {
                        "archive_url": entry["archive_url"],
                        "archive_sha256": entry["archive_sha256"],
                    }
                    if entry.get("archive_url")
                    else {}
                ),
            },
            created_at=now,
            compat={
                "source_project": entry["repository"],
                "source_revision": entry["revision"],
            },
        )
        asset_store.upsert_asset(asset)
        return asset, True

    def _write_remote_model(
        self,
        entry: dict[str, Any],
        model_root: Path,
        entry_relative: str,
    ) -> bytes:
        entry_target = model_root / entry_relative
        entry_bytes = self._write_remote_file(
            _raw_model_url(entry, entry_relative),
            entry_target,
        )
        try:
            model_json = json.loads(entry_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Live2D model descriptor for {entry['id']}") from exc
        entry_dir = PurePosixPath(entry_relative).parent
        for referenced_path in _model_references(model_json):
            resolved_relative = _safe_relative_path(str(entry_dir / referenced_path))
            self._write_remote_file(
                _raw_model_url(entry, resolved_relative),
                model_root / resolved_relative,
            )
        return entry_bytes

    def _write_archive_model(
        self,
        entry: dict[str, Any],
        model_root: Path,
        entry_relative: str,
    ) -> bytes:
        archive_url = str(entry["archive_url"])
        archive_bytes = self.download_bytes(archive_url)
        if len(archive_bytes) > MAX_LIVE2D_FILE_BYTES:
            raise ValueError(f"Live2D download exceeds {MAX_LIVE2D_FILE_BYTES} bytes: {archive_url}")
        expected_hash = str(entry["archive_sha256"]).lower()
        actual_hash = hashlib.sha256(archive_bytes).hexdigest()
        if not hmac.compare_digest(actual_hash, expected_hash):
            raise ValueError(f"Live2D archive checksum mismatch for {entry['id']}")

        archive_entry = _safe_relative_path(str(entry["archive_entry_path"]))
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = _safe_zip_members(archive)
                entry_bytes = _read_zip_member(archive, members, archive_entry)
                try:
                    model_json = json.loads(entry_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"invalid Live2D model descriptor for {entry['id']}"
                    ) from exc

                self._write_local_file(model_root / entry_relative, entry_bytes)
                archive_entry_dir = PurePosixPath(archive_entry).parent
                target_entry_dir = PurePosixPath(entry_relative).parent
                for referenced_path in _model_references(model_json):
                    archive_reference = _safe_relative_path(
                        str(archive_entry_dir / referenced_path)
                    )
                    target_reference = _safe_relative_path(
                        str(target_entry_dir / referenced_path)
                    )
                    self._write_local_file(
                        model_root / target_reference,
                        _read_zip_member(archive, members, archive_reference),
                    )
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid Live2D archive for {entry['id']}") from exc
        return entry_bytes

    def _ensure_runtime(self) -> None:
        for filename, url in _RUNTIME_FILES.items():
            path = self.runtime_root / filename
            if not path.is_file():
                self._write_remote_file(url, path)

    def _write_remote_file(self, url: str, target: Path) -> bytes:
        if target.is_file():
            return target.read_bytes()
        data = self.download_bytes(url)
        if len(data) > MAX_LIVE2D_FILE_BYTES:
            raise ValueError(f"Live2D download exceeds {MAX_LIVE2D_FILE_BYTES} bytes: {url}")
        self._write_local_file(target, data)
        return data

    def _write_local_file(self, target: Path, data: bytes) -> None:
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.partial")
        temporary.write_bytes(data)
        temporary.replace(target)

    def _remember_previous_pack(
        self,
        target_asset: AssetRecord,
        current: CharacterAvatarPack | None,
        character_id: str,
    ) -> None:
        previous_payload: dict[str, Any] | None = None
        if current is not None and current.renderer != "live2d":
            previous_payload = current.model_dump(mode="json")
        elif current is not None and current.rig_asset_id:
            current_asset = self.asset_store_factory().get_asset(current.rig_asset_id)
            if current_asset is not None:
                candidate = dict(current_asset.metadata or {}).get("previous_packs", {}).get(character_id)
                if isinstance(candidate, dict):
                    previous_payload = dict(candidate)
        if previous_payload is None:
            return
        metadata = dict(target_asset.metadata or {})
        previous_packs = dict(metadata.get("previous_packs") or {})
        previous_packs[character_id] = previous_payload
        metadata["previous_packs"] = previous_packs
        self.asset_store_factory().upsert_asset(target_asset.model_copy(update={"metadata": metadata}))


def register_character_live2d_avatar_routes(
    router: APIRouter,
    *,
    service_factory: Callable[[], CharacterLive2DAvatarService] = CharacterLive2DAvatarService,
) -> None:
    @router.get(
        "/api/characters/{character_id}/live2d-models",
        response_model=Live2DModelCatalogResponse,
        tags=["characters"],
    )
    def live2d_model_catalog(character_id: str) -> Live2DModelCatalogResponse:
        try:
            return service_factory().catalog(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/characters/{character_id}/live2d-avatar",
        response_model=Live2DAvatarActionResponse,
        tags=["characters"],
    )
    def activate_live2d_avatar(
        character_id: str,
        request: ActivateLive2DAvatarRequest,
    ) -> Live2DAvatarActionResponse:
        try:
            return service_factory().activate(character_id, request)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/api/characters/{character_id}/live2d-avatar/disable",
        response_model=Live2DAvatarActionResponse,
        tags=["characters"],
    )
    def disable_live2d_avatar(character_id: str) -> Live2DAvatarActionResponse:
        try:
            return service_factory().disable(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/character-live2d/runtime/{filename}",
        response_model=None,
        response_class=FileResponse,
        responses={
            200: {
                "description": "Live2D runtime script or binary content.",
                "content": {
                    "text/javascript": {"schema": {"type": "string", "format": "binary"}},
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                },
            }
        },
        tags=["characters"],
    )
    def live2d_runtime_file(filename: str) -> FileResponse:
        try:
            path = service_factory().runtime_file(filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Live2D runtime file not installed") from exc
        media_type = "text/javascript" if path.suffix == ".js" else "application/octet-stream"
        # Large static files: revalidated by ETag rather than refetched.
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-cache"})

    @router.get(
        "/api/character-live2d/assets/{asset_id}/{asset_path:path}",
        response_model=None,
        response_class=FileResponse,
        responses={
            200: {
                "description": "Live2D model asset bytes with an extension-derived media type.",
                "content": {
                    "application/json": {"schema": {"type": "object", "additionalProperties": True}},
                    "image/*": {"schema": {"type": "string", "format": "binary"}},
                    "audio/*": {"schema": {"type": "string", "format": "binary"}},
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                },
            }
        },
        tags=["characters"],
    )
    def live2d_model_file(asset_id: str, asset_path: str) -> FileResponse:
        try:
            path = service_factory().model_file(asset_id, asset_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Live2D model file not found") from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Large static files: revalidated by ETag rather than refetched.
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-cache"})


def _catalog_entry(model_id: str) -> dict[str, Any]:
    for entry in _MODEL_CATALOG:
        if entry["id"] == model_id:
            return dict(entry)
    raise ValueError(f"unknown Live2D model: {model_id}")


def _asset_id(model_id: str) -> str:
    return f"character-live2d:{model_id}"


def _model_relative_path(entry: dict[str, Any]) -> str:
    marker = "live2d-models/"
    source_path = str(entry["entry_path"])
    if marker not in source_path:
        raise ValueError("Live2D catalog entry is outside live2d-models")
    return _safe_relative_path(source_path.split(marker, 1)[1].split("/", 1)[1])


def _raw_model_url(entry: dict[str, Any], relative_path: str) -> str:
    source_entry = PurePosixPath(str(entry["entry_path"]))
    model_root = source_entry.parents[1]
    repository = str(entry["repository"])
    revision = str(entry["revision"])
    return (
        f"https://raw.githubusercontent.com/{repository}/{revision}/"
        f"{model_root.as_posix()}/{_safe_relative_path(relative_path)}"
    )


def _safe_relative_path(value: str) -> str:
    normalized = posixpath.normpath(str(value).replace("\\", "/")).lstrip("/")
    path = PurePosixPath(normalized)
    if normalized in {"", "."} or ".." in path.parts:
        raise ValueError(f"unsafe Live2D asset path: {value}")
    return path.as_posix()


def _safe_zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    infos = archive.infolist()
    if len(infos) > MAX_LIVE2D_ARCHIVE_MEMBERS:
        raise ValueError(f"Live2D archive has more than {MAX_LIVE2D_ARCHIVE_MEMBERS} entries")
    if sum(info.file_size for info in infos) > MAX_LIVE2D_ARCHIVE_BYTES:
        raise ValueError(f"Live2D archive expands beyond {MAX_LIVE2D_ARCHIVE_BYTES} bytes")
    for info in infos:
        if info.is_dir():
            continue
        normalized = _safe_relative_path(info.filename)
        if normalized in members:
            raise ValueError(f"duplicate Live2D archive member: {normalized}")
        if info.flag_bits & 0x1:
            raise ValueError(f"encrypted Live2D archive member: {normalized}")
        if info.file_size > MAX_LIVE2D_FILE_BYTES:
            raise ValueError(
                f"Live2D archive member exceeds {MAX_LIVE2D_FILE_BYTES} bytes: {normalized}"
            )
        members[normalized] = info
    return members


def _read_zip_member(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    relative_path: str,
) -> bytes:
    normalized = _safe_relative_path(relative_path)
    info = members.get(normalized)
    if info is None:
        raise ValueError(f"Live2D archive member not found: {normalized}")
    data = archive.read(info)
    if len(data) > MAX_LIVE2D_FILE_BYTES:
        raise ValueError(
            f"Live2D archive member exceeds {MAX_LIVE2D_FILE_BYTES} bytes: {normalized}"
        )
    return data


def _model_references(model_json: dict[str, Any]) -> list[str]:
    references = dict(model_json.get("FileReferences") or {})
    result: list[str] = []
    for key in ("Moc", "Physics", "Pose", "DisplayInfo", "UserData"):
        value = references.get(key)
        if isinstance(value, str) and value:
            result.append(_safe_relative_path(value))
    for value in references.get("Textures") or []:
        if isinstance(value, str) and value:
            result.append(_safe_relative_path(value))
    for value in references.get("Expressions") or []:
        if isinstance(value, dict) and isinstance(value.get("File"), str):
            result.append(_safe_relative_path(value["File"]))
    for motion_group in dict(references.get("Motions") or {}).values():
        for value in motion_group or []:
            if isinstance(value, dict) and isinstance(value.get("File"), str):
                result.append(_safe_relative_path(value["File"]))
            if isinstance(value, dict) and isinstance(value.get("Sound"), str):
                result.append(_safe_relative_path(value["Sound"]))
    return list(dict.fromkeys(result))


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Omnix-Live2D-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=45, context=_LIVE2D_SSL_CONTEXT) as response:
        content_length = int(response.headers.get("Content-Length") or 0)
        if content_length > MAX_LIVE2D_FILE_BYTES:
            raise ValueError(f"Live2D download exceeds {MAX_LIVE2D_FILE_BYTES} bytes: {url}")
        return response.read(MAX_LIVE2D_FILE_BYTES + 1)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ActivateLive2DAvatarRequest",
    "CharacterLive2DAvatarService",
    "Live2DAvatarActionResponse",
    "Live2DModelCatalogItem",
    "Live2DModelCatalogResponse",
    "register_character_live2d_avatar_routes",
]
