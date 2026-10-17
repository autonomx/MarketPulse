"""Agent runtime feature declaration."""
from app.runtime.background import BackgroundWorker
from app.runtime.capabilities import RuntimeCapability
from app.platform.assistant_tools.contracts import AGENT_RUN_WORKSPACES
from app.platform.chat.contracts import TYPED_TURN_ROUTER
from app.runtime.features import FeatureContext, FeatureModule
from app.runtime.ports import ContributionSpec
from .routes import create_agent_runtime_router
from .jobs import AGENT_RUN_JOB_HANDLERS


def _supervisor_worker(context: FeatureContext) -> BackgroundWorker | None:
    if not context.capabilities.allows(RuntimeCapability.RUN_JOB_WORKERS):
        return None
    service = getattr(context.services, "agent_runs", None)
    if service is None:
        raise RuntimeError("Agent runtime service is not composed")

    def startup() -> None:
        service.start_supervisor()

    def shutdown() -> None:
        service.stop_supervisor()

    return BackgroundWorker(
        name="agent-runtime-supervisor",
        monitor=service,
        startup=(startup,),
        shutdown=(shutdown,),
        requires=frozenset({RuntimeCapability.RUN_JOB_WORKERS}),
    )


class _RunWorkspaces:
    """The authoritative issued workspace of an agent run and its preview launcher."""

    def preview(self, run_id: str):
        from .service import default_agent_run_service

        service = default_agent_run_service()
        snapshot = service.get(run_id)
        if snapshot is None or snapshot.spec.workspace is None:
            return None
        return snapshot.spec.workspace, service.workspace_preview_launcher(snapshot.spec)


def _route_typed_turn(session, user_message, **options):
    # The chat bridge loads on the first typed turn, not at startup.
    from .chat_bridge import route_typed_chat_turn

    return route_typed_chat_turn(session, user_message, **options)


def _typed_turn_router(_context: FeatureContext):
    return _route_typed_turn


FEATURE = FeatureModule(
    id="agent-runtime",
    title="Agent Runtime",
    tier="platform",
    # Builds on chat and assistant tools, and runs without either (ADR-0016).
    uses=("chat", "assistant-tools"),
    routers=(create_agent_runtime_router,),
    job_handlers=AGENT_RUN_JOB_HANDLERS,
    background_workers=(_supervisor_worker,),
    contributions=(
        ContributionSpec(TYPED_TURN_ROUTER, _typed_turn_router),
        ContributionSpec(AGENT_RUN_WORKSPACES, lambda _context: _RunWorkspaces()),
    ),
)
