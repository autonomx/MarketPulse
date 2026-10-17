"""Stateless validation, edit, preview, and export routes for RPG map content."""

from __future__ import annotations
from fastapi import APIRouter

import json
from typing import Any, Mapping, Sequence

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.apps.rpg.world.map_content_editor import MapContentEditError, apply_map_content_operations
from app.apps.rpg.world.map_content_validation import validate_map_content

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict, Field as _typed_field

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class ValidateDefinitionRequestBody(_TypedRequestModel):
    definition: dict[str, Any] = _typed_field(default_factory=dict)
    context: dict[str, list[str]] = _typed_field(default_factory=dict)

class ApplyOperationsRequestBody(_TypedRequestModel):
    definition: dict[str, Any] = _typed_field(default_factory=dict)
    operations: list[dict[str, Any]] = _typed_field(default_factory=list)
    context: dict[str, list[str]] = _typed_field(default_factory=dict)

class ExportDefinitionRequestBody(_TypedRequestModel):
    definition: dict[str, Any] = _typed_field(default_factory=dict)
    context: dict[str, list[str]] = _typed_field(default_factory=dict)
    filename: str | None = None


class MapContentIssueResponse(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="forbid")

    severity: str
    code: str
    path: str
    detail: str = ""


class MapContentReportResponse(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="forbid")

    ok: bool
    revision: str
    canonical_json: str
    issues: list[MapContentIssueResponse]


class MapEditorValidationResponse(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="forbid")

    ok: bool
    report: MapContentReportResponse


class MapEditorApplyResponse(MapEditorValidationResponse):
    definition: dict[str, Any]


_ROUTE_SENTINEL = "_omnix_rpg_map_editor_routes_registered"


def register_rpg_map_editor_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/map-editor/validate",
        response_model=MapEditorValidationResponse,
        tags=["rpg-map-editor"],
    )
    def validate_definition(request: Request, request_body: ValidateDefinitionRequestBody) -> Response:
        payload = _payload(request_body.model_dump(exclude_unset=True, by_alias=True))
        report = validate_map_content(payload.get("definition"), **_context(payload.get("context")))
        return JSONResponse({"ok": report.ok, "report": report.as_dict()})

    @router.post(
        "/api/rpg/map-editor/apply",
        response_model=MapEditorApplyResponse,
        tags=["rpg-map-editor"],
    )
    def apply_operations(request: Request, request_body: ApplyOperationsRequestBody) -> Response:
        payload = _payload(request_body.model_dump(exclude_unset=True, by_alias=True))
        try:
            result = apply_map_content_operations(
                payload.get("definition"),
                payload.get("operations", ()),
                **_context(payload.get("context")),
            )
        except MapContentEditError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error": exc.code,
                    "path": exc.path,
                    "detail": exc.detail,
                },
            ) from exc
        return JSONResponse({"ok": result.report.ok, **result.as_dict()})

    @router.post(
        "/api/rpg/map-editor/export",
        response_class=Response,
        response_model=dict[str, Any],
        responses={
            200: {
                "description": "Validated map definition exported as a JSON document.",
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True}
                    }
                },
            },
            422: {
                "description": "Map definition failed validation.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/MapEditorValidationResponse"}
                    }
                },
            },
        },
        tags=["rpg-map-editor"],
    )
    def export_definition(request: Request, request_body: ExportDefinitionRequestBody) -> Response:
        payload = _payload(request_body.model_dump(exclude_unset=True, by_alias=True))
        report = validate_map_content(payload.get("definition"), **_context(payload.get("context")))
        if not report.ok:
            return JSONResponse({"ok": False, "report": report.as_dict()}, status_code=422)
        name = _safe_filename(_text(payload.get("filename")) or "rpg-map-definition.json")
        body = json.dumps(json.loads(report.canonical_json), ensure_ascii=False, indent=2, sort_keys=True)
        return Response(
            content=f"{body}\n",
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
                "X-Map-Definition-Revision": report.revision,
            },
        )


def _payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=422, detail={"ok": False, "error": "request_body_must_be_object"})
    return value


def _context(value: object) -> dict[str, tuple[str, ...]]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "canonical_route_ids": _strings(raw.get("canonical_route_ids")),
        "known_map_ids": _strings(raw.get("known_map_ids")),
        "allowed_asset_ids": _strings(raw.get("allowed_asset_ids")),
    }


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))


def _safe_filename(value: str) -> str:
    cleaned = "".join(character for character in value if character.isalnum() or character in {"-", "_", "."})
    if not cleaned.endswith(".json"):
        cleaned = f"{cleaned}.json"
    return cleaned[:120] or "rpg-map-definition.json"


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
