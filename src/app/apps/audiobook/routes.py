"""Browser API for canonical audiobook projects and durable source jobs."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from contextvars import copy_context
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.persistence.blob_store import default_blob_store
from app.persistence.database import default_database
from app.security.tenant_context import current_tenant
from app.runtime.paths import resources_data_root
from app.runtime.background import (
    BackgroundOwnershipUnavailable, BackgroundWorker,
)
from app.runtime.capabilities import RuntimeCapability
from app.persistence.background_authority import require_background_owner
from app.runtime.config import get_runtime_config
from app.runtime.logging import runtime_transition

from .extraction import (
    SUPPORTED_SOURCE_FORMATS,
    normalize_extraction_settings,
    parse_page_ranges,
)
from app.errors import error_code

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .service import AudiobookService


_LOG = logging.getLogger(__name__)
_SOURCE_FORMAT_PATTERN = "^(" + "|".join(sorted(SUPPORTED_SOURCE_FORMATS)) + ")$"
_SOURCE_LIBRARY_DISPLAY_PATH = Path("resources") / "data" / "audiobooks"
_SERVICE_CONTEXT_LOCK = threading.Lock()
_SERVICE_CONTEXT: tuple[Any, Any] | None = None
_BINARY_SCHEMA = {"schema": {"type": "string", "format": "binary"}}
_SPOOL_IN_MEMORY_BYTES = 1024 * 1024
_MAX_COVER_BYTES = 10 * 1024 * 1024


def _binary_response(*media_types: str) -> dict[int, dict[str, Any]]:
    return {
        200: {
            "description": "File content returned with its source media type.",
            "content": {media_type: _BINARY_SCHEMA for media_type in media_types},
        }
    }


class ClassificationRequest(BaseModel):
    custom_rules: str = Field(default="", max_length=4000)


class ExcludeSpanText(BaseModel):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    source_text: str = Field(min_length=1)


def _source_library_root() -> Path:
    root = resources_data_root() / "audiobooks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _source_library_files() -> dict[str, object]:
    root = _source_library_root().resolve()
    files: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if path.suffix.lower().lstrip(".") not in SUPPORTED_SOURCE_FORMATS:
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if not resolved.is_file():
                continue
            stat = resolved.stat()
            name = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            # Do not expose symlink targets outside the audiobook library.
            continue
        files.append({
            "name": name,
            "source_format": path.suffix.lower().lstrip("."),
            "size_bytes": stat.st_size,
        })
    files.sort(key=lambda item: str(item["name"]).casefold())
    return {"directory": str(_SOURCE_LIBRARY_DISPLAY_PATH), "files": files}


def _resolve_source_library_file(filename: str) -> Path:
    root = _source_library_root().resolve()
    candidate = (root / Path(filename)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("source library path is outside the audiobook directory") from exc
    if not candidate.is_file():
        raise FileNotFoundError(filename)
    if candidate.suffix.lower().lstrip(".") not in SUPPORTED_SOURCE_FORMATS:
        raise ValueError("unsupported source format")
    return candidate


def _page_extraction_settings(source_format: str, exclude_pages: str | None) -> dict[str, object]:
    if not exclude_pages or not exclude_pages.strip():
        return {}
    return normalize_extraction_settings(
        source_format, {"excluded_page_ranges": parse_page_ranges(exclude_pages)},
    )


class CreateAudiobookProject(BaseModel):
    title: str
    author: str = ""
    language: str = "en"
    custom_rules: str = Field(default="", max_length=4000)


class UpdateAudiobookProject(BaseModel):
    title: str
    author: str = ""


class CreateSpeaker(BaseModel):
    canonical_name: str


class AssignVoice(BaseModel):
    voice_profile_id: str


class ConfirmSpeakerAlias(BaseModel):
    alias: str


class ResolveReviewIssue(BaseModel):
    speaker_id: str
    role: str
    delivery: str = ""


class ReviseSpanAnnotation(ResolveReviewIssue):
    pass


class StartRender(BaseModel):
    # Omitted: Omnix's selected TTS engine (the one /models/current reports).
    provider_id: str | None = None
    model_id: str | None = None
    model_revision: str
    generation_parameters: dict[str, object] = Field(default_factory=dict)
    seed: int | None = None


class ReorderRenderQueue(BaseModel):
    """Waiting chapters to render first, in this order; the rest follow in their current order."""
    chapter_ids: list[str] = Field(min_length=1, max_length=1000)


class StartPreview(StartRender):
    chapter_id: str
    span_id: str
    voice_profile_id: str | None = None


class StartExport(BaseModel):
    format: str = "m4b"


class SetPronunciation(BaseModel):
    source_term: str
    spoken_term: str


class SetAudiobookMode(BaseModel):
    mode: str = Field(pattern="^(standard|story_only|verbatim)$")


class CharacterPerformance(BaseModel):
    """A character's own levels; an omitted one follows the book."""
    stability: float | None = Field(default=None, ge=0, le=1)
    style: float | None = Field(default=None, ge=0, le=1)


