"""Voice/TTS feature declaration."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CreateJobRequest, ResourceClass
from app.settings.effective_defaults import apply_job_defaults
from app.platform.voice.voice_cloning_defaults import apply_voice_cloning_defaults
from app.runtime.features import FeatureModule

from .jobs import execute_voice_studio_job, store_inline_clone_sample


class VoiceJobInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def _execute(context: JobExecutionContext, job):
    return execute_voice_studio_job(context.job_store, job)


def voice_submission_defaults(request: CreateJobRequest) -> CreateJobRequest:
    value = request.model_dump(mode="python")
    if request.module == "voice-cloning":
        value = apply_voice_cloning_defaults(value)
        value["input_payload"] = store_inline_clone_sample(dict(value.get("input_payload") or {}))
    else:
        value = apply_job_defaults(value)
    return CreateJobRequest.model_validate(value)



def _voice_router(context):
    from .stt_proxy_routes import register_stt_proxy_routes
    from .tts_pcm_websocket import register_tts_pcm_websocket
    from .tts_runtime_routes import register_tts_runtime_routes
    from .voice_job_summary_routes import register_voice_job_summary_routes
    from .voice_library_routes import register_voice_library_route

    router = APIRouter()
    state = context.runtime_state
    register_tts_runtime_routes(router, state)
    register_stt_proxy_routes(router, state)
    register_tts_pcm_websocket(router, state)
    register_voice_job_summary_routes(router, state)
    register_voice_library_route(router, state)
    return router

FEATURE = FeatureModule(
    id="voice",
    title="Voice",
    tier="platform",
    routers=(_voice_router,),
    job_handlers=tuple(
        JobHandlerSpec(
            type=job_type,
            handler=_execute,
            input_model=VoiceJobInput,
            resource_class=(
                ResourceClass.GPU_STT
                if job_type == "voice-cloning.transcribe-sample"
                else ResourceClass.GPU_TTS
            ),
            timeout_seconds=900,
            max_attempts=3,
            submission_policy=voice_submission_defaults,
        )
        for job_type in (
            "tts.synthesize",
            "tts.multi_speaker_synthesize",
            "voice-cloning.create-profile",
            "voice-cloning.transcribe-sample",
        )
    ),
)
