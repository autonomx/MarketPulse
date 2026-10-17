"""Image and speech-input settings profile models."""
from pydantic import BaseModel, ConfigDict, Field


class ImageSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    width: int = 768
    height: int = 768
    aspect_ratio: str = Field("1:1", alias="aspectRatio")
    portrait_preset: str = Field("", alias="portraitPreset")
    scene_preset: str = Field("", alias="scenePreset")
    unload_after_generation: bool = Field(True, alias="unloadAfterGeneration")


class SttSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    language: str = ""
    alignment: bool = True
    save_transcript: bool = Field(True, alias="saveTranscript")
    microphone_device_id: str = Field("", alias="microphoneDeviceId")
    noise_suppression: bool = Field(True, alias="noiseSuppression")
    echo_cancellation: bool = Field(True, alias="echoCancellation")


class StorageSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    save_output_by_default: bool = Field(True, alias="saveOutputByDefault")
    retention_days: int = Field(30, alias="retentionDays")
    temporary_asset_cleanup: bool = Field(True, alias="temporaryAssetCleanup")
