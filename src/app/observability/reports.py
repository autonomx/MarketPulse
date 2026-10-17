"""Report artifact inventory for platform gateway contracts."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.runtime.paths import test_results_root


class ReportArtifact(BaseModel):
    id: str
    path: str
    kind: str
    size_bytes: int


class ReportListResponse(BaseModel):
    reports: list[ReportArtifact] = Field(default_factory=list)


def _report_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "html_report"
    if suffix == ".json":
        return "json_report"
    if suffix == ".zip":
        return "archive"
    if suffix in {".txt", ".md"}:
        return "text_report"
    return "artifact"


def list_report_artifacts(limit: int = 100) -> ReportListResponse:
    root = test_results_root()
    reports: list[ReportArtifact] = []
    if not root.exists():
        return ReportListResponse()

    candidates = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[:limit]:
        reports.append(
            ReportArtifact(
                id=str(path.relative_to(root)).replace("\\", "/"),
                path=str(path),
                kind=_report_kind(path),
                size_bytes=path.stat().st_size,
            )
        )
    return ReportListResponse(reports=reports)
