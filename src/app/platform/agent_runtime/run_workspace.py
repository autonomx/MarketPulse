"""Run workspaces: preparation, baselines, diff capture, promotion and repository binding (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .profiles import profile_produces_diff
from .event_queries import events_of_types
from .exception_logging import log_recovered_exception
from app.config.env import env_str as _env_str
import hashlib
import json
import subprocess
from pathlib import Path
import tempfile
from app.platform.assistant_tools.contracts import github_repository_from_remote
from .isolation import run_mutates
from .request_policy import allowed_workspace_root
from .contracts import (
    AgentArtifact,
    AgentEvent,
    AgentRunSnapshot,
    AgentRunSpec,
    EvidencePolicy,
    EvidenceRequirement,
    ResourceScope,
    SubjectRef,
    RunChangeSet,
    WorkspaceSpec,
)
from app.observability.agent_logging import log_agent_activity
from .repository import PostgresAgentRunRepository
from .workspace import WorkspaceAuthority
from .run_change_set import baseline_identity, patch_structure, run_change_set_from_artifact
from .workspace_promotion import WorkspacePromotionError, promote_change_set
from .workspace_dependencies import prepare_project_dependencies
from typing import Any, TYPE_CHECKING
from .service_core import (
    _diff_file_stats,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.service_core import AgentRunService


def _capture_workspace_baseline(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    spec: AgentRunSpec,
) -> None:
    if spec.workspace is None or "diff" not in spec.expected_artifacts:
        return
    root = spec.workspace.worktree or spec.workspace.root
    baseline = service.workspace_authority_factory(root).provenance_snapshot()
    dirty_paths = list(baseline["dirty_paths"])
    dirty_digests = {str(key): str(value) for key, value in dict(baseline["dirty_digests"]).items()}
    baseline_id = baseline_identity(str(baseline["head"]), dirty_paths, dirty_digests)
    repository.add_artifact(
        AgentArtifact(
            artifact_id=f"baseline-{baseline_id}",
            run_id=spec.run_id,
            kind="other",
            name="workspace-baseline.json",
            metadata={
                "baseline_id": baseline_id,
                "head": baseline["head"],
                "dirty_paths": dirty_paths,
                "dirty_digests": dirty_digests,
            },
        )
    )


def _quarantine_isolated_workspace_contamination(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    spec: AgentRunSpec,
    *,
    authority: WorkspaceAuthority | None = None,
) -> list[dict[str, str]]:
    workspace = spec.workspace
    if workspace is None or not workspace.worktree:
        return []
    worktree_root = Path(workspace.worktree).expanduser().resolve()
    repository_root = Path(workspace.repository or workspace.root).expanduser().resolve()
    if worktree_root == repository_root:
        return []
    workspace_authority = authority or service.workspace_authority_factory(worktree_root)
    quarantined = workspace_authority.quarantine_generated_windows_cache_contamination()
    if not quarantined:
        return []
    repository.append_event(
        AgentEvent(
            run_id=spec.run_id,
            event_type="run.status",
            payload={
                "status": "workspace_contamination_quarantined",
                "artifacts": quarantined,
            },
        )
    )
    log_agent_activity(
        "service.workspace.contamination_quarantined",
        category="quality",
        level="warning",
        run_id=spec.run_id,
        fields={"workspace": str(worktree_root), "artifacts": quarantined},
    )
    return quarantined


def _capture_diff(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    spec: AgentRunSpec,
    *,
    task_revision_id: str | None = None,
    workspace_state_id: str | None = None,
) -> RunChangeSet | None:
    """Capture/reuse the canonical baseline-relative RunChangeSet.

    The exact checkout remains represented by WorkspaceState. This artifact
    contains only run-owned paths; baseline-dirty paths are context and any
    attempted mutation of them is reported separately as a baseline conflict.
    """
    if spec.workspace is None:
        return None
    root = spec.workspace.worktree or spec.workspace.root
    try:
        authority = service.workspace_authority_factory(root)
        baseline_artifact = next(
            (
                artifact
                for artifact in repository.list_artifacts(spec.run_id)
                if artifact.name == "workspace-baseline.json"
            ),
            None,
        )
        if baseline_artifact is None:
            log_agent_activity(
                "service.diff_capture.skipped_no_baseline",
                category="quality",
                level="warning",
                run_id=spec.run_id,
                fields={"workspace": str(root), "task_revision_id": task_revision_id},
            )
            return None
        baseline_metadata = baseline_artifact.metadata
        dirty_digests, dirty_paths = _baseline_dirty_state(baseline_metadata)
        head = str(baseline_metadata.get("head") or authority.git_head())
        baseline_id = str(baseline_metadata.get("baseline_id") or baseline_identity(head, dirty_paths, dirty_digests))
        service._quarantine_isolated_workspace_contamination(
            repository,
            spec,
            authority=authority,
        )
        status_entries = authority.git_status_entries()
        modified_paths = authority.run_owned_paths(dirty_paths)
        baseline_conflicts = authority.baseline_conflicts(dirty_digests)
        tracked_patch = authority.git_tracked_diff(modified_paths)
        untracked_paths = [path for path in modified_paths if status_entries.get(path) == "??"]
        untracked_patch = authority.git_diff(untracked_paths) if untracked_paths else ""
        patch = tracked_patch + untracked_patch
        tracked_digest = hashlib.sha256(tracked_patch.encode("utf-8")).hexdigest()
        untracked_digests = {path: authority.file_digest(path) for path in untracked_paths}
        candidate_id = str(workspace_state_id or hashlib.sha256(
            f"{authority.git_head()}:{hashlib.sha256(patch.encode('utf-8')).hexdigest()}".encode("utf-8")
        ).hexdigest())
        identity_payload = {
            "run_id": spec.run_id,
            "task_revision_id": task_revision_id,
            "baseline_id": baseline_id,
            "candidate_workspace_state_id": candidate_id,
            "run_owned_paths": modified_paths,
            "tracked_patch_sha256": tracked_digest,
            "untracked_digests": untracked_digests,
            "baseline_conflicts": baseline_conflicts,
        }
        change_set_id = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for artifact in reversed(repository.list_artifacts(spec.run_id)):
            existing = run_change_set_from_artifact(artifact)
            if existing is not None and existing.change_set_id == change_set_id:
                return existing
    except Exception as exc:
        log_agent_activity(
            "service.diff_capture.failed",
            category="quality",
            level="error",
            run_id=spec.run_id,
            fields={"workspace": str(root)},
            error=exc,
            include_traceback=True,
        )
        return None

    base_key, untracked_manifest = _upload_untracked_files(service, spec, change_set_id, untracked_digests, authority)

    patch_blob = service.blob_store.put_bytes(f"{base_key}/run-owned.patch", patch.encode("utf-8"))
    deletions, renames, mode_changes = patch_structure(tracked_patch)
    change_set = RunChangeSet(
        change_set_id=change_set_id,
        run_id=spec.run_id,
        task_revision_id=task_revision_id,
        baseline_id=baseline_id,
        baseline_head_sha=head,
        candidate_workspace_state_id=candidate_id,
        run_owned_paths=modified_paths,
        baseline_context_paths=dirty_paths,
        tracked_patch_sha256=tracked_digest,
        patch_checksum=str(patch_blob["checksum_sha256"]),
        patch_storage_ref=str(patch_blob["storage_key"]),
        untracked_manifest=untracked_manifest,
        deletions=deletions,
        renames=renames,
        mode_changes=mode_changes,
        baseline_conflicts=baseline_conflicts,
    )
    preview_limit = 16_000
    file_stats = _diff_file_stats(patch, modified_paths)
    repository.add_artifact(
        AgentArtifact(
            artifact_id=change_set_id,
            run_id=spec.run_id,
            kind="diff",
            name="run-change-set.patch",
            storage_ref=change_set.patch_storage_ref,
            checksum=change_set.patch_checksum,
            metadata={
                "task_revision_id": task_revision_id,
                "workspace_state_id": candidate_id,
                "run_change_set_id": change_set_id,
                "run_change_set": change_set.model_dump(mode="json"),
                "storage_provider": str(patch_blob["storage_provider"]),
                "byte_size": int(patch_blob["byte_size"]),
                "preview": patch[:preview_limit],
                "truncated": len(patch) > preview_limit,
                "modified_paths": modified_paths,
                "file_stats": file_stats,
                "additions": sum(item["additions"] for item in file_stats),
                "deletions": sum(item["deletions"] for item in file_stats),
                "baseline_conflicts": baseline_conflicts,
                "baseline_id": baseline_id,
            },
        )
    )
    return change_set


def _baseline_dirty_state(baseline_metadata):
    """The baseline's dirty paths and their content digests, with forward slashes."""
    dirty_paths = [
        str(path).replace(chr(92), "/")
        for path in (
            baseline_metadata.get("dirty_paths")
            if isinstance(baseline_metadata.get("dirty_paths"), list)
            else []
        )
    ]
    dirty_digests = {
        str(key).replace(chr(92), "/"): str(value)
        for key, value in (
            baseline_metadata.get("dirty_digests")
            if isinstance(baseline_metadata.get("dirty_digests"), dict)
            else {}
        ).items()
    }
    return dirty_digests, dirty_paths


