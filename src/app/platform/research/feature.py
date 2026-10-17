"""Research feature declaration."""
from __future__ import annotations

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import ResourceClass
from app.platform.research.persistence.report_repository import PostgresResearchReportRepository
from app.persistence.repository_registry import RepositorySpec
from app.runtime.features import FeatureModule
from app.runtime.features import FeatureContext
from app.capabilities.registry import TOOL_DECLARATIONS
from app.platform.chat.contracts import CHAT_RESEARCH
from app.runtime.ports import ContributionSpec
from .api import create_research_credential_router

from .jobs import DeepResearchJobInput, execute_research_job


def _execute(context: JobExecutionContext, job):
    chat_store = getattr(context.services, "chat", None) if context.services is not None else None
    return execute_research_job(context.job_store, job, chat_store=chat_store)



def _research_router(context: FeatureContext):
    from fastapi import APIRouter

    from .api import register_research_job_routes

    router = APIRouter()
    router.include_router(create_research_credential_router())
    services = context.services
    register_research_job_routes(router, job_store_factory=lambda: services.jobs)
    return router


def _research_tool(_context: FeatureContext):
    from .assistant_tool import ResearchTool

    return ResearchTool()


def _chat_research(_context: FeatureContext):
    from .api import ChatResearchAdapter

    return ChatResearchAdapter()

FEATURE = FeatureModule(
    id="research",
    title="Research",
    tier="platform",
    # Deep research is asked for in chat and answers into chat sessions; research
    # implements chat's CHAT_RESEARCH port, so chat never imports research (ADR-0016).
    depends_on=("chat",),
    # Its web search tool is contributed only when assistant tools can run it.
    uses=("assistant-tools",),
    contributions=(
        ContributionSpec(CHAT_RESEARCH, _chat_research),
        ContributionSpec(TOOL_DECLARATIONS, _research_tool),
    ),
    routers=(_research_router,),
    repositories=(
        RepositorySpec(PostgresResearchReportRepository, PostgresResearchReportRepository, "research_reports"),
    ),
    job_handlers=(
        JobHandlerSpec(
            type="assistant.deep_research",
            handler=_execute,
            input_model=DeepResearchJobInput,
            resource_class=ResourceClass.NETWORK,
            timeout_seconds=1800,
            max_attempts=3,
        ),
    ),
)
