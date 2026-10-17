-- omnix-migration: phase=contract transactional=true
-- WP-4.4: PostgreSQL row-level security on every tenant table.
--
-- Each pooled connection carries omnix.workspace_id (the current tenant) and
-- omnix.system ('on' only inside a named cross-workspace system operation),
-- set by app.persistence.tenant_scope at checkout. A query that forgets its
-- workspace filter still sees only the current workspace's rows.
--
-- FORCE applies the policies to the table owner as well. Superusers and
-- BYPASSRLS roles are not subject to row-level security; production should
-- run with a separate runtime role (OMNIX_DATABASE_URL) as documented in
-- docs/OPERATIONS.md.
--
-- Contract phase: code older than this migration does not set the session
-- settings and would read no rows under a non-superuser role.

-- Tables with a workspace_id column.
DO $$
DECLARE
    tenant_table TEXT;
BEGIN
    FOREACH tenant_table IN ARRAY ARRAY[
        'omnix_agent_approvals',
        'omnix_agent_artifacts',
        'omnix_agent_capability_executions',
        'omnix_agent_coding_quality_state',
        'omnix_agent_evidence_query_reservations',
        'omnix_agent_evidence_receipts',
        'omnix_agent_impact_candidates',
        'omnix_agent_inspection_evidence',
        'omnix_agent_plan_revisions',
        'omnix_agent_planning_decisions',
        'omnix_agent_planning_state',
        'omnix_agent_resource_grants',
        'omnix_agent_review_attempts',
        'omnix_agent_review_results',
        'omnix_agent_review_snapshots',
        'omnix_agent_run_commands',
        'omnix_agent_run_events',
        'omnix_agent_run_usage',
        'omnix_agent_runs',
        'omnix_agent_self_review_results',
        'omnix_agent_task_revisions',
        'omnix_agent_validation_results',
        'omnix_agent_worker_leases',
        'omnix_agent_workspace_states',
        'omnix_assets',
        'omnix_audiobook_annotations',
        'omnix_audiobook_castings',
        'omnix_audiobook_chapter_assemblies',
        'omnix_audiobook_chapters',
        'omnix_audiobook_document_blocks',
        'omnix_audiobook_document_overrides',
        'omnix_audiobook_export_manifests',
        'omnix_audiobook_exports',
        'omnix_audiobook_projects',
        'omnix_audiobook_pronunciations',
        'omnix_audiobook_render_batches',
        'omnix_audiobook_renders',
        'omnix_audiobook_review_issues',
        'omnix_audiobook_source_revisions',
        'omnix_audiobook_spans',
        'omnix_audiobook_speaker_aliases',
        'omnix_audiobook_speakers',
        'omnix_audiobook_speech_exclusions',
        'omnix_audiobook_structural_regions',
        'omnix_audiobook_structure_runs',
        'omnix_auth_login_codes',
        'omnix_auth_sessions',
        'omnix_backup_blob_manifest',
        'omnix_capability_approvals',
        'omnix_characters',
        'omnix_chat_messages',
        'omnix_chat_sessions',
        'omnix_conversation_segments',
        'omnix_dead_letters',
        'omnix_idempotency_keys',
        'omnix_install_credentials',
        'omnix_job_events',
        'omnix_job_logs',
        'omnix_jobs',
        'omnix_memory_candidates',
        'omnix_memory_events',
        'omnix_memory_records',
        'omnix_memory_snapshots',
        'omnix_module_records',
        'omnix_outbox_dead_letters',
        'omnix_outbox_events',
        'omnix_outbox_sequences',
        'omnix_prompt_templates',
        'omnix_provider_configs',
        'omnix_provider_status_projections',
        'omnix_reports',
        'omnix_research_records',
        'omnix_rpg_campaign_bible_revisions',
        'omnix_rpg_campaign_bibles',
        'omnix_rpg_campaign_genesis_runs',
        'omnix_rpg_campaign_map_events',
        'omnix_rpg_campaign_map_instances',
        'omnix_rpg_campaign_spatial_clocks',
        'omnix_rpg_campaign_world_bindings',
        'omnix_rpg_campaigns',
        'omnix_rpg_foreground_submissions',
        'omnix_rpg_hermes_research',
        'omnix_rpg_interactions',
        'omnix_rpg_item_descriptions',
        'omnix_rpg_map_blueprint_revisions',
        'omnix_rpg_map_definitions',
        'omnix_rpg_map_observation_events',
        'omnix_rpg_map_observer_knowledge',
        'omnix_rpg_narrative_deliveries',
        'omnix_rpg_narrative_responses',
        'omnix_rpg_narrative_retirement_records',
        'omnix_rpg_npc_spatial_goals',
        'omnix_rpg_npc_spatial_routines',
        'omnix_rpg_npc_spatial_tick_runs',
        'omnix_rpg_npc_spatial_transitions',
        'omnix_rpg_participants',
        'omnix_rpg_scenario_revisions',
        'omnix_rpg_scenarios',
        'omnix_rpg_snapshots',
        'omnix_rpg_turns',
        'omnix_rpg_world_entity_history',
        'omnix_rpg_world_forge_proposals',
        'omnix_rpg_world_generation_runs',
        'omnix_rpg_world_generation_topic_results',
        'omnix_rpg_world_image_attempts',
        'omnix_rpg_world_image_targets',
        'omnix_rpg_world_releases',
        'omnix_rpg_world_revisions',
        'omnix_rpg_world_topic_history',
        'omnix_rpg_world_topics',
        'omnix_rpg_worlds',
        'omnix_runtime_projections',
        'omnix_secret_references',
        'omnix_settings',
        'omnix_settings_entries',
        'omnix_side_effect_receipts',
        'omnix_task_graph_events',
        'omnix_task_graph_node_runs',
        'omnix_task_graph_revisions',
        'omnix_task_graph_runs',
        'omnix_trading_alert_triggers',
        'omnix_trading_alerts',
        'omnix_trading_backtest_equity',
        'omnix_trading_backtest_logs',
        'omnix_trading_backtest_runs',
        'omnix_trading_backtest_trades',
        'omnix_trading_catalyst_evidence',
        'omnix_trading_datasets',
        'omnix_trading_fact_sets',
        'omnix_trading_gapper_universes',
        'omnix_trading_issuer_identities',
        'omnix_trading_model_artifacts',
        'omnix_trading_model_scores',
        'omnix_trading_paper_accounts',
        'omnix_trading_paper_balances',
        'omnix_trading_paper_epoch_archives',
        'omnix_trading_paper_equity_snapshots',
        'omnix_trading_paper_fills',
        'omnix_trading_paper_ledger',
        'omnix_trading_paper_orders',
        'omnix_trading_paper_positions',
        'omnix_trading_paper_protections',
        'omnix_trading_paper_simulation_epochs',
        'omnix_trading_paper_trade_records',
        'omnix_trading_research_actions',
        'omnix_trading_research_evidence',
        'omnix_trading_research_features',
        'omnix_trading_research_outcomes',
        'omnix_trading_research_reports',
        'omnix_trading_research_shadow_annotations',
        'omnix_trading_research_validation_reports',
        'omnix_trading_scanner_results',
        'omnix_trading_scanner_runs',
        'omnix_trading_scanners',
        'omnix_trading_session_evidence_manifests',
        'omnix_trading_solana_ai_decisions',
        'omnix_trading_solana_ai_strategies',
        'omnix_trading_strategy_archives',
        'omnix_trading_strategy_configs',
        'omnix_trading_strategy_events',
        'omnix_trading_strategy_protections',
        'omnix_trading_strategy_runs',
        'omnix_trading_supply_facts',
        'omnix_trading_trigger_plans',
        'omnix_workflow_definitions',
        'omnix_workflow_run_events',
        'omnix_workflow_runs',
        'omnix_workflow_schedule_fires',
        'omnix_workflow_schedules',
        'omnix_workflow_step_runs',
        'omnix_workspace_memberships'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tenant_table);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tenant_table);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tenant_table);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I '
            'USING (workspace_id = current_setting(''omnix.workspace_id'', true) OR current_setting(''omnix.system'', true) = ''on'') '
            'WITH CHECK (workspace_id = current_setting(''omnix.workspace_id'', true) OR current_setting(''omnix.system'', true) = ''on'')',
            tenant_table
        );
    END LOOP;
