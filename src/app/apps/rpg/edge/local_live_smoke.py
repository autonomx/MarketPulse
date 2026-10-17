"""Local-only live smoke runner for the interactive RPG response pipeline."""
from __future__ import annotations
from app.config.env import environment as _environment

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.apps.rpg.edge.release_finalization import (
    LOCAL_LIVE_SMOKE_ENV,
    local_live_acceptance_criteria,
)
from app.apps.rpg.edge.release_gates import evaluate_turn_response_release_gates

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_COMMANDS = (
    "I ask Bran how business is doing.",
    "I ask Bran how his day is going.",
    "I ask Bran what he has heard about the old road.",
)


@dataclass(frozen=True)
class SmokeRequest:
    command: str
    submission_id: str
    replay_of: str | None = None


@dataclass(frozen=True)
class SmokeResult:
    command: str
    submission_id: str
    interaction_id: str | None
    elapsed_seconds: float
    response_bytes: int
    gate_ok: bool
    failures: tuple[str, ...]


def assert_live_smoke_allowed(env: Mapping[str, str] | None = None) -> None:
    values = env or _environment()
    if str(values.get("CI") or "").strip().casefold() in _TRUTHY:
        raise RuntimeError("live RPG smoke is local-only and must not run in CI")
    if str(values.get(LOCAL_LIVE_SMOKE_ENV) or "").strip() != "1":
        raise RuntimeError(
            f"set {LOCAL_LIVE_SMOKE_ENV}=1 explicitly before running live provider validation"
        )


def build_smoke_plan(
    commands: Iterable[str] = _DEFAULT_COMMANDS,
    *,
    run_id: str | None = None,
    include_idempotent_replay: bool = True,
) -> list[SmokeRequest]:
    resolved_run_id = str(run_id or uuid.uuid4().hex)
    plan = [
        SmokeRequest(
            command=str(command).strip(),
            submission_id=f"live-smoke:{resolved_run_id}:{index}",
        )
        for index, command in enumerate(commands, start=1)
        if str(command).strip()
    ]
    if include_idempotent_replay and plan:
        first = plan[0]
        plan.append(
            SmokeRequest(
                command=first.command,
                submission_id=first.submission_id,
                replay_of=first.submission_id,
            )
        )
    return plan


def evaluate_live_smoke_payload(payload: dict[str, Any]) -> dict[str, Any]:
    gate = evaluate_turn_response_release_gates(payload)
    visible = raw_visible_response if isinstance(raw_visible_response := payload.get("visible_response"), dict) else {}
    failures = list(gate.get("failures") or [])
    if payload.get("contract_version") != "rpg_turn_response_v2":
        failures.append("unexpected_contract_version")
    if not str(payload.get("interaction_id") or "").strip():
        failures.append("missing_interaction_id")
    if not str(visible.get("plain_text") or payload.get("response") or "").strip():
        failures.append("missing_visible_text")
    return {
        "ok": not failures,
        "failures": sorted(set(str(item) for item in failures)),
        "interaction_id": payload.get("interaction_id"),
        "response_bytes": int(gate.get("response_bytes") or 0),
    }


