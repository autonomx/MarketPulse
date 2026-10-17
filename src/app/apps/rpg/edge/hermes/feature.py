"""The Hermes feature: RPG planning, approved sequences and the sidecar routes (depends on RPG)."""
from __future__ import annotations

from fastapi import APIRouter

from app.platform.chat.contracts import ASSIST_READOUTS
from app.runtime.features import FeatureContext, FeatureModule
from app.runtime.ports import ContributionSpec

from app.apps.rpg.edge.hermes.api import router as hermes_router
from app.apps.rpg.edge.hermes.approved_routes import hermes_rpg_approved_bp


def _router(_context: FeatureContext) -> APIRouter:
    router = APIRouter()
    router.include_router(hermes_router)
    router.include_router(hermes_rpg_approved_bp)
    return router


class _PlanSummaryReadout:
    """Assist mode's read-only summary of the Hermes RPG plan."""

    name = "get_hermes_rpg_plan_summary"

    def payload(self, args):
        from app.apps.rpg.edge.hermes.plan_summary import hermes_rpg_plan_summary_payload

        return hermes_rpg_plan_summary_payload()


FEATURE = FeatureModule(
    id="hermes",
    title="Hermes",
    tier="app",
    depends_on=("rpg",),
    routers=(_router,),
    contributions=(ContributionSpec(ASSIST_READOUTS, lambda _context: _PlanSummaryReadout()),),
)
