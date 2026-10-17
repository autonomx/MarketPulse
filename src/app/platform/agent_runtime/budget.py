"""Durable fail-closed resource budgets for generalized agent runs."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from app.caching.bounded_cache import bounded_lru_cache

from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

from .profiles import profile_produces_diff
from .contracts import AgentEvent, AgentRunSnapshot
from .repository import PostgresAgentRunRepository
from .resource_grants import PostgresResourceGrantRepository
from .run_repository_queries import PostgresAgentRunQueries
from typing import Any, cast

logger = logging.getLogger(__name__)

_ZERO_COST_PROVIDERS = {"lmstudio", "llamacpp", "chatgpt_codex"}


def _agent_run_settings():
    from app.settings.effective_defaults import agent_run_settings

    return agent_run_settings()


def provider_price(provider_id: str | None):
    """The configured price of a provider, or ``None`` when its cost cannot be metered."""
    provider = normalize_budget_provider_id(provider_id or "")
    try:
        return _agent_run_settings().provider_prices.get(provider)
    except Exception:  # settings unreadable: treat the provider as unpriced
        logger.warning("agent_budget_settings_unavailable", exc_info=True)
        return None


def apply_default_run_limits(spec):
    """Fill a run's unset token and cost limits from the settings' defaults."""
    try:
        settings = _agent_run_settings()
    except Exception:
        logger.warning("agent_budget_settings_unavailable", exc_info=True)
        return spec
    updates: dict[str, Any] = {}
    if spec.limits.max_tokens is None and settings.default_max_output_tokens is not None:
        updates["max_tokens"] = settings.default_max_output_tokens
    if spec.limits.max_cost is None and settings.default_max_cost_usd is not None:
        updates["max_cost"] = settings.default_max_cost_usd
    if not updates:
        return spec
    return spec.model_copy(update={"limits": spec.limits.model_copy(update=updates)})
_TERMINAL = {"completed", "failed", "cancelled"}


class AgentBudgetError(RuntimeError):
    pass


def normalize_budget_provider_id(provider_id: str) -> str:
    value = str(provider_id or "").strip().casefold()
    return value.removeprefix("llm:")


class AgentBudgetManager:
    context = RequestTenant()
    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        context: TenantContext | None = None,
    ) -> None:
        self.database = database or default_database()
        self.context = context

    def usage(self, run_id: str) -> dict[str, Any]:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            row = repository.get_usage(run_id)
            work.rollback()
        return row

    def remaining_output_tokens(self, run_id: str) -> int | None:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            if snapshot is None:
                raise KeyError(run_id)
            usage = repository.get_usage(run_id)
            maximum = self._effective_limits(repository, snapshot)["max_tokens"]
            work.rollback()
        if maximum is None:
            return None
        return max(0, int(maximum) - int(usage["output_tokens"]))

    def token_metering_required(self, run_id: str) -> bool:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            work.rollback()
        if snapshot is None:
            raise KeyError(run_id)
        return snapshot.spec.limits.max_tokens is not None

    def authorize_model_call(
        self,
        run_id: str,
        *,
        provider_id: str,
    ) -> dict[str, Any]:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            self._lock_run(repository, run_id)
            snapshot = self._require_runnable(repository, run_id)
            effective = self._effective_limits(repository, snapshot)
            if self._wall_time_exceeded(snapshot):
                reason = "budget_max_wall_time_exceeded"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            provider = normalize_budget_provider_id(provider_id)
            if (
                snapshot.spec.limits.max_cost is not None
                and provider not in _ZERO_COST_PROVIDERS
                and provider_price(provider) is None
            ):
                reason = f"budget_cost_unmeterable_provider:{provider or 'unknown'}"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            current = repository.get_usage(run_id)
            if (
                effective["max_tokens"] is not None
                and int(current["output_tokens"]) >= int(effective["max_tokens"])
            ):
                reason = "budget_output_tokens_exhausted"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            usage = repository.consume_usage(
                run_id,
                steps=1,
                model_calls=1,
                max_steps=int(cast(int, effective["max_steps"])),
            )
            if usage is None:
                reason = "budget_max_steps_exceeded"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            work.commit()
            return usage

    def authorize_tool_call(
        self,
        run_id: str,
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        del tool_name
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            self._lock_run(repository, run_id)
            snapshot = self._require_runnable(repository, run_id)
            effective = self._effective_limits(repository, snapshot)
            if self._wall_time_exceeded(snapshot):
                reason = "budget_max_wall_time_exceeded"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            usage = repository.consume_usage(
                run_id,
                tool_calls=1,
                max_tool_calls=int(cast(int, effective["max_tool_calls"])),
            )
            if usage is None:
                reason = "budget_max_tool_calls_exceeded"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            work.commit()
            return usage

    def record_token_usage(
        self,
        run_id: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_id: str | None = None,
    ) -> dict[str, Any]:
        if input_tokens is not None and input_tokens < 0:
            raise ValueError("token usage must be non-negative")
        if output_tokens is not None and output_tokens < 0:
            raise ValueError("token usage must be non-negative")
        if input_tokens is None and output_tokens is None:
            return self.usage(run_id)
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            self._lock_run(repository, run_id)
            snapshot = repository.get_run(run_id)
            if snapshot is None:
                raise KeyError(run_id)
            effective = self._effective_limits(repository, snapshot)
            price = provider_price(provider_id) if provider_id else None
            cost = 0.0
            if price is not None:
                cost = ((input_tokens or 0) * price.input_usd_per_million
                        + (output_tokens or 0) * price.output_usd_per_million) / 1_000_000
            max_tokens = int(effective["max_tokens"]) if effective["max_tokens"] is not None else None
            before = repository.get_usage(run_id)
            usage = repository.consume_usage(
                run_id,
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                input_tokens_reported=input_tokens is not None,
                output_tokens_reported=output_tokens is not None,
                cost=cost,
                max_output_tokens=max_tokens,
                max_cost=float(effective["max_cost"]) if effective["max_cost"] is not None else None,
            )
            if usage is None:
                tokens_over = max_tokens is not None and int(before["output_tokens"]) + (output_tokens or 0) > max_tokens
                reason = "budget_max_output_tokens_exceeded" if tokens_over else "budget_cost_exhausted"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            work.commit()
            return usage

    def record_output_tokens(self, run_id: str, tokens: int) -> dict[str, Any]:
        return self.record_token_usage(run_id, output_tokens=tokens)

    def enforce_wall_time(self, run_id: str) -> None:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            if snapshot is None:
                work.rollback()
                raise KeyError(run_id)
            if snapshot.status in _TERMINAL:
                work.rollback()
                return
            if self._wall_time_exceeded(snapshot):
                reason = "budget_max_wall_time_exceeded"
                self._fail_locked(repository, snapshot, reason)
                work.commit()
                raise AgentBudgetError(reason)
            work.rollback()

    def fail(self, run_id: str, reason: str) -> None:
        with unit_of_work(self.database) as work:
            repository = PostgresAgentRunRepository(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            if snapshot is not None:
                self._fail_locked(repository, snapshot, reason)
            work.commit()

    @staticmethod
    def _lock_run(
        repository: PostgresAgentRunRepository,
        run_id: str,
    ) -> None:
        row = PostgresAgentRunQueries(repository.connection, repository.context).lock_run(run_id).fetchone()
        if row is None:
            raise KeyError(run_id)

    @staticmethod
    def _quality_state(
        repository: PostgresAgentRunRepository,
        snapshot: AgentRunSnapshot,
    ) -> tuple[str | None, int]:
        try:
            row = PostgresAgentRunQueries(repository.connection, repository.context).quality_stage(snapshot.run_id).fetchone()
        except Exception:
            return None, 1
        return (str(row[0]), max(1, int(row[1] or 1))) if row else (None, 1)

    @classmethod
    def _quality_reserve(
        cls,
        repository: PostgresAgentRunRepository,
        snapshot: AgentRunSnapshot,
    ) -> dict[str, int | float]:
        """Protect the first acceptance-repair envelope.

        Independent review is disabled for coding runs. During the first
        implementation attempt, preserve a small repair envelope so a
        substantive deterministic acceptance finding can still be acted on.
        Once repair has begun that extra envelope has served its purpose.
        """

        spec = snapshot.spec
        if (
            not profile_produces_diff(spec.profile)
            or "diff" not in spec.expected_artifacts
            or spec.quality_policy == "off"
        ):
            return {"steps": 0, "tools": 0, "tokens": 0, "cost": 0.0}
        stage, attempt = cls._quality_state(repository, snapshot)
        # Legacy reviewer runs still release the quality reserve if they are
        # encountered during a rolling deployment or recovery.
        if stage in {"reviewing", "acceptance"}:
            return {"steps": 0, "tools": 0, "tokens": 0, "cost": 0.0}

        review_fraction = 0.0
        repair_fraction = 0.10 if attempt <= 1 and stage != "repairing" else 0.0
        limits = spec.limits

        def reserve_int(maximum: int | None) -> int:
            if maximum is None:
                return 0
            value = 0
            if review_fraction:
                value += max(1, int(maximum * review_fraction))
            if repair_fraction:
                value += max(1, int(maximum * repair_fraction))
            return min(maximum, value)

        def reserve_cost(maximum: float | None) -> float:
            if maximum is None:
                return 0.0
            return min(
                float(maximum),
                float(maximum) * review_fraction + float(maximum) * repair_fraction,
            )

        return {
            "steps": reserve_int(limits.max_steps),
            "tools": reserve_int(limits.max_tool_calls),
            "tokens": reserve_int(limits.max_tokens),
            "cost": reserve_cost(limits.max_cost),
        }

    @classmethod
    def _effective_limits(
        cls,
        repository: PostgresAgentRunRepository,
        snapshot: AgentRunSnapshot,
    ) -> dict[str, int | float | None]:
        grants = PostgresResourceGrantRepository(repository.connection, repository.context)
        quality = cls._quality_reserve(repository, snapshot)
        effective = grants.own_effective_limits(snapshot, quality_reserve=quality)

        # Fail closed for direct children created before migration 0065 or by an
        # older worker during rolling deployment. New children always receive a
        # durable grant, but ungranted legacy children must not become free work.
        granted_ids = {grant.child_run_id for grant in grants.list_direct(snapshot.run_id)}
        legacy_children = [
            child for child in repository.list_children(snapshot.run_id)
            if child.run_id not in granted_ids
        ]
        legacy_steps = 0
        legacy_tools = 0
        legacy_tokens = 0
        legacy_cost = 0.0
        for child in legacy_children:
            if child.status in _TERMINAL:
                usage = grants.subtree_usage(child.run_id)
                legacy_steps += int(usage["steps"])
                legacy_tools += int(usage["tool_calls"])
                legacy_tokens += int(usage["output_tokens"])
                legacy_cost += float(usage["cost"])
            else:
                legacy_steps += child.spec.limits.max_steps
                legacy_tools += child.spec.limits.max_tool_calls
                legacy_tokens += child.spec.limits.max_tokens or 0
                legacy_cost += child.spec.limits.max_cost or 0.0

        effective["max_steps"] = max(0, int(effective["max_steps"] or 0) - legacy_steps)
        effective["max_tool_calls"] = max(
            0,
            int(effective["max_tool_calls"] or 0) - legacy_tools,
        )
        if effective["max_tokens"] is not None:
            effective["max_tokens"] = max(0, int(effective["max_tokens"]) - legacy_tokens)
        if effective["max_cost"] is not None:
            effective["max_cost"] = max(0.0, float(effective["max_cost"]) - legacy_cost)
        return effective

    @staticmethod
    def _require_runnable(
        repository: PostgresAgentRunRepository,
        run_id: str,
    ) -> AgentRunSnapshot:
        snapshot = repository.get_run(run_id)
        if snapshot is None:
            raise KeyError(run_id)
        if snapshot.status in _TERMINAL or snapshot.desired_state != "running":
            raise AgentBudgetError(f"agent_run_not_runnable:{snapshot.status}")
        return snapshot

    @staticmethod
    def _wall_time_exceeded(snapshot: AgentRunSnapshot) -> bool:
        anchor = snapshot.started_at or snapshot.created_at
        elapsed = max(
            0.0,
            (datetime.now(timezone.utc) - anchor).total_seconds(),
        )
        return elapsed > snapshot.spec.limits.max_wall_time_seconds

    @staticmethod
    def _fail_locked(
        repository: PostgresAgentRunRepository,
        snapshot: AgentRunSnapshot,
        reason: str,
    ) -> None:
        if snapshot.status in _TERMINAL:
            return
        current = repository.get_run(snapshot.run_id) or snapshot
        if current.status in _TERMINAL:
            return
        repository.append_event(
            AgentEvent(
                run_id=snapshot.run_id,
                event_type="run.failed",
                payload={
                    "source": "omnix_budget",
                    "error": reason[:2000],
                    "provider_error_code": "agent_run_budget_exhausted",
                    "retryable": False,
                    "error_scope": "run",
                },
            )
        )
        repository.update_state(
            snapshot.run_id,
            expected_revision=current.revision,
            status="failed",
            desired_state="cancelled",
            last_error=reason[:2000],
        )


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_agent_budget_manager() -> AgentBudgetManager:
    return AgentBudgetManager()
