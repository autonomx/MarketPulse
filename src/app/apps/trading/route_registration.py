"""Trading router composition and scheduler-owned monitor tasks."""
from __future__ import annotations


from fastapi import APIRouter

from app.runtime.features import FeatureContext, ScheduledTaskFactory
from app.runtime.scheduler import ScheduledTaskSpec


def create_trading_router(context: FeatureContext) -> APIRouter:
    """Compose the Trading HTTP surface without patching the gateway class."""
    from app.apps.trading.alerts_api import create_trading_alert_router
    from app.apps.trading.alerts_notify_api import create_trading_notification_router
    from app.apps.trading.api import create_trading_router as create_trading_base_router
    from app.apps.trading.catalyst_api import create_trading_catalyst_router
    from app.apps.trading.execution_api import create_trading_execution_router
    from app.apps.trading.hermes_research_api import create_trading_hermes_research_router
    from app.apps.trading.indicator_values import create_trading_indicator_values_router
    from app.apps.trading.kill_switches import create_trading_kill_switch_router
    from app.apps.trading.market_data_api import create_trading_market_data_router
    from app.apps.trading.metric_api import create_trading_metric_router
    from app.apps.trading.model_api import create_trading_model_router
    from app.apps.trading.paper_analytics_api import create_trading_paper_analytics_router
    from app.apps.trading.paper_api import create_trading_paper_router
    from app.apps.trading.prospective_gap_api import create_trading_prospective_gap_router
    from app.apps.trading.replay_api import create_trading_replay_router
    from app.apps.trading.research_api import create_trading_research_router
    from app.apps.trading.scanner_api import create_trading_scanner_router
    from app.apps.trading.scripts_api import create_trading_scripts_router
    from app.apps.trading.snapshots_api import create_trading_snapshots_router
    from app.apps.trading.heatmaps import create_trading_heatmaps_router
    from app.apps.trading.fundamentals import create_trading_fundamentals_router
    from app.apps.trading.economic_calendar import create_trading_economic_calendar_router
    from app.apps.trading.corporate_events import create_trading_corporate_events_router
    from app.apps.trading.macro import create_trading_macro_router
    from app.apps.trading.options import create_trading_options_router
    from app.apps.trading.strategy_api import create_trading_strategy_router
    from app.apps.trading.strategy_operations_api import create_trading_strategy_operations_router
    from app.apps.trading.strategy_prospective_economic_api import (
        create_trading_strategy_prospective_economic_router,
    )

    router = APIRouter()
    for factory in (
        # Before the base router: its script documents' GET /scripts/{record_id} would take /scripts/reference.
        create_trading_scripts_router,
        create_trading_base_router,
        create_trading_metric_router,
        create_trading_execution_router,
        create_trading_alert_router,
        create_trading_notification_router,
        create_trading_scanner_router,
        create_trading_indicator_values_router,
        create_trading_snapshots_router,
        create_trading_heatmaps_router,
        create_trading_fundamentals_router,
        create_trading_economic_calendar_router,
        create_trading_corporate_events_router,
        create_trading_macro_router,
        create_trading_options_router,
        create_trading_replay_router,
        create_trading_paper_router,
        create_trading_kill_switch_router,
        create_trading_paper_analytics_router,
        create_trading_prospective_gap_router,
        create_trading_research_router,
        create_trading_hermes_research_router,
        create_trading_strategy_router,
        create_trading_strategy_prospective_economic_router,
        create_trading_strategy_operations_router,
        create_trading_catalyst_router,
        create_trading_model_router,
        create_trading_market_data_router,
    ):
        if factory is create_trading_strategy_router:
            router.include_router(
                factory(job_store_factory=lambda: context.services.jobs)
            )
        else:
            router.include_router(factory())
    return router


