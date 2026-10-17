"""Governed repository adapter: local preparation is separate from remote publication."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import json
from pathlib import Path
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from .models import AssistantToolRequest, AssistantToolResult


@dataclass(frozen=True)
class RepositoryPullRequestRecord:
    number: int
    title: str
    head_sha: str
    checks_passed: bool = False
    merged: bool = False


class RepositoryRuntimeAdapter(Protocol):
    def read_repo(self, *, repository: str, ref: str | None = None, requested_ref: str | None = None) -> dict[str, object]: ...
    def create_branch(self, *, repository: str, branch: str, base_sha: str) -> dict[str, object]: ...
    def push(self, *, repository: str, worktree: str, branch: str, remote: str = "origin") -> dict[str, object]: ...
    def inspect_ci(self, *, repository: str, ref: str) -> dict[str, object]: ...
    def create_pr(self, *, repository: str, title: str, body: str, branch: str, base: str) -> RepositoryPullRequestRecord: ...
    def merge_pr(self, *, repository: str, number: int, expected_head_sha: str) -> RepositoryPullRequestRecord: ...


@dataclass
class FakeRepositoryRuntimeAdapter:
    pull_requests: dict[int, RepositoryPullRequestRecord] = field(default_factory=dict)

    def read_repo(self, *, repository: str, ref: str | None = None, requested_ref: str | None = None) -> dict[str, object]:
        output: dict[str, object] = {
            "repository": repository,
            "status": "readable",
            "pull_request_count": len(self.pull_requests),
        }
        if requested_ref:
            output["requested_ref"] = requested_ref
        if ref:
            output["resolved_commit"] = ref
        return output

    def create_branch(self, *, repository: str, branch: str, base_sha: str) -> dict[str, object]:
        return {"repository": repository, "branch": branch, "base_sha": base_sha, "created": True}

    def push(self, *, repository: str, worktree: str, branch: str, remote: str = "origin") -> dict[str, object]:
        return {"repository": repository, "worktree": worktree, "branch": branch, "remote": remote, "pushed": True}

    def inspect_ci(self, *, repository: str, ref: str) -> dict[str, object]:
        return {
            "repository": repository,
            "ref": ref,
            "requested_ref": ref,
            "resolved_commit": ref,
            "status": "success",
            "checks_passed": True,
        }

    def create_pr(self, *, repository: str, title: str, body: str, branch: str, base: str) -> RepositoryPullRequestRecord:
        number = max(self.pull_requests.keys(), default=1000) + 1
        record = RepositoryPullRequestRecord(number=number, title=title, head_sha=f"fake-{uuid.uuid4().hex[:12]}")
        self.pull_requests[number] = record
        return record

    def merge_pr(self, *, repository: str, number: int, expected_head_sha: str) -> RepositoryPullRequestRecord:
        record = self.pull_requests.get(number)
        if record is None:
            record = RepositoryPullRequestRecord(number=number, title="Existing pull request", head_sha=expected_head_sha, checks_passed=True)
        if record.head_sha != expected_head_sha:
            raise ValueError("head_sha_mismatch")
        if not record.checks_passed:
            raise ValueError("checks_not_passed")
        merged = RepositoryPullRequestRecord(number=record.number, title=record.title, head_sha=record.head_sha, checks_passed=True, merged=True)
        self.pull_requests[number] = merged
        return merged


class GitHubCliRuntimeAdapter:
    """Explicitly enabled GitHub CLI publication adapter.

    Authentication remains owned by the installed gh/git clients. Omnix never
    reads or stores GitHub credentials; it only invokes a gated operation.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.gh = shutil.which("gh")
        if not self.gh:
            raise RuntimeError("GitHub CLI is not installed")
        self.timeout = timeout

    def _gh(self, args: list[str], *, timeout: float | None = None) -> dict[str, object]:
        completed = subprocess.run(
            [self.gh, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"gh_failed:{completed.stderr[-1000:]}")
        text = completed.stdout.strip()
        return json.loads(text) if text else {}

    def _resolve_commit_sha(self, *, repository: str, ref: str) -> str:
        commit = self._gh(["api", f"repos/{repository}/commits/{ref}"])
        resolved = str(commit.get("sha") or "").strip()
        if not resolved:
            raise RuntimeError("github_commit_resolution_failed")
        return resolved

    def _check_runs(self, *, repository: str, commit_sha: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        page = 1
        total_count: int | None = None
        while True:
            data = self._gh(
                [
                    "api",
                    f"repos/{repository}/commits/{commit_sha}/check-runs?per_page=100&page={page}",
                ]
            )
            batch = [
                dict(row)
                for row in data.get("check_runs") or []
                if isinstance(row, dict)
            ]
            rows.extend(batch)
            try:
                total_count = int(data.get("total_count"))
            except (TypeError, ValueError):
                total_count = None
            if not batch or len(batch) < 100 or (
                total_count is not None and len(rows) >= total_count
            ):
                break
            page += 1
            if page > 100:
                raise RuntimeError("github_check_run_pagination_exceeded")

        # Re-runs can leave older failed check-runs attached to the same commit.
        # Keep only the newest run for each check identity so stale attempts do
        # not mask a later successful authoritative result.
        latest: dict[tuple[str, str], dict[str, object]] = {}
        for row in sorted(
            rows,
            key=lambda item: int(item.get("id") or 0),
            reverse=True,
        ):
            app = row.get("app") if isinstance(row.get("app"), dict) else {}
            key = (
                str(row.get("name") or ""),
                str(app.get("slug") or app.get("id") or ""),
            )
            latest.setdefault(key, row)
        return list(latest.values())

    def _commit_statuses(self, *, repository: str, commit_sha: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        page = 1
        while True:
            data = self._gh(
                [
                    "api",
                    f"repos/{repository}/commits/{commit_sha}/statuses?per_page=100&page={page}",
                ]
            )
            if isinstance(data, list):
                batch = [dict(row) for row in data if isinstance(row, dict)]
            else:
                # Keep a defensive path for mocked adapters that wrap the list.
                batch = [
                    dict(row)
                    for row in data.get("statuses") or []
                    if isinstance(row, dict)
                ]
            rows.extend(batch)
            if not batch or len(batch) < 100:
                break
            page += 1
            if page > 100:
                raise RuntimeError("github_commit_status_pagination_exceeded")

        # The statuses endpoint is newest-first. Combined status semantics use
        # only the most recent state for each context.
        latest: dict[str, dict[str, object]] = {}
        for row in rows:
            context = str(row.get("context") or "").strip()
            if context:
                latest.setdefault(context, row)
        return list(latest.values())

    def read_repo(self, *, repository: str, ref: str | None = None, requested_ref: str | None = None) -> dict[str, object]:
        _repository_parts(repository)
        data = self._gh(["api", f"repos/{repository}"])
        output: dict[str, object] = {
            "repository": str(data.get("full_name") or repository),
            "default_branch": data.get("default_branch"),
            "visibility": data.get("visibility"),
        }
        if requested_ref:
            output["requested_ref"] = requested_ref
        if ref:
            output["resolved_commit"] = self._resolve_commit_sha(
                repository=repository,
                ref=ref,
            )
        return output

    def create_branch(self, *, repository: str, branch: str, base_sha: str) -> dict[str, object]:
        _repository_parts(repository)
        data = self._gh(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repository}/git/refs",
                "-f",
                f"ref=refs/heads/{branch}",
                "-f",
                f"sha={base_sha}",
            ]
        )
        return {"repository": repository, "branch": branch, "base_sha": base_sha, "ref": data.get("ref"), "created": True}

    def push(self, *, repository: str, worktree: str, branch: str, remote: str = "origin") -> dict[str, object]:
        expected_owner, expected_repo = _repository_parts(repository)
        if remote != "origin":
            raise ValueError("repository remote is Omnix-managed and must be origin")
        cwd = Path(worktree).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError("issued worktree does not exist")
        remote_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
        if remote_result.returncode != 0:
            raise RuntimeError("git_remote_lookup_failed")
        remote_owner, remote_repo = _github_repository_from_remote(
            remote_result.stdout.strip()
        )
        if (
            remote_owner.casefold(),
            remote_repo.casefold(),
        ) != (
            expected_owner.casefold(),
            expected_repo.casefold(),
        ):
            raise ValueError("repository_remote_mismatch")
        completed = subprocess.run(
            ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"git_push_failed:{completed.stderr[-1000:]}")
        return {"repository": repository, "branch": branch, "remote": remote, "pushed": True}

    def inspect_ci(self, *, repository: str, ref: str) -> dict[str, object]:
        _repository_parts(repository)
        requested_ref = str(ref or "").strip()
        if not requested_ref:
            raise ValueError("repository CI inspection requires a ref")
        resolved_commit = self._resolve_commit_sha(
            repository=repository,
            ref=requested_ref,
        )
        checks = self._check_runs(
            repository=repository,
            commit_sha=resolved_commit,
        )
        statuses = self._commit_statuses(
            repository=repository,
            commit_sha=resolved_commit,
        )

        checks_passed = all(
            str(row.get("status") or "").casefold() == "completed"
            and str(row.get("conclusion") or "").casefold() == "success"
            for row in checks
        )
        statuses_passed = all(
            str(row.get("state") or "").casefold() == "success"
            for row in statuses
        )
        passed = bool(checks or statuses) and checks_passed and statuses_passed
        return {
            "repository": repository,
            "ref": requested_ref,
            "requested_ref": requested_ref,
            "resolved_commit": resolved_commit,
            "checks_passed": passed,
            "checks": [
                {
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "conclusion": row.get("conclusion"),
                }
                for row in checks
            ],
            "statuses": [
                {
                    "context": row.get("context"),
                    "state": row.get("state"),
                    "description": row.get("description"),
                }
                for row in statuses
            ],
        }

    def create_pr(self, *, repository: str, title: str, body: str, branch: str, base: str) -> RepositoryPullRequestRecord:
        _repository_parts(repository)
        data = self._gh(
            [
                "api",
                "--method",
                "POST",
                f"repos/{repository}/pulls",
                "-f",
                f"title={title}",
                "-f",
                f"body={body}",
                "-f",
                f"head={branch}",
                "-f",
                f"base={base}",
            ]
        )
        head = data.get("head") if isinstance(data.get("head"), dict) else {}
        return RepositoryPullRequestRecord(
            number=int(data["number"]),
            title=str(data.get("title") or title),
            head_sha=str(head.get("sha") or ""),
            checks_passed=False,
            merged=bool(data.get("merged")),
        )

    def merge_pr(self, *, repository: str, number: int, expected_head_sha: str) -> RepositoryPullRequestRecord:
        _repository_parts(repository)
        pr = self._gh(["api", f"repos/{repository}/pulls/{number}"])
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        head_sha = str(head.get("sha") or "")
        if head_sha != expected_head_sha:
            raise ValueError("head_sha_mismatch")
        ci = self.inspect_ci(repository=repository, ref=head_sha)
        if not bool(ci.get("checks_passed")):
            raise ValueError("checks_not_passed")
        merged = self._gh(
            [
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/pulls/{number}/merge",
                "-f",
                f"sha={expected_head_sha}",
                "-f",
                "merge_method=merge",
            ]
        )
        if not bool(merged.get("merged")):
            raise RuntimeError(str(merged.get("message") or "merge_failed"))
        return RepositoryPullRequestRecord(
            number=number,
            title=str(pr.get("title") or ""),
            head_sha=head_sha,
            checks_passed=True,
            merged=True,
        )


def get_repository_runtime_adapter() -> RepositoryRuntimeAdapter:
    if (_env_str("OMNIX_GITHUB_REAL_ADAPTER") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return GitHubCliRuntimeAdapter()
    raise RuntimeError("github_runtime_adapter_unavailable")


def run_repository_tool_request(request: AssistantToolRequest, adapter: RepositoryRuntimeAdapter | None = None) -> AssistantToolResult:
    repository = str(request.input.get("repository") or request.input.get("repo") or "")
    try:
        runtime = adapter or get_repository_runtime_adapter()
        if request.action_id == "github.read_repo":
            return _result(
                request,
                "low",
                False,
                "Read repository.",
                runtime.read_repo(
                    repository=repository,
                    ref=str(request.input.get("ref") or "") or None,
                    requested_ref=str(request.input.get("requested_ref") or "") or None,
                ),
            )
        if request.action_id == "github.create_branch":
            output = runtime.create_branch(repository=repository, branch=str(request.input.get("branch") or ""), base_sha=str(request.input.get("base_sha") or ""))
            return _result(request, "medium", True, f"Created repository branch {output.get('branch')}.", output)
        if request.action_id == "github.push":
            output = runtime.push(repository=repository, worktree=str(request.input.get("worktree") or ""), branch=str(request.input.get("branch") or ""), remote=str(request.input.get("remote") or "origin"))
            return _result(request, "medium", True, f"Pushed branch {output.get('branch')}.", output)
        if request.action_id == "github.inspect_ci":
            output = runtime.inspect_ci(repository=repository, ref=str(request.input.get("ref") or request.input.get("sha") or ""))
            return _result(request, "low", False, "Inspected repository CI.", output)
        if request.action_id == "github.create_pr":
            record = runtime.create_pr(repository=repository, title=str(request.input.get("title") or "Prepared change"), body=str(request.input.get("body") or ""), branch=str(request.input.get("branch") or ""), base=str(request.input.get("base") or "main"))
            return _result(request, "medium", True, f"Opened pull request #{record.number}.", {"pull_request": record.__dict__})
        if request.action_id == "github.merge_pr":
            record = runtime.merge_pr(repository=repository, number=int(request.input.get("number") or 0), expected_head_sha=str(request.input.get("expected_head_sha") or ""))
            return _result(request, "high", True, f"Merged pull request #{record.number}.", {"pull_request": record.__dict__})
    except (ValueError, RuntimeError) as exc:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            error=str(exc)[:500],
            result_summary="Repository action was blocked or failed at the governed publication boundary.",
        )
    return AssistantToolResult(tool_id=request.tool_id, action_id=request.action_id, session_id=request.session_id, error="repository_action_not_available")


def _result(request: AssistantToolRequest, risk: str, changed: bool, summary: str, output: dict[str, object]) -> AssistantToolResult:
    return AssistantToolResult(
        tool_id=request.tool_id,
        action_id=request.action_id,
        session_id=request.session_id,
        risk_level=risk,
        state_changed=changed,
        result_summary=summary,
        output=output,
    )


def _github_repository_from_remote(remote_url: str) -> tuple[str, str]:
    value = str(remote_url or "").strip()
    if value.startswith("git@github.com:"):
        return _repository_parts(value.removeprefix("git@github.com:"))
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "ssh", "git"} or (
        parsed.hostname or ""
    ).casefold() != "github.com":
        raise ValueError("repository remote must be hosted on github.com")
    return _repository_parts(parsed.path.strip("/"))


def _repository_parts(repository: str) -> tuple[str, str]:
    value = str(repository or "").strip()
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/")
    value = value.strip("/").removesuffix(".git")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must be owner/name")
    return parts[0], parts[1]


# Public name for the agent runtime's workspace setup (assistant_tools.contracts).
github_repository_from_remote = _github_repository_from_remote
