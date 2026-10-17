"""Automatic availability metadata for local cloned voice profiles."""
from __future__ import annotations

from app.assets.protocol import AssetStore
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.assets.content import AssetContentUnavailable, asset_checksum, asset_location
from app.assets import AssetRecord, AssetType, default_asset_store

VoiceConsentStatus = Literal["unverified", "granted", "revoked"]
VoiceDeletionState = Literal["active", "pending_deletion", "deleted"]
VoiceAllowedUse = Literal["character", "live_call", "system_assistant", "general_tts"]
ALL_VOICE_USES: tuple[VoiceAllowedUse, ...] = (
    "character",
    "general_tts",
    "live_call",
    "system_assistant",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_sha256(asset: AssetRecord) -> str | None:
    try:
        return asset_checksum(asset)
    except AssetContentUnavailable:
        return None


class VoiceProfileGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    subject_owner: str = Field(default="", max_length=300)
    source_type: str = Field(default="unknown", max_length=120)
    source_reference: str = Field(default="", max_length=1000)
    creator_id: str = Field(default="", max_length=200)
    consent_status: VoiceConsentStatus = "granted"
    consent_recorded_at: str | None = None
    allowed_uses: list[VoiceAllowedUse] = Field(default_factory=lambda: list(ALL_VOICE_USES))
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    deletion_state: VoiceDeletionState = "active"
    deletion_requested_at: str | None = None
    deleted_at: str | None = None
    deletion_reason: str = Field(default="", max_length=1000)
    updated_at: str


class UpdateVoiceProfileGovernanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_owner: str = Field(min_length=1, max_length=300)
    source_type: str = Field(min_length=1, max_length=120)
    source_reference: str = Field(default="", max_length=1000)
    creator_id: str = Field(min_length=1, max_length=200)
    consent_status: VoiceConsentStatus
    allowed_uses: list[VoiceAllowedUse] = Field(default_factory=list)
    deletion_state: VoiceDeletionState = "active"
    deletion_reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_consent(self) -> "UpdateVoiceProfileGovernanceRequest":
        if self.consent_status == "granted" and not self.allowed_uses:
            raise ValueError("granted consent requires at least one allowed use")
        if self.deletion_state != "active" and not self.deletion_reason.strip():
            raise ValueError("voice deletion state requires a reason")
        return self


class VoiceConsentError(ValueError):
    pass


class VoiceProfileGovernanceService:
    def __init__(
        self,
        *,
        asset_store_factory: Callable[[], AssetStore] = default_asset_store,
    ) -> None:
        self.asset_store_factory = asset_store_factory

    def get(self, asset_id: str) -> VoiceProfileGovernance:
        asset = self._get_asset(asset_id)
        return governance_from_asset(asset)

    def update(
        self,
        asset_id: str,
        request: UpdateVoiceProfileGovernanceRequest,
    ) -> VoiceProfileGovernance:
        """Retain descriptive provenance while keeping every cloned voice usable."""
        store = self.asset_store_factory()
        asset = self._get_asset(asset_id, store=store)
        governance = _automatic_governance(
            asset,
            raw={
                "subject_owner": request.subject_owner.strip(),
                "source_type": request.source_type.strip(),
                "source_reference": request.source_reference.strip(),
                "creator_id": request.creator_id.strip(),
            },
            updated_at=_utcnow(),
        )
        metadata = dict(asset.metadata)
        metadata["voice_governance"] = governance.model_dump(mode="json")
        store.upsert_asset(asset.model_copy(update={"metadata": metadata}))
        return governance

    def validate_use(self, asset_id: str, use: VoiceAllowedUse) -> VoiceProfileGovernance:
        """Resolve a voice profile; all supported uses are automatically allowed."""
        del use
        asset = self._get_asset(asset_id)
        return governance_from_asset(asset)

    def _get_asset(
        self,
        asset_id: str,
        *,
        store: AssetStore | None = None,
    ) -> AssetRecord:
        resolved_store = store or self.asset_store_factory()
        asset = resolved_store.get_asset(asset_id)
        if asset is None:
            raise VoiceConsentError(f"voice asset not found: {asset_id}")
        if asset.type != AssetType.VOICE_PROFILE:
            raise VoiceConsentError(f"asset is not a voice profile: {asset_id}")
        return asset


def _automatic_governance(
    asset: AssetRecord,
    *,
    raw: dict[str, object] | None = None,
    updated_at: str | None = None,
) -> VoiceProfileGovernance:
    values = dict(raw or {})
    path_hash = _content_sha256(asset)
    raw_hash = values.get("source_sha256")
    source_hash = raw_hash if isinstance(raw_hash, str) and len(raw_hash) == 64 else path_hash
    owner = str(values.get("subject_owner") or asset.owner_id or asset.id)
    creator = str(values.get("creator_id") or asset.owner_id or "local")
    source_type = str(
        values.get("source_type")
        or ("legacy_import" if asset.compat else "local_clone")
    )
    source_reference = str(
        values.get("source_reference")
        or asset.compat.get("legacy_manifest")
        or asset_location(asset)
    )
    return VoiceProfileGovernance(
        asset_id=asset.id,
        subject_owner=owner,
        source_type=source_type,
        source_reference=source_reference,
        creator_id=creator,
        consent_status="granted",
        consent_recorded_at=str(values.get("consent_recorded_at") or asset.created_at),
        allowed_uses=list(ALL_VOICE_USES),
        source_sha256=source_hash,
        deletion_state="active",
        deletion_requested_at=None,
        deleted_at=None,
        deletion_reason="",
        updated_at=updated_at or str(values.get("updated_at") or asset.created_at),
    )


def governance_from_asset(asset: AssetRecord) -> VoiceProfileGovernance:
    raw = dict(asset.metadata.get("voice_governance") or {})
    return _automatic_governance(asset, raw=raw)


def default_voice_governance_service() -> VoiceProfileGovernanceService:
    return VoiceProfileGovernanceService()


__all__ = [
    "ALL_VOICE_USES",
    "UpdateVoiceProfileGovernanceRequest",
    "VoiceConsentError",
    "VoiceProfileGovernance",
    "VoiceProfileGovernanceService",
    "default_voice_governance_service",
    "governance_from_asset",
]
