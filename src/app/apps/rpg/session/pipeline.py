"""Explicit, ordered stages for the interactive RPG turn boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Protocol

from app.apps.rpg.foundation.performance_trace import current_rpg_pipeline_trace, rpg_pipeline_span
from app.apps.rpg.foundation.debug_logging import log_rpg_event, new_rpg_trace_id, summarize_turn_result


@dataclass(slots=True)
class TurnContext:
    """Mutable data passed through one interactive turn pipeline."""

    session_id: str
    player_input: str
    action: dict[str, Any] | None
    performance_override: dict[str, Any] | None
    session_override: dict[str, Any] | None
    execute_core: Callable[[], Any]
    result: Any = None
    resolved: bool = False
    trace_id: str = field(default_factory=lambda: new_rpg_trace_id("turn"))
    degraded_stages: list[str] = field(default_factory=list)


class TurnStage(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def optional(self) -> bool: ...

    def run(self, ctx: TurnContext) -> TurnContext: ...


@dataclass(frozen=True, slots=True)
class CoreResolutionStage:
    """Run the interactive RPG resolver once at its fixed place in the order."""

    name: str = "resolve"
    optional: bool = False

    def run(self, ctx: TurnContext) -> TurnContext:
        if not ctx.resolved:
            ctx.result = ctx.execute_core()
            ctx.resolved = True
            if isinstance(ctx.result, dict):
                ctx.result.setdefault("trace_id", ctx.trace_id)
        return ctx


@dataclass(frozen=True, slots=True)
class FastVisibleDialogueStage:
    """Take the safe, non-stateful dialogue fast path before semantic routing."""

    name: str = "fast_visible_dialogue"
    optional: bool = True

    def run(self, ctx: TurnContext) -> TurnContext:
        from app.apps.rpg.session.fast_visible_dialogue_hook import try_fast_visible_dialogue

        result = try_fast_visible_dialogue(ctx)
        if result:
            ctx.result = result
            ctx.resolved = True
        return ctx


@dataclass(frozen=True, slots=True)
class DialogueQualityStage:
    """Validate the player-facing dialogue after turn resolution."""

    name: str = "dialogue_quality"
    optional: bool = True

    def run(self, ctx: TurnContext) -> TurnContext:
        from app.apps.rpg.session.dialogue_quality_hook import apply_dialogue_quality_stage

        return apply_dialogue_quality_stage(ctx)


@dataclass(frozen=True, slots=True)
class VisibleResponseStage:
    """Attach the final visible-response record after enrichment and repair."""

    name: str = "visible_response"
    optional: bool = False

    def run(self, ctx: TurnContext) -> TurnContext:
        from app.apps.rpg.session.visible_response_stage import apply_visible_response_stage

        return apply_visible_response_stage(ctx)


@dataclass(frozen=True, slots=True)
class FastCombatResultStage:
    """Restore the deterministic combat presentation selected by the runtime."""

    name: str = "fast_combat_result"
    optional: bool = False

    def run(self, ctx: TurnContext) -> TurnContext:
        if not isinstance(ctx.result, dict):
            return ctx
        from app.apps.rpg.session.interactive_fast_combat_result_hook import (
            normalize_interactive_fast_combat_result,
        )

        ctx.result = normalize_interactive_fast_combat_result(ctx.result)
        return ctx


@dataclass(frozen=True, slots=True)
class PlayerAgencyStage:
    """Attach non-authoritative next-action suggestions to the turn result."""

    name: str = "player_agency"
    optional: bool = True

    def run(self, ctx: TurnContext) -> TurnContext:
        if not isinstance(ctx.result, dict):
            return ctx
        from app.apps.rpg.session.player_agency_runtime_hook import (
            attach_player_agency_to_runtime_result,
        )

        ctx.result = attach_player_agency_to_runtime_result(
            ctx.result,
            call_context={
                "session_id": ctx.session_id,
                "player_input": ctx.player_input,
                "performance_override": ctx.performance_override,
                "session_override": ctx.session_override,
                "max_options": (ctx.performance_override or {}).get(
                    "player_agency_max_options", 5
                ),
                "enable_flavor": (ctx.performance_override or {}).get(
                    "enable_player_agency_flavor", False
                ),
            },
        )
        return ctx


@dataclass(frozen=True, slots=True)
class InteractionCommitStage:
    """Persist the interaction before any post-commit lifecycle side effects."""

    name: str = "interaction_commit"
    optional: bool = False
    commit_boundary: bool = True

    def run(self, ctx: TurnContext) -> TurnContext:
        from app.apps.rpg.session.interaction_stages import commit_interaction

        return commit_interaction(ctx)


@dataclass(frozen=True, slots=True)
class InteractionLifecycleStage:
    """Record post-commit narration lifecycle and queue optional enrichment."""

    name: str = "interaction_lifecycle"
    optional: bool = True

    def run(self, ctx: TurnContext) -> TurnContext:
        from app.apps.rpg.session.interaction_stages import initialize_lifecycle

        return initialize_lifecycle(ctx)


TURN_PIPELINE: tuple[TurnStage, ...] = (
    FastVisibleDialogueStage(),
    CoreResolutionStage(),
    FastCombatResultStage(),
    DialogueQualityStage(),
    VisibleResponseStage(),
    PlayerAgencyStage(),
    InteractionCommitStage(),
    InteractionLifecycleStage(),
)


def run_turn_pipeline(
    ctx: TurnContext,
    *,
    stages: tuple[TurnStage, ...] = TURN_PIPELINE,
) -> TurnContext:
    """Run stages in their declared order and report optional degradation."""

    commit_boundary = next(
        (index for index, stage in enumerate(stages) if getattr(stage, "commit_boundary", False)),
        None,
    )
    started_at = perf_counter()
    log_rpg_event(
        "turn.started",
        category="performance",
        session_id=ctx.session_id,
        trace_id=ctx.trace_id,
        fields={
            "player_input": ctx.player_input,
            "player_input_chars": len(str(ctx.player_input or "")),
            "action": ctx.action or {},
            "performance_override": ctx.performance_override or {},
            "session_override_present": isinstance(ctx.session_override, dict),
        },
    )
    try:
        if commit_boundary is None:
            ctx = _run_stage_sequence(ctx, stages)
        else:
            from app.persistence.runtime import uses_postgresql_runtime
            from app.apps.rpg.narration.narrative_engine.persistence_policy import (
                narrative_repository_save_policy,
            )
            from app.apps.rpg.foundation.persistence.rpg_session_save_policy import (
                rpg_session_save_policy,
            )

            defer_session_writes = uses_postgresql_runtime()
            with (
                narrative_repository_save_policy(defer=defer_session_writes),
                rpg_session_save_policy(defer=defer_session_writes),
            ):
                ctx = _run_stage_sequence(ctx, stages[: commit_boundary + 1])
            ctx = _run_stage_sequence(ctx, stages[commit_boundary + 1 :])
    except Exception as exc:
        log_rpg_event(
            "turn.exception",
            category="performance",
            level="error",
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            duration_ms=(perf_counter() - started_at) * 1000.0,
            fields={"player_input": ctx.player_input, "action": ctx.action or {}},
            error=exc,
            include_traceback=True,
        )
        raise

    summary = summarize_turn_result(ctx.result)
    turn_id = str(summary.get("turn_id") or "") or None
    ok = ctx.result.get("ok") is True if isinstance(ctx.result, dict) else False
    log_rpg_event(
        "turn.completed" if ok else "turn.failed",
        category="performance",
        level="info" if ok else "error",
        session_id=ctx.session_id,
        turn_id=turn_id,
        trace_id=ctx.trace_id,
        duration_ms=(perf_counter() - started_at) * 1000.0,
        fields={"player_input": ctx.player_input, "result": summary},
        error=(
            str(ctx.result.get("error"))
            if isinstance(ctx.result, dict) and ctx.result.get("error")
            else None
        ),
    )
    return ctx


def _run_stage_sequence(
    ctx: TurnContext,
    stages: tuple[TurnStage, ...],
) -> TurnContext:
    for stage in stages:
        try:
            with rpg_pipeline_span(
                f"turn.stage.{stage.name}",
                fields={"stage": stage.name, "optional": stage.optional},
            ):
                next_context = stage.run(ctx)
            if not isinstance(next_context, TurnContext):
                raise TypeError(f"turn stage {stage.name!r} returned a non-context value")
            ctx = next_context
        except Exception as exc:
            if not stage.optional:
                raise
            ctx.degraded_stages.append(stage.name)
            trace = current_rpg_pipeline_trace()
            if trace is not None:
                trace.fields["degraded_stages"] = list(ctx.degraded_stages)
            log_rpg_event(
                "turn.stage.degraded",
                category="performance",
                level="warning",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                fields={
                    "metric": "rpg_turn_stage_degraded",
                    "stage": stage.name,
                    "degraded_stage_count": len(ctx.degraded_stages),
                },
                error=exc,
                include_traceback=True,
            )
    return ctx


__all__ = [
    "TURN_PIPELINE",
    "CoreResolutionStage",
    "DialogueQualityStage",
    "FastCombatResultStage",
    "FastVisibleDialogueStage",
    "InteractionCommitStage",
    "InteractionLifecycleStage",
    "PlayerAgencyStage",
    "TurnContext",
    "TurnStage",
    "VisibleResponseStage",
    "run_turn_pipeline",
]