def _upload_untracked_files(service, spec, change_set_id, untracked_digests, authority):
    """Store each untracked run-owned file's content under the change set; record its digest and storage key."""
    workspace_key = hashlib.sha256(service.context.workspace_id.encode("utf-8")).hexdigest()[:16]
    run_key = hashlib.sha256(spec.run_id.encode("utf-8")).hexdigest()
    base_key = f"agent/runs/{workspace_key}/{run_key}/changesets/{change_set_id}"
    untracked_manifest: dict[str, dict[str, Any]] = {}
    for relative, digest in untracked_digests.items():
        entry: dict[str, Any] = {"sha256": digest, "content_storage_ref": None}
        try:
            source = authority.resolve_path(relative)
            if source.is_file():
                content_blob = service.blob_store.put_bytes(
                    f"{base_key}/untracked/{hashlib.sha256(relative.encode('utf-8')).hexdigest()}.bin",
                    source.read_bytes(),
                )
                entry["content_storage_ref"] = str(content_blob["storage_key"])
        except Exception as exc:
            log_recovered_exception("workspace artifact content upload", exc)
            entry["content_storage_ref"] = None
        untracked_manifest[relative] = entry
    return base_key, untracked_manifest


def _github_origin_repository(repository: str) -> str:
    root = Path(repository).expanduser().resolve()
    completed = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise ValueError("github authority requires a readable origin remote")
    owner, name = github_repository_from_remote(completed.stdout.strip())
    return f"{owner}/{name}"


