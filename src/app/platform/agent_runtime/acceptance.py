"""Omnix-authoritative acceptance checks for agent completion."""
from __future__ import annotations

from .profiles import profile_produces_diff
from .exception_logging import log_recovered_exception

from pathlib import Path
import re
import shlex
from typing import Any, Iterable, cast

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AcceptancePlan, AgentArtifact, AgentEvent, AgentRunSpec, EvidenceSet, TaskRevision
from .workspace import WorkspaceAuthority
from .event_queries import json_object


class WorkspaceInspectionError(RuntimeError):
    pass


class AcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    modified_paths: list[str] = Field(default_factory=list)


def compile_acceptance_plan(spec: AgentRunSpec, *, task_revision: TaskRevision | None = None) -> AcceptancePlan:
    if spec.acceptance_plan is not None and task_revision is None:
        return spec.acceptance_plan
    checks: list[str] = []
    criteria = task_revision.effective_success_criteria if task_revision is not None else spec.success_criteria
    descriptions = " ".join(
        item.description.casefold()
        for item in criteria
        if item.required
    )
    expected_artifacts = (
        list(task_revision.expected_artifacts)
        if task_revision is not None
        else list(spec.expected_artifacts)
    )
    mutating_code = (
        profile_produces_diff(spec.profile)
        and (
            "diff" in expected_artifacts
            if task_revision is not None
            else any(
                capability in {"workspace.edit", "workspace.write"}
                for capability in spec.capabilities
            )
        )
    )
    if "test" in descriptions or mutating_code:
        checks.append("successful_test_command")
    if "typecheck" in descriptions or "type check" in descriptions:
        checks.append("successful_typecheck_command")
    if "lint" in descriptions:
        checks.append("successful_lint_command")
    if task_revision is not None:
        checks.extend(value for value in task_revision.acceptance_checks if value not in checks)
    return AcceptancePlan(
        allowed_modified_paths=list(spec.workspace.allowed_paths if spec.workspace else ["**"]),
        forbidden_modified_paths=list(spec.workspace.forbidden_paths if spec.workspace else []),
        required_artifacts=expected_artifacts,
        require_diff="diff" in expected_artifacts,
        checks=checks,
    )


def evaluate_acceptance(
    spec: AgentRunSpec,
    *,
    events: Iterable[AgentEvent],
    artifacts: Iterable[AgentArtifact],
    task_revision: TaskRevision | None = None,
    evidence_set: EvidenceSet | None = None,
) -> AcceptanceResult:
    plan = compile_acceptance_plan(spec, task_revision=task_revision)
    event_rows = list(events)
    artifact_rows = list(artifacts)
    checks: dict[str, bool] = {}
    failures: list[str] = []
    try:
        modified_paths = _modified_paths(spec, artifact_rows)
        workspace_inspection_ok = True
    except WorkspaceInspectionError:
        modified_paths = []
        workspace_inspection_ok = False
        checks["workspace_inspection"] = False
        failures.append("workspace_inspection_failed")

    effective_policy = (
        task_revision.evidence_decision.policy
        if task_revision is not None
        else spec.evidence_policy
    )
    if effective_policy.requirement == "required":
        evidence_ok = evidence_set is not None and evidence_set.passed
        checks["required_evidence"] = evidence_ok
        if not evidence_ok:
            failures.append("evidence_requirements_unsatisfied")
    if effective_policy.user_visible_attribution == "required":
        attribution_ready = bool(
            evidence_set is not None
            and evidence_set.passed
            and evidence_set.attribution_refs
        )
        checks["user_visible_attribution_ready"] = attribution_ready
        if not attribution_ready:
            failures.append("user_visible_attribution_unavailable")

    checks["modified_paths_in_scope"] = workspace_inspection_ok and _paths_allowed(
        modified_paths,
        allowed=plan.allowed_modified_paths,
        forbidden=plan.forbidden_modified_paths,
    )
    if not checks["modified_paths_in_scope"]:
        failures.append("modified_paths_outside_scope")

    diff_artifacts: list[AgentArtifact] = []
    diff_is_nonempty = False
    if plan.require_diff:
        diff_artifacts = [item for item in artifact_rows if item.kind == "diff"]
        checks["diff_artifact"] = bool(diff_artifacts)
        if not checks["diff_artifact"]:
            failures.append("missing_diff_artifact")
        else:
            diff_is_nonempty = any(_diff_artifact_nonempty(item) for item in diff_artifacts)
            checks["nonempty_diff_artifact"] = diff_is_nonempty
            baseline_conflicts = sorted({
                str(path)
                for item in diff_artifacts
                for path in (
                    cast(list[Any], item.metadata.get("baseline_conflicts"))
                    if isinstance(item.metadata.get("baseline_conflicts"), list)
                    else []
                )
                if str(path).strip()
            })
            checks["preexisting_dirty_paths_unchanged"] = not baseline_conflicts
            if baseline_conflicts:
                failures.append("preexisting_dirty_paths_modified")

    for kind in plan.required_artifacts:
        key = f"artifact:{kind}"
        checks[key] = any(item.kind == kind for item in artifact_rows)
        if not checks[key]:
            failures.append(f"missing_artifact:{kind}")

    tool_calls = _completed_commands(event_rows)
    already_satisfied_without_diff = False
    requested_replacement = _exact_ui_label_replacement(spec, task_revision)
    exact_ui_validation = _successful_exact_ui_validation(
        event_rows,
        expected=(requested_replacement[1] if requested_replacement is not None else None),
    )

    already_satisfied_without_diff = _check_web_ui_task(plan, spec, task_revision, modified_paths, tool_calls, diff_artifacts, diff_is_nonempty, requested_replacement, exact_ui_validation, checks, failures, already_satisfied_without_diff)

    if plan.require_diff and diff_artifacts and not diff_is_nonempty and not already_satisfied_without_diff:
        failures.append("empty_diff_artifact")

    for index, required_command in enumerate(plan.required_commands, start=1):
        key = f"required_command:{index}"
        ok = any(
            success and _command_matches(command, required_command)
            for command, success in tool_calls
        )
        checks[key] = ok
        if not ok:
            failures.append(key)

    _check_required_command_kinds(plan, spec, task_revision, tool_calls, checks, failures)

    return AcceptanceResult(
        passed=not failures,
        checks=checks,
        failures=failures,
        modified_paths=modified_paths,
    )


