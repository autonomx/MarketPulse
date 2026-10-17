"""Trading feature declaration."""
from app.capabilities.registry import TOOL_DECLARATIONS
from app.runtime.features import FeatureModule
from app.runtime.ports import ContributionSpec
from app.platform.agent_runtime.contracts import SECURITY_INSTRUMENTS
from app.apps.trading.assistant_tool import TradingMarketDataTool, TradingSecurityInstruments
from app.apps.trading.strategy_range_backtest_jobs import STRATEGY_RANGE_BACKTEST_JOB

from .route_registration import create_trading_router, trading_scheduled_task_factories


FEATURE = FeatureModule(
    id="trading",
    title="Trading",
    tier="app",
    # Trading research reads the web through research's quick-search services when
    # research is enabled; research output is evidence only, never order authority.
    uses=("research", "assistant-tools", "agent-runtime"),
    contributions=(
        ContributionSpec(TOOL_DECLARATIONS, lambda _context: TradingMarketDataTool()),
        ContributionSpec(SECURITY_INSTRUMENTS, lambda _context: TradingSecurityInstruments()),
    ),
    routers=(create_trading_router,),
    scheduled_tasks=trading_scheduled_task_factories(),
    job_handlers=(STRATEGY_RANGE_BACKTEST_JOB,),
)
