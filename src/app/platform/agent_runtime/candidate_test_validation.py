"""Candidate-derived execution evidence for run-owned regression tests.

The static TaskRevision validation plan cannot know about regression tests that the
coding agent creates during implementation or repair. Independent review must not
start merely because some other ``final-state-tests`` command passed on the same
workspace state: every executable test file in the authoritative run-owned subject
needs fresh execution evidence for that exact candidate.

This module is deliberately deterministic. It classifies test paths from the
RunChangeSet subject, matches exact-state ValidationResult rows, and has a narrow
fallback for test runners (notably Playwright) that older command classification may
not yet persist as ValidationResult rows. The fallback is accepted only when the
successful test command occurs after the last potentially workspace-mutating tool
completion in the durable event stream.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from .contracts import AgentEvent, ValidationResult, ValidationSpec
from .event_queries import json_object


_EXECUTABLE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".cpp",
    ".cc",
    ".sh",
    ".ps1",
}
_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_NON_EXECUTABLE_SEGMENTS = {
    "__snapshots__",
    "snapshots",
    "fixtures",
    "fixture",
    "testdata",
    "test-data",
    "__mocks__",
    "mocks",
}
_NON_EXECUTABLE_BASENAMES = {
    "conftest.py",
    "playwright.config.js",
    "playwright.config.ts",
    "vitest.config.js",
    "vitest.config.ts",
    "jest.config.js",
    "jest.config.ts",
}
_JS_TEST_RE = re.compile(r"(?:^|\.)(?:test|spec)\.(?:mjs|cjs|js|jsx|tsx|ts)$", re.I)
_PY_TEST_RE = re.compile(r"^(?:test_.+|.+_test)\.py$", re.I)
_GO_TEST_RE = re.compile(r".+_test\.go$", re.I)
_JAVA_TEST_RE = re.compile(r".+(?:Test|Tests)\.java$")
_GENERIC_TEST_RE = re.compile(r".+(?:_test|_tests)\.(?:rb|php|cs|c|cc|cpp|rs|sh|ps1)$", re.I)
_TEST_FILE_TOKEN_RE = re.compile(
    r"[^\s\"']+(?:\.test|\.spec)\.(?:mjs|cjs|js|jsx|tsx|ts)|"
    r"(?:^|[/\\\s\"'])test_[^\s\"']+\.py|"
    r"[^\s\"']+_test\.(?:py|go|rb|php|cs|c|cc|cpp|rs|sh|ps1)|"
    r"[^\s\"']+(?:Test|Tests)\.java",
    re.I,
)
_TEST_COMMAND_RE = re.compile(
    r"\bpytest\b|\bvitest\b|\bjest\b|\bplaywright\s+test\b|\bcypress\s+run\b|"
    r"\bnpx\s+(?:--yes\s+)?(?:playwright\s+test|cypress\s+run)\b|"
    r"\b(?:npm|npm\.cmd|pnpm|yarn)\b[^\r\n]*\btest\b|"
    r"\bgo\s+test\b|\bcargo\s+test\b|\bdotnet\s+test\b|"
    r"\b(?:mvn|mvnw|gradle|gradlew)\b[^\r\n]*\btest\b",
    re.I,
)
_TARGETING_SELECTOR_RE = re.compile(
    r"(?:^|\s)(?:-k|-t|--grep|--grep-invert|--filter|--testNamePattern|--test-name-pattern)(?:=|\s)",
    re.I,
)
_READ_ONLY_SHELL_RE = re.compile(
    r"^\s*(?:git\s+(?:status|diff|show|log|rev-parse)|pwd\b|ls\b|dir\b|cat\b|type\b|"
    r"rg\b|grep\b|findstr\b)",
    re.I,
)
_NON_MUTATING_VALIDATION_RE = re.compile(
    r"\b(?:pytest|vitest|jest|playwright\s+test|cypress\s+run|typecheck|tsc|ruff|eslint|lint)\b|"
    r"\b(?:npm|npm\.cmd|pnpm|yarn)\b[^\r\n]*\b(?:test|build|typecheck|lint)\b|"
    r"\b(?:go|cargo|dotnet)\s+test\b",
    re.I,
)
_READ_ONLY_TOOLS = {
    "read",
    "ls",
    "grep",
    "search",
    "omnix_change_set",
    "omnix_capability",
    "git_status",
}
_SHELL_TOOLS = {"bash", "powershell"}
_MUTATING_TOOLS = {"edit", "write"}


def _normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").lstrip("./")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


def _extension(path: str) -> str:
    basename = _normalize_path(path).rsplit("/", 1)[-1].casefold()
    dot = basename.rfind(".")
    return basename[dot:] if dot >= 0 else ""


def _is_e2e_path(path: str) -> bool:
    parts = [part.casefold() for part in _normalize_path(path).split("/") if part]
    return any(part in {"e2e", "end-to-end", "end_to_end"} for part in parts[:-1])


def is_executable_test_path(path: str) -> bool:
    """Return whether a run-owned path denotes a directly executable test file."""

    normalized = _normalize_path(path)
    if not normalized or normalized.endswith("/"):
        return False
    parts = [part.casefold() for part in normalized.split("/") if part]
    if any(part in _NON_EXECUTABLE_SEGMENTS for part in parts[:-1]):
        return False
    basename = parts[-1] if parts else ""
    if basename in _NON_EXECUTABLE_BASENAMES:
        return False
    if _extension(normalized) not in _EXECUTABLE_EXTENSIONS:
        return False
    original_basename = normalized.rsplit("/", 1)[-1]
    return bool(
        _JS_TEST_RE.search(original_basename)
        or _PY_TEST_RE.match(original_basename)
        or _GO_TEST_RE.match(original_basename)
        or _JAVA_TEST_RE.match(original_basename)
        or _GENERIC_TEST_RE.match(original_basename)
    )


def executable_candidate_test_paths(paths: Iterable[str]) -> list[str]:
    """Return normalized, unique run-owned executable test paths."""

    return sorted(
        {
            normalized
            for path in paths
            if (normalized := _normalize_path(path)) and is_executable_test_path(normalized)
        }
    )


def _existing_snapshot_paths(paths: Iterable[str], workspace_root: str | None) -> list[str]:
    normalized = executable_candidate_test_paths(paths)
    if not workspace_root:
        return normalized
    root = Path(workspace_root).expanduser().resolve()
    existing: list[str] = []
    for path in normalized:
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            existing.append(path)
    return existing


def candidate_test_validation_specs(paths: Iterable[str]) -> list[ValidationSpec]:
    """Build deterministic same-candidate validation requests for missing test files."""

    specs: list[ValidationSpec] = []
    for path in executable_candidate_test_paths(paths):
        digest = hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()[:16]
        specs.append(
            ValidationSpec(
                id=f"candidate-test-execution-{digest}",
                kind="test",
                description=(
                    "Execute this run-owned regression test against the exact final candidate before independent "
                    f"review: {path}"
                ),
                covers=[],
                required=True,
                command_hint=f"Run this exact test file with its repository test runner: {path}",
            )
        )
    return specs


def _test_command(command: str) -> bool:
    return bool(_TEST_COMMAND_RE.search(str(command or "")))


def _path_aliases(path: str, *, basename_unique: bool) -> list[str]:
    normalized = _normalize_path(path).casefold()
    aliases = [normalized]
    parts = normalized.split("/")
    for marker in ("tests", "test", "e2e", "specs", "spec"):
        if marker in parts:
            aliases.append("/".join(parts[parts.index(marker) :]))
    if basename_unique:
        aliases.append(parts[-1])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _runner_compatible_paths(command: str, required_paths: list[str]) -> set[str]:
    """Conservatively map a broad runner invocation to test families it can execute."""

    folded = str(command or "").casefold()
    if "playwright" in folded or "cypress" in folded:
        return {
            path
            for path in required_paths
            if _extension(path) in _JS_EXTENSIONS
            and (_is_e2e_path(path) or ".spec." in path.rsplit("/", 1)[-1].casefold())
        }
    if re.search(r"\bpytest\b", folded):
        return {path for path in required_paths if _extension(path) == ".py"}
    if re.search(r"\b(?:vitest|jest)\b", folded):
        return {
            path for path in required_paths
            if _extension(path) in _JS_EXTENSIONS and not _is_e2e_path(path)
        }
    if re.search(r"\bgo\s+test\b", folded):
        return {path for path in required_paths if _extension(path) == ".go"}
    if re.search(r"\bcargo\s+test\b", folded):
        return {path for path in required_paths if _extension(path) == ".rs"}
    if re.search(r"\bdotnet\s+test\b", folded):
        return {path for path in required_paths if _extension(path) == ".cs"}
    if re.search(r"\b(?:mvn|mvnw|gradle|gradlew)\b", folded):
        return {path for path in required_paths if _extension(path) == ".java"}
    if re.search(r"\b(?:npm|npm\.cmd|pnpm|yarn)\b[^\r\n]*\btest\b", folded):
        # A generic JS package test script is not assumed to include a separate
        # E2E tree. Explicit paths or an explicit Playwright/Cypress runner are
        # required for those files.
        return {
            path for path in required_paths
            if _extension(path) in _JS_EXTENSIONS and not _is_e2e_path(path)
        }
    return set()


def _command_coverage(command: str, required_paths: list[str]) -> set[str]:
    """Return candidate test paths proved executed by one successful test command."""

    if not _test_command(command):
        return set()
    folded = str(command or "").replace("\\", "/").casefold()
    basename_counts = Counter(path.rsplit("/", 1)[-1].casefold() for path in required_paths)
    explicit: set[str] = set()
    for path in required_paths:
        basename = path.rsplit("/", 1)[-1].casefold()
        aliases = _path_aliases(path, basename_unique=basename_counts[basename] == 1)
        if any(alias in folded for alias in aliases):
            explicit.add(path)
    if explicit:
        return explicit

    # A command naming some other concrete test file or using a test-name/filter
    # selector is targeted and cannot prove that every run-owned test executed.
    if _TEST_FILE_TOKEN_RE.search(str(command or "")) or _TARGETING_SELECTOR_RE.search(str(command or "")):
        return set()

    compatible = _runner_compatible_paths(command, required_paths)
    if not compatible:
        return set()

    # Directory-scoped commands such as ``pytest tests/agent_runtime`` cover only
    # compatible candidate tests below the named test directory. Bare runner
    # commands cover the compatible family for that runner.
    directory_covered: set[str] = set()
    for path in compatible:
        parts = path.casefold().split("/")
        for index, part in enumerate(parts[:-1]):
            if part not in {"tests", "test", "e2e", "specs", "spec"}:
                continue
            prefix = "/".join(parts[: index + 1])
            tail = "/".join(parts[index: index + 1])
            if prefix in folded or re.search(rf"(?:^|\s)[\"']?{re.escape(tail)}(?:[/\s\"']|$)", folded):
                directory_covered.add(path)
                break
    if directory_covered:
        return directory_covered
    return compatible


def _event_command(started: AgentEvent | None, completed: AgentEvent) -> str:
    args = json_object(started.payload.get("args")) if started else {}
    return str(args.get("command") or completed.payload.get("command") or "").strip()


def _event_tool(started: AgentEvent | None, completed: AgentEvent) -> str:
    return str(completed.payload.get("tool") or (started.payload.get("tool") if started else "") or "").strip()


def _exit_code_from_payload(payload: object) -> int | None:
    if isinstance(payload, dict):
        for key in ("exit_code", "exitCode"):
            value = payload.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        for key in ("details", "result"):
            nested = payload.get(key)
            code = _exit_code_from_payload(nested)
            if code is not None:
                return code
    return None


def _completed_test_succeeded(event: AgentEvent) -> bool:
    if event.payload.get("is_error"):
        return False
    return _exit_code_from_payload(event.payload.get("result")) == 0


def _potential_workspace_mutation(tool: str, command: str) -> bool:
    folded_tool = str(tool or "").strip().casefold()
    if folded_tool in _MUTATING_TOOLS:
        return True
    if folded_tool in _READ_ONLY_TOOLS:
        return False
    if folded_tool in _SHELL_TOOLS:
        if _test_command(command) or _READ_ONLY_SHELL_RE.search(command) or _NON_MUTATING_VALIDATION_RE.search(command):
            return False
        return True
    # Fail closed for unknown tool families: raw-event fallback evidence is only
    # trusted when no later operation could plausibly have changed the candidate.
    return True


def _completed_tool_rows(events: Iterable[AgentEvent]) -> list[tuple[int, AgentEvent, str, str, str]]:
    ordered = list(events)
    started_by_call_id = {
        str(event.payload.get("tool_call_id") or ""): event
        for event in ordered
        if event.event_type == "tool.started" and str(event.payload.get("tool_call_id") or "")
    }
    rows: list[tuple[int, AgentEvent, str, str, str]] = []
    for index, event in enumerate(ordered):
        if event.event_type != "tool.completed":
            continue
        call_id = str(event.payload.get("tool_call_id") or event.event_id)
        started = started_by_call_id.get(call_id)
        command = _event_command(started, event)
        tool = _event_tool(started, event)
        sequence = int(event.sequence) if event.sequence is not None else index
        rows.append((sequence, event, call_id, tool, command))
    return rows


def _final_state_successful_test_rows(
    events: Iterable[AgentEvent],
    required_paths: list[str],
) -> list[tuple[AgentEvent, str, str, set[str]]]:
    completed_rows = _completed_tool_rows(events)
    last_mutation_sequence = max(
        (
            sequence
            for sequence, _event, _call_id, tool, command in completed_rows
            if _potential_workspace_mutation(tool, command)
        ),
        default=-1,
    )
    rows: list[tuple[AgentEvent, str, str, set[str]]] = []
    for sequence, event, call_id, _tool, command in completed_rows:
        if sequence < last_mutation_sequence or not _test_command(command) or not _completed_test_succeeded(event):
            continue
        coverage = _command_coverage(command, required_paths)
        if coverage:
            rows.append((event, call_id, command, coverage))
    return rows


def _raw_final_state_test_coverage(events: Iterable[AgentEvent], required_paths: list[str]) -> set[str]:
    covered: set[str] = set()
    for _event, _call_id, _command, coverage in _final_state_successful_test_rows(events, required_paths):
        covered.update(coverage)
    return covered


def reconcile_candidate_test_validation_results(
    subject_paths: Iterable[str],
    validations: Iterable[ValidationResult],
    *,
    run_id: str,
    task_revision_id: str | None,
    workspace_state_id: str,
    events: Iterable[AgentEvent],
    workspace_root: str | None = None,
    covers_requirement_ids: Iterable[str] = (),
) -> list[ValidationResult]:
    """Materialize trusted raw runner events as exact-state test evidence.

    Some supported runners historically were not recognized by
    ``validation_kind_for_command`` and therefore never became ValidationResult
    rows. Before independent review, reconcile successful final-state runner
    events that demonstrably cover run-owned test files. This makes reviewer
    evidence identical to the proof used by the deterministic pre-review gate.
    """

    required = _existing_snapshot_paths(subject_paths, workspace_root)
    if not required:
        return []
    existing = list(validations)
    existing_call_ids = {
        str(item.metadata.get("tool_call_id") or "")
        for item in existing
        if item.workspace_state_id == workspace_state_id
    }
    results: list[ValidationResult] = []
    requirement_ids = list(dict.fromkeys(str(item) for item in covers_requirement_ids if str(item)))
    for event, call_id, command, coverage in _final_state_successful_test_rows(events, required):
        if call_id in existing_call_ids:
            continue
        payload_digest = hashlib.sha256(
            json.dumps(event.payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        result_id = hashlib.sha256(
            f"candidate-test-reconcile:{run_id}:{task_revision_id}:{call_id}:{workspace_state_id}".encode("utf-8")
        ).hexdigest()
        results.append(
            ValidationResult(
                result_id=result_id,
                run_id=run_id,
                validation_id="final-state-tests",
                kind="test",
                task_revision_id=task_revision_id,
                workspace_state_id=workspace_state_id,
                command=command,
                exit_code=0,
                success=True,
                outcome="passed",
                output_digest=payload_digest,
                covers_requirement_ids=requirement_ids,
                finished_at=event.created_at,
                metadata={
                    "tool_call_id": call_id,
                    "source": "candidate_test_raw_reconciliation",
                    "candidate_test_paths": sorted(coverage),
                },
            )
        )
        existing_call_ids.add(call_id)
    return results


def missing_candidate_test_execution(
    subject_paths: Iterable[str],
    validations: Iterable[ValidationResult],
    *,
    workspace_state_id: str,
    events: Iterable[AgentEvent] = (),
    workspace_root: str | None = None,
) -> list[str]:
    """Return run-owned executable tests lacking fresh final-candidate execution evidence.

    When ``workspace_root`` is supplied it is the immutable review snapshot. Paths
    absent from that snapshot are deletions/old rename sides and therefore are not
    executable validation obligations.
    """

    required = _existing_snapshot_paths(subject_paths, workspace_root)
    if not required:
        return []

    covered: set[str] = set()
    for validation in validations:
        if (
            validation.workspace_state_id != workspace_state_id
            or validation.kind != "test"
            or not validation.success
            or validation.outcome != "passed"
        ):
            continue
        covered.update(_command_coverage(validation.command, required))

    # Older command classification did not recognize every E2E runner (notably
    # direct Playwright invocations). Preserve exact-state safety by accepting raw
    # successful commands only when they occur after the last potentially mutating
    # durable tool completion.
    covered.update(_raw_final_state_test_coverage(events, required))
    return [path for path in required if path not in covered]