def _check_web_ui_task(plan, spec, task_revision, modified_paths, tool_calls, diff_artifacts, diff_is_nonempty, requested_replacement, exact_ui_validation, checks, failures, already_satisfied_without_diff):
    """A web UI coding task changes UI paths, validates them in the browser, and verifies an exact requested label replacement."""
    if profile_produces_diff(spec.profile) and plan.require_diff and _is_web_ui_task(spec, task_revision):
        relevant_paths = any(_is_web_ui_path(path) for path in modified_paths)
        relevant_validation = any(
            success and _is_web_ui_validation(command)
            for command, success in tool_calls
        )
        already_satisfied_without_diff = bool(
            diff_artifacts
            and not diff_is_nonempty
            and relevant_validation
            and (
                requested_replacement is None
                or exact_ui_validation
            )
        )
        checks["already_satisfied_without_diff"] = already_satisfied_without_diff
        checks["task_relevant_modified_paths"] = relevant_paths or already_satisfied_without_diff
        if not checks["task_relevant_modified_paths"]:
            failures.append("modified_paths_not_task_relevant")
        checks["task_relevant_validation"] = relevant_validation
        if not relevant_validation:
            failures.append("validation_not_task_relevant")

        exact_replacement = requested_replacement
        if exact_replacement is not None:
            old_label, new_label = exact_replacement
            checks["requested_ui_browser_validation"] = exact_ui_validation
            if not exact_ui_validation:
                failures.append("ui_label_browser_validation_not_verified")
            diff_text = _authoritative_diff_text(
                spec,
                diff_artifacts,
                modified_paths,
            )
            replacement_verified = bool(
                (
                    diff_is_nonempty
                    and _diff_hunk_replaces_label(diff_text, old_label, new_label)
                )
                or (already_satisfied_without_diff and exact_ui_validation)
            )
            checks["requested_ui_label_replacement"] = replacement_verified
            if not replacement_verified:
                failures.append("ui_label_replacement_not_verified")
    return already_satisfied_without_diff


def _check_required_command_kinds(plan, spec, task_revision, tool_calls, checks, failures):
    """Each required command kind (test, typecheck, lint) ran successfully."""
    for requirement in plan.checks:
        if requirement == "successful_test_command":
            ok = any(
                success
                and (
                    _is_test(command)
                    or (
                        _is_web_ui_task(spec, task_revision)
                        and (
                            _is_typecheck(command)
                            or _is_web_ui_validation(command)
                        )
                    )
                )
                for command, success in tool_calls
            )
        elif requirement == "successful_typecheck_command":
            ok = any(_is_typecheck(command) and success for command, success in tool_calls)
        elif requirement == "successful_lint_command":
            ok = any(_is_lint(command) and success for command, success in tool_calls)
        else:
            ok = False
        checks[requirement] = ok
        if not ok:
            failures.append(requirement)


def _modified_paths(
    spec: AgentRunSpec,
    artifacts: list[AgentArtifact] | None = None,
) -> list[str]:
    for artifact in reversed(artifacts or []):
        if artifact.kind != "diff":
            continue
        values = artifact.metadata.get("modified_paths")
        if isinstance(values, list):
            return sorted({
                str(value).replace("\\", "/")
                for value in values
                if str(value).strip()
            })
    if spec.workspace is None:
        return []
    root = spec.workspace.worktree or spec.workspace.root
    try:
        return WorkspaceAuthority(root).git_status_paths()
    except Exception as exc:
        raise WorkspaceInspectionError(
            f"unable to inspect authoritative workspace status: {type(exc).__name__}: {exc}"
        ) from exc