def evaluate_latency_targets(
    latencies: Iterable[float],
    criteria: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate live-provider latency outside CI against the release targets."""

    values = [max(0.0, float(value)) for value in latencies]
    resolved = dict(criteria or local_live_acceptance_criteria())
    median = statistics.median(values) if values else 0.0
    p95 = _percentile(values, 0.95) if values else 0.0
    failures: list[str] = []
    target_median = float(resolved.get("target_median_seconds") or 0.0)
    target_p95 = float(resolved.get("target_p95_seconds") or 0.0)
    if values and target_median > 0.0 and median > target_median:
        failures.append("dialogue_median_latency_target_missed")
    if values and target_p95 > 0.0 and p95 > target_p95:
        failures.append("dialogue_p95_latency_target_missed")
    return {
        "ok": not failures,
        "failures": failures,
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "median": round(median, 3),
        "p95": round(p95, 3),
        "maximum": round(max(values), 3) if values else 0.0,
        "sample_count": len(values),
    }


def run_live_smoke(
    *,
    base_url: str,
    session_id: str,
    commands: Iterable[str] = _DEFAULT_COMMANDS,
    timeout_seconds: float = 120.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    assert_live_smoke_allowed(env)
    criteria = local_live_acceptance_criteria()
    plan = build_smoke_plan(commands)
    results: list[SmokeResult] = []
    payloads_by_submission: dict[str, dict[str, Any]] = {}

    for item in plan:
        payload, elapsed_seconds, response_bytes = _post_turn(
            base_url=base_url,
            session_id=session_id,
            request=item,
            timeout_seconds=timeout_seconds,
        )
        evaluation = evaluate_live_smoke_payload(payload)
        failures = list(evaluation["failures"])
        if item.replay_of:
            original = payloads_by_submission.get(item.replay_of)
            if original is None:
                failures.append("replay_original_missing")
            elif original.get("interaction_id") != payload.get("interaction_id"):
                failures.append("same_submission_changed_interaction_id")
        else:
            payloads_by_submission[item.submission_id] = payload
        results.append(
            SmokeResult(
                command=item.command,
                submission_id=item.submission_id,
                interaction_id=str(payload.get("interaction_id") or "") or None,
                elapsed_seconds=round(elapsed_seconds, 3),
                response_bytes=response_bytes,
                gate_ok=not failures,
                failures=tuple(sorted(set(failures))),
            )
        )

    non_replay = [result for request, result in zip(plan, results) if request.replay_of is None]
    interaction_ids = [result.interaction_id for result in non_replay if result.interaction_id]
    aggregate_failures: list[str] = []
    if len(interaction_ids) != len(set(interaction_ids)):
        aggregate_failures.append("distinct_submissions_reused_interaction_id")
    minimum_interactions = int(criteria.get("minimum_distinct_interactions") or 0)
    if len(set(interaction_ids)) < minimum_interactions:
        aggregate_failures.append("minimum_distinct_interactions_not_met")
    if any(not result.gate_ok for result in results):
        aggregate_failures.append("one_or_more_turns_failed_release_gates")
    latency = evaluate_latency_targets(
        [result.elapsed_seconds for result in non_replay],
        criteria,
    )
    aggregate_failures.extend(str(item) for item in latency["failures"])

    return {
        "format_version": "rpg_interactive_live_smoke_v1",
        "ok": not aggregate_failures,
        "failures": sorted(set(aggregate_failures)),
        "base_url": base_url.rstrip("/"),
        "session_id": session_id,
        "criteria": criteria,
        "result_count": len(results),
        "distinct_interaction_count": len(set(interaction_ids)),
        "latency_seconds": {
            key: value
            for key, value in latency.items()
            if key not in {"ok", "failures"}
        },
        "results": [asdict(item) for item in results],
    }


def _post_turn(
    *,
    base_url: str,
    session_id: str,
    request: SmokeRequest,
    timeout_seconds: float,
) -> tuple[dict[str, Any], float, int]:
    url = f"{base_url.rstrip('/')}/api/rpg/sessions/{session_id}/turn"
    body = json.dumps({"command": request.command}).encode("utf-8")
    http_request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Omnix-Rpg-Submission-Id": request.submission_id,
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"turn request failed with HTTP {exc.code}: {raw.decode('utf-8', errors='replace')}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"turn request failed: {exc}") from exc
    elapsed = time.perf_counter() - started
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("turn response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("turn response must be a JSON object")
    return payload, elapsed, len(raw)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local-only live validation of the interactive RPG turn pipeline.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--command", action="append", dest="commands")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = run_live_smoke(
            base_url=args.base_url,
            session_id=args.session_id,
            commands=args.commands or _DEFAULT_COMMANDS,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