class BookPerformance(BaseModel):
    """Stability and Style Exaggeration for every render (Voice Studio's sliders)."""
    stability: float = Field(default=0.75, ge=0, le=1)
    style: float = Field(default=0.35, ge=0, le=1)
    speakers: dict[str, CharacterPerformance] = Field(default_factory=dict)


class SetAutoDirect(BaseModel):
    """After classifying: direct the dialogue always, only while Fish S2 Pro is the engine, or never."""
    policy: Literal["always", "fish", "never"]


class DirectPerformance(BaseModel):
    """Chapters to direct; empty directs the whole book."""
    chapter_ids: list[str] = Field(default_factory=list, max_length=1000)


class SetPerformanceResult(BaseModel):
    project_id: str
    performance: BookPerformance
    changed: bool


class SetDocumentOverride(BaseModel):
    scope: str = Field(pattern="^(BLOCK|REGION|RECURRENCE_GROUP|DOCUMENT_ROLE)$")
    scope_key: str = Field(min_length=1, max_length=512)
    action: str = Field(
        default="DEFAULT", pattern="^(DEFAULT|READ|SKIP|READ_ONCE)$"
    )
    role_override: str | None = None


def _octet_stream_body(title: str) -> dict[str, Any]:
    """OpenAPI for a raw body the route reads as a stream instead of a parameter."""
    return {"requestBody": {"required": True, "content": {"application/octet-stream": {
        "schema": {"contentMediaType": "application/octet-stream", "title": title, "type": "string"},
    }}}}


def _declared_length(request: Request) -> int:
    try:
        return int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        return 0


async def _spool_body(request: Request, *, limit: int, what: str) -> BinaryIO:
    """Copy the body to a temporary file, refusing it once it passes ``limit``.

    The cap also holds for chunked uploads that declare no Content-Length.
    Small bodies stay in memory; larger ones spill to disk.
    """
    if _declared_length(request) > limit:
        raise HTTPException(status_code=413, detail=f"{what} is too large")
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY_BYTES)
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"{what} is too large")
            spool.write(chunk)
        if not size:
            raise HTTPException(status_code=422, detail=f"{what} content is required")
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _service_and_context() -> tuple["AudiobookService", Any]:
    global _SERVICE_CONTEXT
    if _SERVICE_CONTEXT is None:
        with _SERVICE_CONTEXT_LOCK:
            if _SERVICE_CONTEXT is None:
                from .service import AudiobookService

                database = default_database()
                context = current_tenant()
                _SERVICE_CONTEXT = (AudiobookService(database, default_blob_store()), context)
    assert _SERVICE_CONTEXT is not None
    return _SERVICE_CONTEXT


router = APIRouter()


@router.get("/api/audiobook/projects", tags=["audiobook"])
def list_projects(offset: int = Query(default=0, ge=0)) -> dict[str, object]:
    service, context = _service_and_context()
    return {"projects": service.list_projects(context, offset=offset)}