def _diff_artifact_nonempty(artifact: AgentArtifact) -> bool:
    metadata = artifact.metadata
    if "byte_size" in metadata:
        try:
            if int(metadata.get("byte_size") or 0) > 0:
                return True
        except (TypeError, ValueError):
            return False
        paths = metadata.get("modified_paths")
        return isinstance(paths, list) and bool(paths)
    preview = metadata.get("preview")
    if isinstance(preview, str):
        return bool(preview.strip())
    # Compatibility for callers that supply an opaque diff artifact. Runtime-
    # created diff artifacts always include byte_size and modified_paths.
    return True


_WEB_UI_TASK = re.compile(
    r"\b(?:light\s*mode|dark\s*mode|theme|style|css|frontend|react|ui|user\s+interface|"
    r"appearance|visual|readab(?:le|ility)|contrast|text\s+color|background\s+color|"
    r"button|buttons|spacing|layout|fullscreen|header|personality)\b",
    re.I,
)
_QUOTED_UI_REPLACEMENT = re.compile(
    r"[\"'“](?P<old>[^\"'”]{1,80})[\"'”]\s*(?:->|→)\s*"
    r"[\"'“](?P<new>[^\"'”]{1,80})[\"'”]",
    re.I,
)
_SHOULD_BE_UI_REPLACEMENT = re.compile(
    r"[\"'](?P<old>[^\"']{1,80})[\"']\s+should\s+be\s+"
    r"[\"'](?P<new>[^\"']{1,80})[\"']",
    re.I,
)
_FROM_TO_UI_REPLACEMENT = re.compile(
    r"\bfrom\s+[\"'“]?(?P<old>[A-Za-z][A-Za-z0-9_-]{0,39})[\"'”]?\s+"
    r"to\s+[\"'“]?(?P<new>[A-Za-z][A-Za-z0-9_-]{0,39})[\"'”]?\b",
    re.I,
)
_NAMED_UI_REPLACEMENT = re.compile(
    r"\b(?:rename|change)\s+(?:the\s+)?(?P<old>[A-Za-z][A-Za-z0-9_-]{0,39})\s+"
    r"(?:button|label|tab|header|control)\s+(?:to|as)\s+"
    r"(?P<new>[A-Za-z][A-Za-z0-9_-]{0,39})\b",
    re.I,
)
_SIMPLE_RENAME = re.compile(
    r"\brename\s+[\"'“]?(?P<old>[A-Za-z][A-Za-z0-9_-]{0,39})[\"'”]?\s+"
    r"(?:to|as)\s+[\"'“]?(?P<new>[A-Za-z][A-Za-z0-9_-]{0,39})[\"'”]?\b",
    re.I,
)


def _effective_objective(spec: AgentRunSpec, task_revision: TaskRevision | None) -> str:
    return (
        task_revision.effective_objective
        if task_revision is not None
        else (spec.objective or spec.task)
    )


def _is_web_ui_task(spec: AgentRunSpec, task_revision: TaskRevision | None) -> bool:
    return bool(_WEB_UI_TASK.search(_effective_objective(spec, task_revision)))


def _exact_ui_label_replacement(
    spec: AgentRunSpec,
    task_revision: TaskRevision | None,
) -> tuple[str, str] | None:
    objective = _effective_objective(spec, task_revision)
    if not _WEB_UI_TASK.search(objective):
        return None
    for pattern in (
        _SHOULD_BE_UI_REPLACEMENT,
        _QUOTED_UI_REPLACEMENT,
        _NAMED_UI_REPLACEMENT,
        _FROM_TO_UI_REPLACEMENT,
        _SIMPLE_RENAME,
    ):
        match = pattern.search(objective)
        if match is None:
            continue
        old_label = match.group("old").strip()
        new_label = match.group("new").strip()
        if old_label and new_label and old_label != new_label:
            return old_label, new_label
    return None


def _authoritative_diff_text(
    spec: AgentRunSpec,
    artifacts: list[AgentArtifact],
    modified_paths: list[str],
) -> str:
    if spec.workspace is not None:
        root = spec.workspace.worktree or spec.workspace.root
        try:
            authority = WorkspaceAuthority(
                root,
                allowed_paths=list(spec.workspace.allowed_paths),
                forbidden_paths=list(spec.workspace.forbidden_paths),
            )
            return authority.git_diff(modified_paths if modified_paths else None)
        except Exception as exc:
            # Runtime-created diff artifacts are produced from the authoritative
            # workspace and retain a bounded preview for acceptance fallback.
            log_recovered_exception("acceptance diff artifact fallback", exc, level="DEBUG")
            pass
    previews = [
        str(item.metadata.get("preview") or "")
        for item in artifacts
        if item.kind == "diff" and str(item.metadata.get("preview") or "").strip()
    ]
    return "\n".join(previews)


