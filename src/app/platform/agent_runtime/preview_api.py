"""Read-only browser preview surface for agent-created web artifacts.

The preview endpoint deliberately reuses the issued WorkspaceSpec authority. It
never serves arbitrary machine paths, and it only exposes a bounded allowlist of
browser assets. HTML is additionally constrained by CSP and is expected to be
embedded by the web client in an iframe sandbox without ``allow-same-origin``.
"""
from __future__ import annotations

from mimetypes import guess_type
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response

from .service import AgentRunService, default_agent_run_service
from .workspace import WorkspaceAuthority, WorkspacePolicyError

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runtime"])

_HTML_SUFFIXES = {".html", ".htm"}
_PREVIEW_ASSET_SUFFIXES = _HTML_SUFFIXES | {
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".wav",
    ".ogg",
    ".mp4",
    ".webm",
}
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_PREVIEW_ASSET_BYTES = 32 * 1024 * 1024

# The iframe itself is also sandboxed by the React client. This header keeps the
# same restrictions when the preview URL is opened directly and blocks generated
# pages from using fetch/XHR/WebSocket to reach the Omnix gateway or the network.
_HTML_PREVIEW_CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' data: blob:",
        "connect-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    ]
)


def _service() -> AgentRunService:
    return default_agent_run_service()


def _resolve_preview_path(run_id: str, asset_path: str) -> Path:
    snapshot = _service().get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    workspace = snapshot.spec.workspace
    if workspace is None:
        raise HTTPException(status_code=404, detail="agent_workspace_not_available")
    root = workspace.worktree or workspace.root
    authority = WorkspaceAuthority(
        root,
        allowed_paths=list(workspace.allowed_paths),
        forbidden_paths=list(workspace.forbidden_paths),
        run_id=run_id,
    )
    try:
        path = authority.resolve_path(asset_path)
    except WorkspacePolicyError as exc:
        raise HTTPException(status_code=403, detail="workspace_preview_path_not_allowed") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="workspace_preview_file_not_found")
    if path.suffix.casefold() not in _PREVIEW_ASSET_SUFFIXES:
        raise HTTPException(status_code=415, detail="workspace_preview_file_type_not_allowed")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="workspace_preview_file_not_found") from exc
    if size > _MAX_PREVIEW_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="workspace_preview_file_too_large")
    return path


def _preview_headers(*, html: bool) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if html:
        headers["Content-Security-Policy"] = _HTML_PREVIEW_CSP
    return headers


@router.get(
    "/{run_id}/workspace-preview/{asset_path:path}",
    response_model=None,
    response_class=Response,
    responses={
        200: {
            "description": "Run-scoped preview asset; content type follows its allowlisted file extension.",
            "content": {
                "text/html": {"schema": {"type": "string", "format": "binary"}},
                "text/plain": {"schema": {"type": "string", "format": "binary"}},
                "text/css": {"schema": {"type": "string", "format": "binary"}},
                "text/javascript": {"schema": {"type": "string", "format": "binary"}},
                "application/javascript": {"schema": {"type": "string", "format": "binary"}},
                "application/json": {"schema": {"type": "object", "additionalProperties": True}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                "image/*": {"schema": {"type": "string", "format": "binary"}},
                "font/*": {"schema": {"type": "string", "format": "binary"}},
                "audio/*": {"schema": {"type": "string", "format": "binary"}},
                "video/*": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
def get_agent_workspace_preview(
    run_id: str,
    asset_path: str,
    source: bool = False,
    download: bool = False,
) -> Response:
    """Serve one run-scoped browser artifact or its HTML source.

    ``source`` is intentionally limited to HTML. Other assets are only exposed
    as browser subresources through the same extension and WorkspaceSpec checks.
    """

    path = _resolve_preview_path(run_id, asset_path)
    suffix = path.suffix.casefold()
    is_html = suffix in _HTML_SUFFIXES
    if source:
        if not is_html:
            raise HTTPException(status_code=415, detail="workspace_preview_source_requires_html")
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="workspace_preview_source_too_large")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail="workspace_preview_html_not_utf8") from exc
        return PlainTextResponse(
            text,
            media_type="text/plain; charset=utf-8",
            headers=_preview_headers(html=False),
        )

    media_type = guess_type(path.name)[0] or "application/octet-stream"
    headers = _preview_headers(html=is_html and not download)
    if download:
        return FileResponse(
            path,
            media_type=media_type,
            filename=path.name,
            content_disposition_type="attachment",
            headers=headers,
        )
    return FileResponse(path, media_type=media_type, headers=headers)