def _resolve_repository_commit(repository: str, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(Path(repository).expanduser().resolve()), "rev-parse", ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValueError(f"unable to resolve repository ref: {ref}")
    return completed.stdout.strip()


def _bind_repository_evidence_policy(
    cls,
    policy: EvidencePolicy,
    *,
    workspace: WorkspaceSpec,
    repository_name: str,
) -> EvidencePolicy:
    if not any(
        requirement.source_class in {"repo_ci_state", "repo_contents"}
        for requirement in policy.requirements
    ):
        return policy
    resolved_commit = cls._resolve_repository_commit(
        workspace.repository or workspace.root,
        workspace.base_ref,
    )
    requirements: list[EvidenceRequirement] = []
    for requirement in policy.requirements:
        if requirement.source_class not in {"repo_ci_state", "repo_contents"}:
            requirements.append(requirement)
            continue
        prior = requirement.subject
        qualifiers = dict(prior.qualifiers if prior else {})
        qualifiers.update({
            "requested_ref": workspace.base_ref,
            "resolved_commit": resolved_commit,
        })
        requirements.append(requirement.model_copy(update={
            "subject": SubjectRef(
                type="repository_ref",
                canonical_id=repository_name,
                display_name=repository_name,
                qualifiers=qualifiers,
            )
        }))
    return policy.model_copy(update={"requirements": requirements})


def _bind_github_repository_authority(
    cls,
    spec: AgentRunSpec,
) -> AgentRunSpec:
    github_capabilities = {
        capability
        for capability in spec.external_capabilities
        if capability.startswith("github.")
    }
    if not github_capabilities:
        return spec
    workspace = spec.workspace
    if workspace is None or not workspace.repository:
        raise ValueError(
            "GitHub capabilities require a repository-backed workspace"
        )
    repository = cls._github_origin_repository(workspace.repository)
    scopes: list[ResourceScope] = []
    explicitly_scoped: set[str] = set()
    for scope in spec.resource_scopes:
        if scope.capability not in github_capabilities:
            scopes.append(scope)
            continue
        if (
            scope.resource_type.casefold() not in {"repository", "repo"}
            or scope.resource_id.casefold() != repository.casefold()
        ):
            raise ValueError(
                f"GitHub resource scope exceeds issued repository: {scope.capability}"
            )
        explicitly_scoped.add(scope.capability)
        scopes.append(
            scope.model_copy(
                update={
                    "resource_type": "repository",
                    "resource_id": repository,
                }
            )
        )
    for capability in sorted(github_capabilities - explicitly_scoped):
        scopes.append(
            ResourceScope(
                capability=capability,
                resource_type="repository",
                resource_id=repository,
            )
        )
    bound_policy = cls._bind_repository_evidence_policy(
        spec.evidence_policy,
        workspace=workspace,
        repository_name=repository,
    )
    return spec.model_copy(update={
        "resource_scopes": scopes,
        "evidence_policy": bound_policy,
    })


def _promote_accepted_workspace(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    *,
    task_revision_id: str | None,
    workspace_state_id: str | None,
) -> dict[str, Any] | None:
    """Adopt an accepted isolated coding candidate into its main checkout."""

    spec = current.spec
    workspace = spec.workspace
    if (
        not profile_produces_diff(spec.profile)
        or "diff" not in spec.expected_artifacts
        or workspace is None
        or not workspace.repository
        or not workspace.worktree
    ):
        return None
    source = Path(workspace.worktree).expanduser().resolve()
    target = Path(workspace.repository).expanduser().resolve()
    if source == target:
        return {"status": "already_in_main", "paths": []}

    for event in reversed(events_of_types(repository, current.run_id, {"run.completed"})):
        marker = event.payload.get("workspace_promotion")
        if isinstance(marker, dict) and marker.get("change_set_id"):
            return dict(marker)

    change_set = None
    for artifact in reversed(repository.list_artifacts(current.run_id)):
        change_set = run_change_set_from_artifact(artifact)
        if change_set is not None:
            break
    if change_set is None:
        raise WorkspacePromotionError("accepted run change set is unavailable")
    if change_set.task_revision_id != task_revision_id:
        raise WorkspacePromotionError("accepted run change set revision is stale")
    if change_set.candidate_workspace_state_id != workspace_state_id:
        raise WorkspacePromotionError("accepted run change set workspace state is stale")
    try:
        patch = service.blob_store.read_bytes(
            change_set.patch_storage_ref,
            expected_checksum=change_set.patch_checksum,
        ).decode("utf-8")
    except Exception as exc:
        raise WorkspacePromotionError(f"accepted run change set blob is unavailable: {exc}") from exc
    result = promote_change_set(
        source_root=source,
        target_root=target,
        change_set=change_set,
        patch=patch,
    )
    return {
        "change_set_id": change_set.change_set_id,
        "status": result.status,
        "source_workspace": str(source),
        "target_workspace": str(target),
        "target_head_sha": result.target_head_sha,
        "paths": list(result.paths),
    }


def workspace_preview_launcher(spec: AgentRunSpec):
    """How a run's workspace preview starts: inside the sandbox when the run is sandboxed.

    Returns a ``launch(root=..., package=..., port=...)`` callable giving
    ``(process, cleanup)``, where ``cleanup()`` removes the preview's
    containers, or None for a preview on the host (a run the operator let
    go unsandboxed) (WP-4.7).
    """
    from .isolation import plan_isolation, remove_containers, start_sandboxed_preview

    if not plan_isolation(spec).sandboxed:
        return None

    def launch(*, root, package, port):
        process, containers = start_sandboxed_preview(spec, root=root, package=package, port=port)
        return process, lambda: remove_containers(containers)

    return launch


def _prepare_workspace(service: AgentRunService, spec: AgentRunSpec) -> AgentRunSpec:
    workspace = spec.workspace
    if workspace is None:
        # Read-only research and other non-workspace profiles retain an
        # explicit None workspace. PiRpcSession supplies an ephemeral cwd
        # without turning it into repository authority.
        return spec
    root = Path(
        _env_str(
            "OMNIX_AGENT_WORKTREE_ROOT",
            str(Path(tempfile.gettempdir()) / "omnix-agent-worktrees"),
        )
    ).expanduser().resolve()
    if not workspace.repository or workspace.worktree:
        # In place: a run that changes this folder needs it allow-listed
        # (or an Omnix-managed worktree) (WP-4.7).
        in_place = Path(workspace.worktree or workspace.root).expanduser().resolve()
        if run_mutates(spec) and not in_place.is_relative_to(root):
            allowed_workspace_root(str(in_place))
        return spec
    root.mkdir(parents=True, exist_ok=True)
    target = root / spec.run_id
    authority = service.workspace_authority_factory.create_worktree(
        workspace.repository,
        target,
        base_ref=workspace.base_ref,
    )
    try:
        prepare_project_dependencies(repository=workspace.repository, worktree=authority.root)
    except Exception:
        try:
            service.workspace_authority_factory.remove_worktree(workspace.repository, authority.root)
        except Exception as exc:
            # Preserve the actionable dependency error; the supervisor can
            # reconcile an orphaned temporary worktree on its next pass.
            log_recovered_exception("temporary worktree cleanup", exc)
            pass
        raise
    issued_workspace = workspace.model_copy(update={"root": str(authority.root), "worktree": str(authority.root)})
    return spec.model_copy(update={"workspace": issued_workspace})