def _diff_hunk_replaces_label(diff_text: str, old_label: str, new_label: str) -> bool:
    lines = str(diff_text or "").splitlines()
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        hunks.append(current)
    if not hunks and lines:
        hunks = [lines]

    for hunk in hunks:
        removed = any(
            line.startswith("-")
            and not line.startswith("---")
            and old_label in line[1:]
            for line in hunk
        )
        added = any(
            line.startswith("+")
            and not line.startswith("+++")
            and new_label in line[1:]
            for line in hunk
        )
        if removed and added:
            return True
    return False


def _is_web_ui_path(path: str) -> bool:
    return str(path).replace("\\", "/").casefold().startswith("web/")


def _is_web_ui_validation(command: str) -> bool:
    normalized = " ".join(str(command).casefold().split())
    normalized = normalized.replace("npx.cmd", "npx").replace("npm.cmd", "npm")
    if (
        "npx vitest" in normalized
        or "npx tsc" in normalized
        or "npm test" in normalized
        or "npm run test" in normalized
        or "npm run build" in normalized
        or "npm run web:test" in normalized
        or "npm run web:typecheck" in normalized
        or ("npm --prefix" in normalized and " typecheck" in normalized)
        or ("npm --prefix" in normalized and " test" in normalized)
        or ("npm --prefix" in normalized and " build" in normalized)
    ):
        return True
    # A pytest command that names a web/ path (PA-5.4), not any word containing "web".
    return "pytest" in normalized and re.search(r"(?:^|[\s'\"=])web/", normalized) is not None


def _successful_exact_ui_validation(
    events: list[AgentEvent],
    *,
    expected: str | None,
) -> bool:
    """Recognize the planned deterministic browser validation for exact UI text."""
    for event in events:
        if event.event_type != "quality.validation_recorded":
            continue
        payload = event.payload
        command = str(payload.get("command") or "").casefold()
        metadata = json_object(payload.get("metadata"))
        asserted = str(metadata.get("assertion_expected") or "")
        if (
            payload.get("validation_id") == "browser-validation"
            and payload.get("kind") == "browser"
            and payload.get("success") is True
            and (expected is None or asserted == expected)
            and (
                "browser.assert_text_contains" in command
                or "browser.assert_attribute_contains" in command
            )
        ):
            return True
    return False


def _paths_allowed(paths: list[str], *, allowed: list[str], forbidden: list[str]) -> bool:
    for value in paths:
        path = Path(value)
        if any(path.match(pattern) for pattern in forbidden):
            return False
        if allowed and not any(pattern == "**" or path.match(pattern) for pattern in allowed):
            return False
    return True


def _completed_commands(events: list[AgentEvent]) -> list[tuple[str, bool]]:
    starts: dict[str, str] = {}
    results: list[tuple[str, bool]] = []
    for event in events:
        if event.event_type == "tool.started":
            tool = str(event.payload.get("tool") or "")
            if tool not in {"bash", "powershell"}:
                continue
            call_id = str(event.payload.get("tool_call_id") or "")
            args = json_object(event.payload.get("args"))
            command = str(args.get("command") or "")
            if call_id and command:
                starts[call_id] = command
        elif event.event_type == "tool.completed":
            call_id = str(event.payload.get("tool_call_id") or "")
            if call_id in starts:
                success = not bool(event.payload.get("is_error"))
                result = event.payload.get("result")
                if isinstance(result, dict):
                    nested_details = result.get("details")
                    details = nested_details if isinstance(nested_details, dict) else result
                    exit_code = details.get("exitCode", details.get("exit_code"))
                    if exit_code is not None:
                        try:
                            success = success and int(exit_code) == 0
                        except (TypeError, ValueError):
                            success = False
                results.append((starts[call_id], success))
    return results


def _is_test(command: str) -> bool:
    value = command.casefold()
    return (
        "pytest" in value
        or "vitest" in value
        or "npm test" in value
        or "npm run test" in value
        or ("npm --prefix" in value and " test" in value)
    )


def _is_typecheck(command: str) -> bool:
    value = command.casefold()
    return "typecheck" in value or "tsc" in value


def _is_lint(command: str) -> bool:
    value = command.casefold()
    return "ruff" in value or " lint" in value


def _command_matches(command: str, required: list[str]) -> bool:
    expected = [str(part) for part in required]
    if not expected:
        return False
    try:
        observed = shlex.split(command, posix=True)
    except ValueError:
        observed = command.split()
    return observed == expected