def trading_scheduled_task_factories() -> tuple[ScheduledTaskFactory, ...]:
    """Each monitor is a task on the shared scheduler (WP-8.3); enablement stays with its module."""
    from app.apps.trading.alerts_delivery import create_notification_delivery_monitor_task
    from app.apps.trading.alerts_monitor import create_trading_alert_monitor_task
    from app.apps.trading.breadth import create_trading_breadth_monitor_task
    from app.apps.trading.fundamental_snapshots import create_trading_fundamentals_monitor_task
    from app.apps.trading.execution_observation_monitor import create_trading_execution_observation_monitor_task
    from app.apps.trading.ibkr_market_data_monitor import create_trading_ibkr_market_data_monitor_task
    from app.apps.trading.metric_monitor import create_trading_metric_monitor_task
    from app.apps.trading.paper_monitor import create_trading_paper_monitor_task
    from app.apps.trading.strategy_overnight_monitor import create_overnight_shadow_monitor_task
    from app.apps.trading.prospective_gap_monitor import create_prospective_gap_monitor_task
    from app.apps.trading.providers.alpaca_iex_status import create_alpaca_iex_status_monitor_task
    from app.apps.trading.session_reconciliation_monitor import create_trading_session_reconciliation_monitor_task
    from app.apps.trading.strategy_ai_shadow_monitor import create_trading_ai_shadow_monitor_task
    from app.apps.trading.strategy_ai_shadow_v2_monitor import create_trading_ai_shadow_v2_monitor_task
    from app.apps.trading.strategy_ai_shadow_v3_monitor import create_trading_ai_shadow_v3_monitor_task
    from app.apps.trading.strategy_deep_recovery_monitor import create_trading_strategy_deep_recovery_shadow_monitor_task
    from app.apps.trading.strategy_dynamic_discovery_monitor import create_interday_dynamic_discovery_monitor_task
    from app.apps.trading.strategy_interday_learning_monitor import create_interday_learning_monitor_task
    from app.apps.trading.strategy_monitor import create_trading_strategy_monitor_task
    from app.apps.trading.strategy_prospective_economic_monitor import create_trading_strategy_prospective_economic_monitor_task
    from app.apps.trading.strategy_research_monitor import create_trading_strategy_research_monitor_task
    from app.apps.trading.strategy_research_outcome_monitor import create_trading_strategy_research_outcome_monitor_task
    from app.apps.trading.strategy_universe_archive_monitor import create_trading_strategy_universe_archive_monitor_task
    from app.apps.trading.strategy_v2_qualification_monitor import create_trading_strategy_v2_qualification_monitor_task
    from app.apps.trading.yahoo_acquisition_monitor import create_trading_yahoo_acquisition_monitor_task
    from app.apps.trading.monitor_task import scheduled_task_spec

    monitor_tasks = (
        create_trading_alert_monitor_task,
        create_notification_delivery_monitor_task,
        create_trading_breadth_monitor_task,
        create_trading_fundamentals_monitor_task,
        create_trading_execution_observation_monitor_task,
        create_trading_ibkr_market_data_monitor_task,
        create_trading_metric_monitor_task,
        create_trading_paper_monitor_task,
        create_overnight_shadow_monitor_task,
        create_prospective_gap_monitor_task,
        create_alpaca_iex_status_monitor_task,
        create_trading_session_reconciliation_monitor_task,
        create_trading_ai_shadow_monitor_task,
        create_trading_ai_shadow_v2_monitor_task,
        create_trading_ai_shadow_v3_monitor_task,
        create_trading_strategy_deep_recovery_shadow_monitor_task,
        create_interday_dynamic_discovery_monitor_task,
        create_interday_learning_monitor_task,
        create_trading_strategy_monitor_task,
        create_trading_strategy_prospective_economic_monitor_task,
        create_trading_strategy_research_monitor_task,
        create_trading_strategy_research_outcome_monitor_task,
        create_trading_strategy_universe_archive_monitor_task,
        create_trading_strategy_v2_qualification_monitor_task,
        create_trading_yahoo_acquisition_monitor_task,
    )

    def factory_for(create_task):
        def create(context: FeatureContext) -> ScheduledTaskSpec | None:
            task = create_task(context)
            return None if task is None else scheduled_task_spec(task)

        return create

    from app.apps.trading.prospective_gap_inputs import handoff_import_task
    from app.apps.trading.strategies.runner import strategy_runner_task

    return (
        *(factory_for(create_task) for create_task in monitor_tasks),
        # Registered strategies run through the generic runner (WP-8.3).
        strategy_runner_task,
        # Import the premarket handoff and climatology from GitHub into PostgreSQL.
        handoff_import_task,
    )


__all__ = [
    "create_trading_router",
    "trading_scheduled_task_factories",
]