END
$$;

-- The workspace registry: a workspace sees its own row.
ALTER TABLE omnix_workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_workspaces;
CREATE POLICY tenant_isolation ON omnix_workspaces
    USING (id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');

-- Audit events without a workspace are system events: anyone may record
-- one, only system operations read them.
ALTER TABLE omnix_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_audit_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_audit_events;
CREATE POLICY tenant_isolation ON omnix_audit_events
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR workspace_id IS NULL OR current_setting('omnix.system', true) = 'on');

-- Child tables without workspace_id follow their parent row (the subquery
-- is itself subject to the parent's policy).
ALTER TABLE omnix_asset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_asset_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_asset_versions;
CREATE POLICY tenant_isolation ON omnix_asset_versions
    USING (EXISTS (SELECT 1 FROM omnix_assets AS parent WHERE parent.id = omnix_asset_versions.asset_id))
    WITH CHECK (EXISTS (SELECT 1 FROM omnix_assets AS parent WHERE parent.id = omnix_asset_versions.asset_id));

ALTER TABLE omnix_character_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_character_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_character_versions;
CREATE POLICY tenant_isolation ON omnix_character_versions
    USING (EXISTS (SELECT 1 FROM omnix_characters AS parent WHERE parent.id = omnix_character_versions.character_id))
    WITH CHECK (EXISTS (SELECT 1 FROM omnix_characters AS parent WHERE parent.id = omnix_character_versions.character_id));

ALTER TABLE omnix_job_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_job_attempts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_job_attempts;
CREATE POLICY tenant_isolation ON omnix_job_attempts
    USING (EXISTS (SELECT 1 FROM omnix_jobs AS parent WHERE parent.id = omnix_job_attempts.job_id))
    WITH CHECK (EXISTS (SELECT 1 FROM omnix_jobs AS parent WHERE parent.id = omnix_job_attempts.job_id));

ALTER TABLE omnix_memory_snapshot_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_memory_snapshot_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_memory_snapshot_items;
CREATE POLICY tenant_isolation ON omnix_memory_snapshot_items
    USING (EXISTS (SELECT 1 FROM omnix_memory_snapshots AS parent WHERE parent.id = omnix_memory_snapshot_items.snapshot_id))
    WITH CHECK (EXISTS (SELECT 1 FROM omnix_memory_snapshots AS parent WHERE parent.id = omnix_memory_snapshot_items.snapshot_id));

ALTER TABLE omnix_outbox_consumer_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_outbox_consumer_inbox FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_outbox_consumer_inbox;
CREATE POLICY tenant_isolation ON omnix_outbox_consumer_inbox
    USING (EXISTS (SELECT 1 FROM omnix_outbox_events AS parent WHERE parent.event_key = omnix_outbox_consumer_inbox.event_key))
    WITH CHECK (EXISTS (SELECT 1 FROM omnix_outbox_events AS parent WHERE parent.event_key = omnix_outbox_consumer_inbox.event_key));