@router.get("/api/audiobook/voices", tags=["audiobook"])
def list_voices() -> dict[str, object]:
    from .service import AudiobookService

    return {"voices": AudiobookService.list_voices()}


@router.get("/api/audiobook/models/current", tags=["audiobook"])
def audiobook_model() -> dict[str, object]:
    from .model_identity import current_model_identity

    try:
        return current_model_identity()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/audiobook/source-library", tags=["audiobook"])
def source_library() -> dict[str, object]:
    return _source_library_files()


@router.post("/api/audiobook/projects", tags=["audiobook"])
def create_project(request: CreateAudiobookProject) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.create_project(context, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/api/audiobook/projects/{project_id}", tags=["audiobook"])
def update_project(project_id: str, request: UpdateAudiobookProject) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.update_project(context, project_id=project_id, **request.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/audiobook/projects/{project_id}", tags=["audiobook"], status_code=204)
def delete_project(project_id: str) -> Response:
    service, context = _service_and_context()
    try:
        service.delete_project(context, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    return Response(status_code=204)


@router.delete("/api/audiobook/projects/{project_id}/assets/{asset_id}", tags=["audiobook"])
def delete_asset(project_id: str, asset_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.delete_asset(context, project_id=project_id, asset_id=asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook asset not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/audiobook/projects/{project_id}", tags=["audiobook"])
def get_project(project_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.get_project(context, project_id, include_text=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.get("/api/audiobook/projects/{project_id}/chapters/{chapter_id}", tags=["audiobook"])
def get_chapter(project_id: str, chapter_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.get_chapter(context, project_id=project_id,
                                   chapter_id=chapter_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook chapter not found") from exc


@router.get(
    "/api/audiobook/projects/{project_id}/document-structure",
    tags=["audiobook"],
)
def get_document_structure(
    project_id: str, chapter_id: str | None = Query(default=None),
) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.get_document_structure(
            context, project_id=project_id, chapter_id=chapter_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="audiobook project not found"
        ) from exc


@router.put(
    "/api/audiobook/projects/{project_id}/performance",
    tags=["audiobook"], response_model=SetPerformanceResult,
)
def set_performance(project_id: str, request: BookPerformance) -> dict[str, object]:
    """Store the book's Stability/Style and per-character overrides; the render run restarts."""
    service, context = _service_and_context()
    try:
        return service.set_performance(
            context, project_id=project_id, performance=request.model_dump(exclude_none=True),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/api/audiobook/projects/{project_id}/auto-direct", tags=["audiobook"])
def set_auto_direct(project_id: str, request: SetAutoDirect) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.set_auto_direct(context, project_id=project_id, policy=request.policy)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post(
    "/api/audiobook/projects/{project_id}/direct-performance",
    tags=["audiobook"], status_code=202,
)
def direct_performance(project_id: str, request: DirectPerformance) -> dict[str, str]:
    """Queue an LLM pass that writes a delivery direction (emotion) for each dialogue line."""
    service, context = _service_and_context()
    try:
        return service.direct_performance(context, project_id=project_id, chapter_ids=request.chapter_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/api/audiobook/projects/{project_id}/reading-policy",
    tags=["audiobook"],
)
def set_reading_policy(
    project_id: str, request: SetAudiobookMode,
) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.set_audiobook_mode(
            context, project_id=project_id, mode=request.mode,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="audiobook project not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/api/audiobook/projects/{project_id}/document-overrides",
    tags=["audiobook"],
)
def set_document_override(
    project_id: str, request: SetDocumentOverride,
) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.set_document_override(
            context, project_id=project_id, **request.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="audiobook project not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/api/audiobook/projects/{project_id}/source",
    tags=["audiobook"],
    status_code=202,
    openapi_extra=_octet_stream_body("Source Content"),
)
async def upload_source(
    project_id: str, request: Request,
    source_format: str = Query(pattern=_SOURCE_FORMAT_PATTERN),
    filename: str = Query(default="book"),
    exclude_pages: str | None = Query(default=None, max_length=500),
) -> dict[str, str]:
    from .extraction import MAX_SOURCE_BYTES, UnsupportedSource

    source = await _spool_body(request, limit=MAX_SOURCE_BYTES, what="source")
    try:
        service, context = await asyncio.to_thread(_service_and_context)
        extraction_settings = _page_extraction_settings(source_format, exclude_pages)
        return await asyncio.to_thread(
            service.submit_source, context, project_id=project_id,
            source_format=source_format, content=source, filename=filename,
            extraction_settings=extraction_settings,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except UnsupportedSource as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        source.close()


@router.post("/api/audiobook/projects/{project_id}/source/library", tags=["audiobook"], status_code=202)
async def import_library_source(
    project_id: str, filename: str = Query(min_length=1),
    exclude_pages: str | None = Query(default=None, max_length=500),
) -> dict[str, str]:
    from .extraction import MAX_SOURCE_BYTES, UnsupportedSource

    try:
        path = _resolve_source_library_file(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source file not found in audiobook directory") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="source is too large")
        source = path.open("rb")
    except OSError as exc:
        raise HTTPException(status_code=409, detail="source file could not be read") from exc
    try:
        service, context = await asyncio.to_thread(_service_and_context)
        source_format = path.suffix.lower().lstrip(".")
        extraction_settings = _page_extraction_settings(source_format, exclude_pages)
        return await asyncio.to_thread(
            service.submit_source, context, project_id=project_id,
            source_format=source_format, content=source, filename=path.name,
            extraction_settings=extraction_settings,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except UnsupportedSource as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail="source file could not be read") from exc
    finally:
        source.close()


@router.get(
    "/api/audiobook/projects/{project_id}/source/download",
    response_model=None,
    response_class=StreamingResponse,
    responses=_binary_response(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/epub+zip",
        "text/html",
        "text/markdown",
        "application/pdf",
        "text/plain",
    ),
    tags=["audiobook"],
)
def download_source(project_id: str) -> StreamingResponse:
    service, context = _service_and_context()
    try:
        handle, mime, filename = service.open_source(context, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook source not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="audiobook source failed integrity verification") from exc

    def chunks():
        with handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk

    safe_filename = filename.replace('"', '').replace('\r', '').replace('\n', '')
    suffix = Path(safe_filename).suffix.lower().lstrip('.')
    fallback_name = f"audiobook.{suffix}" if suffix in SUPPORTED_SOURCE_FORMATS else "audiobook"
    return StreamingResponse(chunks(), media_type=mime,
                             background=BackgroundTask(handle.close), headers={
        "Content-Length": str(os.fstat(handle.fileno()).st_size),
        "Content-Disposition": (
            f'attachment; filename="{fallback_name}"; filename*=UTF-8\'\'{quote(safe_filename, safe="")}'
        ),
    })


@router.post(
    "/api/audiobook/projects/{project_id}/cover",
    tags=["audiobook"],
    openapi_extra=_octet_stream_body("Cover Content"),
)
async def upload_cover(
    project_id: str, request: Request, filename: str = Query(default="cover"),
) -> dict[str, str]:
    with await _spool_body(request, limit=_MAX_COVER_BYTES, what="cover") as cover:
        content = cover.read()
    service, context = await asyncio.to_thread(_service_and_context)
    try:
        return await asyncio.to_thread(service.set_cover, context,
                                       project_id=project_id, content=content,
                                       filename=filename)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/api/audiobook/projects/{project_id}/cover",
    response_model=None,
    response_class=Response,
    responses=_binary_response("image/jpeg", "image/png"),
    tags=["audiobook"],
)
def project_cover(project_id: str) -> Response:
    service, context = _service_and_context()
    try:
        content, mime = service.read_cover(context, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook cover not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="audiobook cover failed integrity verification") from exc
    return Response(content, media_type=mime)


@router.post("/api/audiobook/projects/{project_id}/speakers", tags=["audiobook"])
def add_speaker(project_id: str, request: CreateSpeaker) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.add_speaker(context, project_id=project_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/spans/{span_id}/speech-exclusions", tags=["audiobook"])
def exclude_span_text(project_id: str, span_id: str, body: ExcludeSpanText) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.exclude_span_text(context, project_id=project_id, span_id=span_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="current source span not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/chapters/{chapter_id}/speech-exclusion", tags=["audiobook"])
def remove_chapter_from_audio(project_id: str, chapter_id: str) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.remove_chapter_from_audio(context, project_id=project_id, chapter_id=chapter_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="current source chapter not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/api/audiobook/projects/{project_id}/speech-exclusions/{exclusion_id}", tags=["audiobook"])
def restore_span_text(project_id: str, exclusion_id: str) -> dict[str, bool]:
    service, context = _service_and_context()
    try:
        return service.restore_span_text(context, project_id=project_id, exclusion_id=exclusion_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="speech exclusion not found") from exc


@router.post("/api/audiobook/projects/{project_id}/pronunciations", tags=["audiobook"])
def set_pronunciation(project_id: str, request: SetPronunciation) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.set_pronunciation(context, project_id=project_id,
                                         **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post(
    "/api/audiobook/projects/{project_id}/speakers/{speaker_id}/reject",
    tags=["audiobook"],
)
def reject_speaker(project_id: str, speaker_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.reject_speaker(
            context, project_id=project_id, speaker_id=speaker_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="speaker not found") from exc


@router.post("/api/audiobook/projects/{project_id}/speakers/{speaker_id}/casting", tags=["audiobook"])
def assign_voice(project_id: str, speaker_id: str, request: AssignVoice) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.assign_voice(
            context, project_id=project_id, speaker_id=speaker_id,
            voice_profile_id=request.voice_profile_id,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="speaker not found") from exc


@router.post("/api/audiobook/projects/{project_id}/speakers/{speaker_id}/aliases", tags=["audiobook"])
def confirm_alias(project_id: str, speaker_id: str, request: ConfirmSpeakerAlias) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.confirm_alias(context, project_id=project_id,
                                     speaker_id=speaker_id, alias=request.alias)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="speaker not found") from exc


@router.post("/api/audiobook/projects/{project_id}/speakers/{speaker_id}/aliases/unconfirm", tags=["audiobook"])
def unconfirm_alias(project_id: str, speaker_id: str, request: ConfirmSpeakerAlias) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.unconfirm_alias(context, project_id=project_id,
                                       speaker_id=speaker_id, alias=request.alias)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="confirmed alias not found") from exc


@router.post("/api/audiobook/projects/{project_id}/aliases/confirm-suggested", tags=["audiobook"])
def confirm_suggested_aliases(project_id: str) -> dict[str, int]:
    service, context = _service_and_context()
    try:
        return service.confirm_suggested_aliases(context, project_id=project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/review/{issue_id}", tags=["audiobook"])
def resolve_issue(project_id: str, issue_id: str, request: ResolveReviewIssue) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.resolve_issue(
            context, project_id=project_id, issue_id=issue_id, **request.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="review issue or speaker not found") from exc


@router.post("/api/audiobook/projects/{project_id}/spans/{span_id}/annotation", tags=["audiobook"])
def revise_span(project_id: str, span_id: str, request: ReviseSpanAnnotation) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.revise_span(context, project_id=project_id,
                                   span_id=span_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="span or speaker not found") from exc


@router.post("/api/audiobook/projects/{project_id}/render", tags=["audiobook"], status_code=202)
def start_render(project_id: str, request: StartRender) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.start_render(context, project_id=project_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/jobs/{job_id}/cancel", tags=["audiobook"])
def cancel_audiobook_job(project_id: str, job_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.cancel_job(context, project_id=project_id, job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook job not found") from exc


@router.post("/api/audiobook/projects/{project_id}/jobs/{job_id}/pause", tags=["audiobook"])
def pause_audiobook_job(project_id: str, job_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.pause_job(context, project_id=project_id, job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/jobs/{job_id}/resume", tags=["audiobook"])
def resume_audiobook_job(project_id: str, job_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.resume_job(context, project_id=project_id, job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/render/pause", tags=["audiobook"])
def pause_audiobook_render_queue(project_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.pause_render_queue(context, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/render/resume", tags=["audiobook"])
def resume_audiobook_render_queue(project_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.pause_render_queue(context, project_id=project_id, resume=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/render/order", tags=["audiobook"])
def reorder_audiobook_render_queue(project_id: str, request: ReorderRenderQueue) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.reorder_render_queue(context, project_id=project_id, chapter_ids=request.chapter_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/api/audiobook/projects/{project_id}/chapters/{chapter_id}/audio",
    response_model=None,
    response_class=FileResponse,
    responses=_binary_response("audio/wav"),
    tags=["audiobook"],
)
def chapter_audio(project_id: str, chapter_id: str) -> FileResponse:
    """A finished chapter's assembled audio, with range support for seeking."""
    service, context = _service_and_context()
    try:
        path = service.open_chapter_audio(context, project_id=project_id, chapter_id=chapter_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="chapter audio not found") from exc
    return FileResponse(path, media_type="audio/wav", headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
    })


@router.post("/api/audiobook/projects/{project_id}/render/stop", tags=["audiobook"])
def stop_audiobook_render_queue(project_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.stop_render_queue(context, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.post("/api/audiobook/projects/{project_id}/jobs/{job_id}/retry", tags=["audiobook"], status_code=202)
def retry_audiobook_job(project_id: str, job_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.retry_pipeline_job(context, project_id=project_id, job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/extract-quotes", tags=["audiobook"], status_code=202)
def extract_quotes(project_id: str, body: ClassificationRequest | None = None) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.reclassify_source(
            context, project_id=project_id, custom_rules=body.custom_rules if body else None,
            extraction_only=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/reclassify", tags=["audiobook"], status_code=202)
def reclassify_audiobook(
    project_id: str, body: ClassificationRequest | None = None,
) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.reclassify_source(
            context, project_id=project_id,
            custom_rules=body.custom_rules if body else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/classification-rules", tags=["audiobook"])
def save_classification_rules(project_id: str, body: ClassificationRequest) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.save_classification_rules(context, project_id=project_id, custom_rules=body.custom_rules)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/audiobook/projects/{project_id}/preview", tags=["audiobook"], status_code=202)
def start_preview(project_id: str, request: StartPreview) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.start_preview(context, project_id=project_id,
                                     **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.get(
    "/api/audiobook/projects/{project_id}/previews/{job_id}/audio",
    response_model=None,
    response_class=Response,
    responses=_binary_response("audio/wav"),
    tags=["audiobook"],
)
def preview_audio(project_id: str, job_id: str) -> Response:
    service, context = _service_and_context()
    try:
        content = service.read_preview(context, project_id=project_id,
                                       job_id=job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="preview audio not found") from exc
    return Response(content, media_type="audio/wav")


@router.post("/api/audiobook/projects/{project_id}/exports", tags=["audiobook"], status_code=202)
def start_export(project_id: str, request: StartExport) -> dict[str, str]:
    service, context = _service_and_context()
    try:
        return service.start_export(context, project_id=project_id, format=request.format)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="audiobook asset is unavailable") from exc
    except RuntimeError as exc:
        logger.warning("audiobook_export_unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail=error_code(exc, "audiobook_export_unavailable")) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.get("/api/audiobook/projects/{project_id}/exports", tags=["audiobook"])
def list_exports(project_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return {"exports": service.list_exports(context, project_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook project not found") from exc


@router.get(
    "/api/audiobook/projects/{project_id}/exports/{export_id}/download",
    response_model=None,
    response_class=StreamingResponse,
    responses=_binary_response(
        "audio/mp4", "audio/flac", "audio/wav", "audio/mpeg"
    ),
    tags=["audiobook"],
)
def download_export(project_id: str, export_id: str) -> StreamingResponse:
    service, context = _service_and_context()
    try:
        handle, mime, format = service.open_export(
            context, project_id=project_id, export_id=export_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook export not found") from exc
    def chunks():
        with handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk

    return StreamingResponse(chunks(), media_type=mime,
                             background=BackgroundTask(handle.close), headers={
        "Content-Length": str(os.fstat(handle.fileno()).st_size),
        "Content-Disposition": f'attachment; filename="audiobook.{format}"',
    })


@router.get("/api/audiobook/projects/{project_id}/exports/{export_id}/report", tags=["audiobook"])
def export_report(project_id: str, export_id: str) -> dict[str, object]:
    service, context = _service_and_context()
    try:
        return service.export_report(context, project_id=project_id,
                                     export_id=export_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audiobook export not found") from exc


class _AudiobookWorkers:
    """The pipeline, render and preview loops behind the audiobook background worker.

    Each loop claims durable jobs only while this process owns background work,
    and exits when ownership is revoked.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            self._spawn("audiobook-ingest", self._pipeline_loop),
            self._spawn("audiobook-render", self._render_loop),
            self._spawn("audiobook-preview", self._preview_loop),
        ]

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        if any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("Audiobook workers did not stop within the shutdown deadline")

    @staticmethod
    def _spawn(name: str, loop: Any) -> threading.Thread:
        # Threads do not inherit context variables: keep the owner's scope.
        thread = threading.Thread(target=copy_context().run, args=(loop,), name=name, daemon=True)
        thread.start()
        return thread

    def _pipeline_loop(self) -> None:
        from .assembly_service import run_assemble_once
        from .export_service import run_export_once
        from .performance_direction import run_direction_once
        from .worker import run_analyze_once, run_ingest_once

        def step(database: Any, blobs: Any, context: Any, worker_id: str) -> bool:
            return bool(
                run_ingest_once(database, blobs, context, worker_id=worker_id)
                or run_analyze_once(database, context, worker_id=worker_id)
                or run_direction_once(database, blobs, context, worker_id=worker_id)
                or run_assemble_once(database, blobs, context, worker_id=worker_id)
                or run_export_once(database, blobs, context, worker_id=worker_id)
            )

        self._poll("ingest", step, failure="ingest_poll_failed")

    def _render_loop(self) -> None:
        from .render_service import run_render_once

        self._poll("render", lambda database, blobs, context, worker_id: run_render_once(
            database, blobs, context, worker_id=worker_id,
        ), failure="render_poll_failed")

    def _preview_loop(self) -> None:
        from .render_service import run_preview_once

        self._poll("preview", lambda database, blobs, context, worker_id: run_preview_once(
            database, blobs, context, worker_id=worker_id,
        ), failure="preview_poll_failed")

    def _poll(self, kind: str, step: Any, *, failure: str) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        service, context = runtime
        worker_id = f"audiobook:{kind}:{uuid4().hex}"
        while not self._stop.is_set():
            try:
                require_background_owner()
                if not step(service.database, service.blobs, context, worker_id):
                    self._stop.wait(1.0)
            except BackgroundOwnershipUnavailable:
                return
            except Exception as exc:
                self._error(failure, exc)
                self._stop.wait(5.0)

    def _runtime(self) -> tuple[Any, Any] | None:
        while not self._stop.is_set():
            try:
                require_background_owner()
                return _service_and_context()
            except BackgroundOwnershipUnavailable:
                return None
            except Exception as exc:
                self._error("initialize_failed", exc)
                self._stop.wait(5.0)
        return None

    @staticmethod
    def _error(transition: str, error: Exception) -> None:
        runtime_transition(_LOG, component="audiobook", role=get_runtime_config().gateway_role.value,
                           transition=transition, error=error, level="warning")


def create_audiobook_router() -> APIRouter:
    return router


def create_audiobook_background_worker() -> BackgroundWorker:
    workers = _AudiobookWorkers()

    async def startup() -> None:
        await asyncio.to_thread(workers.start)

    async def shutdown() -> None:
        await asyncio.to_thread(workers.stop)

    return BackgroundWorker(
        name="audiobook", monitor=workers, startup=(startup,), shutdown=(shutdown,),
        requires=frozenset({RuntimeCapability.RUN_JOB_WORKERS}),
    )
