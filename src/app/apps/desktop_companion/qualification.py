"""Deterministic production-qualification reports for Desktop Companion evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .evaluation import (
    DesktopCompanionEvaluationRecord,
    DesktopCompanionEvaluationStore,
    DesktopCompanionGateMetric,
    GateStatus,
)
from .release_gate import (
    DesktopCompanionEvidencePartition,
    build_partitioned_desktop_companion_release_gate,
    build_partitioned_desktop_companion_speech_gate,
)

QualificationStage = Literal["text", "speech"]


class DesktopCompanionQualificationReport(BaseModel):
    """Content-free operational summary for one exact evidence partition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: QualificationStage
    status: GateStatus
    recommendation: str
    exact_commit_sha: str
    observation_schema_version: int = Field(ge=1)
    attention_policy_version: int = Field(ge=1)
    vision_provider: str
    vision_model_hash: str
    remote_provider: bool
    records_scanned: int = Field(ge=0)
    rollout_stages: tuple[str, ...]
    scenarios: tuple[str, ...]
    missing_scenarios: tuple[str, ...]
    metrics: tuple[DesktopCompanionGateMetric, ...]
    failures: tuple[str, ...]
    insufficient: tuple[str, ...]
    evidence_evaluation_ids: tuple[str, ...]


def build_desktop_companion_qualification_report(
    records: list[DesktopCompanionEvaluationRecord],
    *,
    partition: DesktopCompanionEvidencePartition,
    stage: QualificationStage,
) -> DesktopCompanionQualificationReport:
    _validate_exact_partition(partition)
    gate = (
        build_partitioned_desktop_companion_speech_gate(records, partition)
        if stage == "speech"
        else build_partitioned_desktop_companion_release_gate(records, partition)
    )
    recommendation = {
        "pass": f"eligible_for_{stage}_rollout",
        "fail": "keep_rollout_disabled_and_investigate",
        "insufficient": "collect_more_exact_partition_evidence",
    }[gate.status]
    return DesktopCompanionQualificationReport(
        stage=stage,
        status=gate.status,
        recommendation=recommendation,
        exact_commit_sha=partition.exact_commit_sha,
        observation_schema_version=partition.observation_schema_version,
        attention_policy_version=partition.attention_policy_version,
        vision_provider=partition.vision_provider or "",
        vision_model_hash=partition.vision_model_hash or "",
        remote_provider=bool(partition.remote_provider),
        records_scanned=gate.records_scanned,
        rollout_stages=tuple(gate.rollout_stages),
        scenarios=gate.scenarios,
        missing_scenarios=gate.missing_scenarios,
        metrics=gate.metrics,
        failures=gate.failures,
        insufficient=gate.insufficient,
        evidence_evaluation_ids=gate.evidence_evaluation_ids,
    )


def render_desktop_companion_qualification_markdown(
    report: DesktopCompanionQualificationReport,
) -> str:
    lines = [
        f"# Desktop Companion {report.stage.title()} Qualification",
        "",
        f"- Status: **{report.status.upper()}**",
        f"- Recommendation: `{report.recommendation}`",
        f"- Exact commit: `{report.exact_commit_sha}`",
        f"- Evidence records: {report.records_scanned}",
        f"- Observation schema: {report.observation_schema_version}",
        f"- Attention policy: {report.attention_policy_version}",
        f"- Provider class: `{report.vision_provider}`",
        f"- Model hash: `{report.vision_model_hash}`",
        f"- Remote provider: `{_remote_label(report.remote_provider)}`",
        "",
        "## Scenario coverage",
        "",
        f"Observed: {', '.join(report.scenarios) or 'none'}",
        f"Missing: {', '.join(report.missing_scenarios) or 'none'}",
        "",
        "## Metrics",
        "",
        "| Metric | Status | Samples | Observed | Limit | Comparison |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for metric in report.metrics:
        observed = "n/a" if metric.observed is None else f"{metric.observed:g}"
        lines.append(
            f"| {metric.name} | {metric.status} | {metric.samples} | {observed} | "
            f"{metric.limit:g} | {metric.comparison} |"
        )
    lines.extend(
        [
            "",
            "## Blocking findings",
            "",
            f"Failures: {', '.join(report.failures) or 'none'}",
            f"Insufficient: {', '.join(report.insufficient) or 'none'}",
            "",
            (
                "This report contains identifiers and aggregate metrics only; it does not include frames, "
                "captured content, prompts, transcripts, or generated commentary."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def qualification_exit_code(status: GateStatus) -> int:
    return {"pass": 0, "insufficient": 2, "fail": 3}[status]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    partition = DesktopCompanionEvidencePartition(
        exact_commit_sha=args.exact_commit_sha,
        observation_schema_version=args.observation_schema_version,
        attention_policy_version=args.attention_policy_version,
        vision_provider=args.vision_provider,
        vision_model_hash=args.vision_model_hash,
        remote_provider=args.remote_provider == "true",
    )
    store = DesktopCompanionEvaluationStore(Path(args.evidence_path) if args.evidence_path else None)
    report = build_desktop_companion_qualification_report(
        store.list(limit=args.limit),
        partition=partition,
        stage=args.stage,
    )
    rendered = (
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_desktop_companion_qualification_markdown(report)
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return qualification_exit_code(report.status)


def _validate_exact_partition(partition: DesktopCompanionEvidencePartition) -> None:
    if not partition.exact_commit_sha or partition.exact_commit_sha == "unknown-local-build":
        raise ValueError("qualification requires an exact deployed commit SHA")
    if not partition.vision_provider:
        raise ValueError("qualification requires one vision provider class")
    if not partition.vision_model_hash:
        raise ValueError("qualification requires one vision model hash")
    if partition.remote_provider is None:
        raise ValueError("qualification requires explicit remote/local provider status")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate one exact Desktop Companion evidence partition."
    )
    parser.add_argument("--stage", choices=("text", "speech"), required=True)
    parser.add_argument("--exact-commit-sha", required=True)
    parser.add_argument("--evidence-path")
    parser.add_argument("--vision-provider", required=True)
    parser.add_argument("--vision-model-hash", required=True)
    parser.add_argument("--remote-provider", choices=("true", "false"), required=True)
    parser.add_argument("--observation-schema-version", type=int, default=1)
    parser.add_argument("--attention-policy-version", type=int, default=1)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output")
    return parser


def _remote_label(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DesktopCompanionQualificationReport",
    "build_desktop_companion_qualification_report",
    "main",
    "qualification_exit_code",
    "render_desktop_companion_qualification_markdown",
]
