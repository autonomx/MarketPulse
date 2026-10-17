"""Published responses of the RPG and Hermes routes the web client does not read (WP-8.6).

``web_contracts`` documents what the browser reads; these document the rest,
from the code that builds each response, so every RPG route publishes its 200
response. Like those, a model documents and never filters or validates: a field
is ``Required`` only where every successful response carries it, and an opaque
document (a stored revision, a generated payload) stays ``dict[str, Any]``.
Keep a model in step with its builder when either changes.
"""
from __future__ import annotations

from typing import Any, Literal

from typing_extensions import NotRequired, Required, TypedDict

from .web_contracts import (
    RpgCampaignLoreResponse,
    RpgLoreDocumentSummary,
    RpgLoreGenerationJob,
    RpgLoreStorage,
    RpgAuthoringEntityCard,
    RpgAuthoringTopic,
    RpgMapBlueprintRevision,
    RpgScenarioRevision,
    RpgScenarioSummary,
    RpgWorldGenerationMutationResponse,
    RpgWorldGenerationReviewDecision,
    RpgWorldGenerationRun,
    RpgWorldGenerationTopicResult,
    RpgWorldImageTarget,
    RpgWorldLaunchRepairResponse,
    RpgWorldRelease,
    RpgWorldRevision,
    RpgWorldSummary,
    RpgWorldTopic,
)

# -- worlds, scenarios and campaign bindings ----------------------------------


class RpgWorldListResponse(TypedDict):
    ok: bool
    worlds: list[RpgWorldSummary]


class RpgWorldResponse(TypedDict):
    ok: bool
    world: RpgWorldSummary


class RpgWorldLifecycleResponse(TypedDict):
    """Archive or restore; ``idempotent`` when the world was already there."""

    ok: bool
    world: RpgWorldSummary
    idempotent: bool


class RpgScenarioResponse(TypedDict):
    ok: bool
    scenario: RpgScenarioSummary


class RpgScenarioLifecycleResponse(TypedDict):
    ok: bool
    scenario: RpgScenarioSummary
    idempotent: bool


class RpgStoredWorldRevision(RpgWorldRevision):
    workspace_id: str
    world_id: str


class RpgStoredWorldRelease(RpgWorldRelease):
    workspace_id: str
    world_id: str


class RpgStoredScenarioRevision(RpgScenarioRevision):
    workspace_id: str
    scenario_id: str


class RpgWorldRevisionResponse(TypedDict):
    ok: bool
    world_revision: RpgStoredWorldRevision


class RpgWorldReleaseResponse(TypedDict):
    ok: bool
    world_release: RpgStoredWorldRelease


class RpgScenarioRevisionResponse(TypedDict):
    ok: bool
    scenario_revision: RpgStoredScenarioRevision


class RpgCampaignWorldBindingResponse(TypedDict):
    """``binding`` is the stored ``CampaignWorldBinding`` document."""

    ok: bool
    binding: dict[str, Any]


class RpgLegacyWorldImportResponse(TypedDict):
    """A campaign bible imported as a world; ``reused`` when it was imported before."""

    ok: bool
    status: Literal["imported"]
    reused: bool
    source_campaign_id: str
    world: RpgWorldSummary
    world_revision: RpgStoredWorldRevision
    world_release: RpgStoredWorldRelease
    scenario_revision: RpgStoredScenarioRevision
    source_campaign_rebound: bool


class RpgWorldTopicHistoryEntry(TypedDict):
    history_sequence: int
    topic_id: str
    draft_revision: int
    source: str
    status: str
    content: dict[str, Any]
    directives: dict[str, Any]
    dependency_hashes: dict[str, str]
    input_hash: str
    content_hash: str
    provenance: dict[str, Any]
    topic_updated_at: str
    captured_at: str


class RpgWorldTopicHistoryResponse(TypedDict):
    ok: bool
    history: list[RpgWorldTopicHistoryEntry]


class RpgWorldDraftRestoreResponse(TypedDict):
    ok: bool
    world_id: str
    source_draft_revision: int
    previous_current_draft_revision: int
    restored_draft_revision: int
    topics: list[RpgWorldTopic]


class RpgAuthorshipAuditTopic(TypedDict):
    topic_id: str
    source: str
    status: str
    classification: str
    publishable: bool
    artifact_status: str
    generation_artifact_id: str
    lore_string_count: int
    origin_count: int
    blocked_paths: list[str]
    entities: list[dict[str, Any]]


class RpgAuthorshipImmutableHistory(TypedDict):
    world_revision_count: int
    world_release_count: int
    mutated: bool


class RpgWorldAuthorshipAuditResponse(TypedDict):
    schema_version: Literal["rpg_world_authorship_audit_v2"]
    ok: bool
    world_id: str
    draft_revision: int
    audited_at: str
    publishable: bool
    classification_counts: dict[str, int]
    blocked_topic_ids: list[str]
    topics: list[RpgAuthorshipAuditTopic]
    immutable_history: RpgAuthorshipImmutableHistory


class RpgAuthorshipRemediation(TypedDict, total=False):
    status: Required[Literal["not_required", "queued", "marked_stale", "queue_failed"]]
    topic_ids: Required[list[str]]
    generation: NotRequired[RpgWorldGenerationMutationResponse | None]
    generation_error: NotRequired[str]


class RpgWorldAuthorshipRemediationResponse(RpgWorldAuthorshipAuditResponse):
    remediation: RpgAuthorshipRemediation


# -- world library: topics, map blueprints, generation runs -------------------


class RpgWorldTopicResponse(TypedDict):
    ok: bool
    topic: RpgWorldTopic


class RpgMapBlueprintListResponse(TypedDict):
    ok: bool
    map_blueprints: list[RpgMapBlueprintRevision]


class RpgMapBlueprintResponse(TypedDict):
    ok: bool
    map_blueprint: RpgMapBlueprintRevision


class RpgMapBlueprintMaterializeResponse(TypedDict):
    """Blueprints created for generated locations that had none."""

    ok: bool
    created: list[RpgMapBlueprintRevision]
    created_count: int


class RpgWorldGenerationDiagnosticsResponse(TypedDict):
    ok: bool
    path: str
    format: Literal["jsonl"]
    contains_generated_content: bool


class RpgWorldGenerationStatusResponse(TypedDict):
    ok: bool
    run: RpgWorldGenerationRun
    topic_results: list[RpgWorldGenerationTopicResult]


class RpgWorldGenerationPublishResponse(TypedDict):
    """``reused`` when the run was already published."""

    ok: bool
    status: Literal["ready"]
    run: RpgWorldGenerationRun
    publication: dict[str, Any]
    reused: bool


class RpgPreparedOpening(TypedDict):
    scenario_id: str
    title: str
    result: RpgWorldLaunchRepairResponse


class RpgPrepareOpeningsResponse(TypedDict, total=False):
    """Openings made launchable; while the world still generates, nothing is prepared."""

    ok: Required[bool]
    world_id: Required[str]
    prepared: Required[list[RpgPreparedOpening]]
    status: NotRequired[Literal["generating", "review_required"]]
    generation_run_id: NotRequired[str]


# -- world authoring: sections, topics, entities -------------------------------


class RpgAuthoringDocumentBlock(TypedDict, total=False):
    kind: Required[str]
    title: NotRequired[str]
    body: NotRequired[str]
    items: NotRequired[list[dict[str, Any]]]
    value: NotRequired[Any]


class RpgAuthoringSectionPage(TypedDict, total=False):
    """One authoring page: a document (``summary``, ``body``) or a collection (``entities``)."""

    ok: Required[bool]
    section_id: Required[str]
    page_kind: Required[Literal["document", "collection"]]
    title: Required[str]
    summary: NotRequired[str]
    body: NotRequired[list[RpgAuthoringDocumentBlock]]
    related_entities: NotRequired[list[RpgAuthoringEntityCard]]
    entities: NotRequired[list[RpgAuthoringEntityCard]]
    filters: NotRequired[list[dict[str, Any]]]
    sort_options: NotRequired[list[str]]
    topic: NotRequired[RpgAuthoringTopic]


class RpgAuthoringTopicDetailResponse(TypedDict):
    ok: bool
    world: RpgWorldSummary
    topic: RpgAuthoringTopic
    history: list[RpgWorldTopicHistoryEntry]


class RpgAuthoringTopicMutationResponse(TypedDict, total=False):
    """An edited or restored topic; ``replaced_content_hash`` only on restore."""

    ok: Required[bool]
    topic: Required[RpgAuthoringTopic]
    stale_topic_ids: Required[list[str]]
    replaced_content_hash: NotRequired[str]


class RpgAuthoringEntityHistoryEntry(TypedDict):
    history_sequence: int
    operation: str
    before: dict[str, Any]
    after: dict[str, Any]
    topic_content_hash: str
    metadata: dict[str, Any]
    created_at: str


class RpgAuthoringEntityDetailResponse(TypedDict):
    ok: bool
    world: RpgWorldSummary
    topic: RpgAuthoringTopic
    entity: dict[str, Any]
    history: list[RpgAuthoringEntityHistoryEntry]


# -- world generation review ---------------------------------------------------


class RpgWorldGenerationTopicResultResponse(TypedDict, total=False):
    ok: Required[bool]
    run_id: Required[str]
    parent_run_id: Required[str | None]
    topic_result: Required[RpgWorldGenerationTopicResult]


class RpgWorldGenerationAcceptanceResponse(TypedDict):
    """Accepted candidates, the decision recorded per topic, and the reconciled run."""

    ok: bool
    run_id: str
    accepted_topic_ids: list[str]
    decisions: dict[str, RpgWorldGenerationReviewDecision]
    run: RpgWorldGenerationRun


class RpgWorldGenerationRetryDecisionResponse(TypedDict):
    ok: bool
    run_id: str
    topic_id: str
    decision: Literal["keep", "replace"]
    decision_record: RpgWorldGenerationReviewDecision
    run: RpgWorldGenerationRun


# -- world images and progressive map materialization --------------------------


class RpgWorldImageJob(TypedDict):
    job_id: str
    target_id: str
    status: str


class RpgWorldImageGenerationResponse(TypedDict):
    """The image jobs queued, one per target."""

    ok: bool
    world_id: str
    jobs: list[RpgWorldImageJob]


class RpgWorldImagePromptsResponse(TypedDict):
    """The selected targets with their rebuilt suggested prompts; no image job is created."""

    ok: bool
    world_id: str
    targets: list[RpgWorldImageTarget]


class RpgMaterializationScheduleResponse(TypedDict, total=False):
    """Predictive materialization queued around the current location.

    ``not_applicable`` (with ``reason``) when the world has no starter-bubble plan.
    """

    ok: Required[bool]
    status: Required[Literal["scheduled", "idle", "not_applicable"]]
    world_id: Required[str]
    source_world_revision: Required[int]
    scheduled: Required[list[dict[str, Any]]]
    worker_started: Required[bool]
    campaign_id: NotRequired[str | None]
    current_location_id: NotRequired[str]
    route_intent_location_id: NotRequired[str | None]
    reason: NotRequired[str]


class RpgMaterializationJob(TypedDict):
    job_id: str
    status: str
    source_world_revision: int
    location_id: str
    campaign_id: str | None
    trigger_reasons: list[str]
    priority: int
    attempt_count: int
    max_attempts: int
    progress: dict[str, Any]
    error: dict[str, Any] | None
    created_at: str
    updated_at: str
    completed_at: str | None


class RpgMaterializationTelemetryResponse(TypedDict):
    ok: bool
    world_id: str
    source_world_revision: int | None
    status: Literal["active", "failed", "idle"]
    counts: dict[str, int]
    pending: int
    attempts: int
    failed_location_ids: list[str]
    completed_location_ids: list[str]
    jobs: list[RpgMaterializationJob]


# -- campaign genesis and lore -------------------------------------------------


class RpgCampaignGenesisProgress(TypedDict):
    enabled: bool
    status: str
    stage: str
    launch_ready: bool
    percent: int
    completed_jobs: int
    total_jobs: int
    jobs: list[RpgLoreGenerationJob]
    topic_graph: dict[str, Any]
    audit: dict[str, Any]
    compilation: dict[str, Any]
    campaign_bible_revision: int
    campaign_bible_content_hash: str
    background_expansion: dict[str, Any]


class RpgCampaignGenesisResponse(TypedDict):
    ok: bool
    session_id: str
    generation: RpgCampaignGenesisProgress
    storage: RpgLoreStorage


class RpgLoreDocument(RpgLoreDocumentSummary, total=False):
    full_text: Required[str]


class RpgLoreDocumentResponse(TypedDict):
    ok: bool
    session_id: str
    document: RpgLoreDocument
    storage: RpgLoreStorage


class RpgLoreRegenerationResponse(TypedDict):
    ok: bool
    session_id: str
    document: RpgLoreDocument
    lore: RpgCampaignLoreResponse
    storage: RpgLoreStorage


class RpgLoreMaterializationResponse(RpgLoreRegenerationResponse):
    """A lore document generated in play; ``definition`` is the entity it describes."""

    definition: dict[str, Any]


class RpgLoreDiscoveryResponse(TypedDict):
    """``lore`` is the campaign lore payload (``RpgCampaignLoreResponse`` without ``session_id``)."""

    ok: bool
    session_id: str
    document_id: str
    status: str
    lore: dict[str, Any]
    storage: RpgLoreStorage


# -- sessions ------------------------------------------------------------------


class RpgAbilityCoverage(TypedDict):
    """``RpgAbilityCoverageReport`` without its observations."""

    ok: bool
    total_observations: int
    coverage_score: float
    required_dimensions: list[str]
    covered_dimensions: list[str]
    missing_dimensions: list[str]
    dimension_counts: dict[str, int]
    kind_counts: dict[str, int]
    purpose_counts: dict[str, int]
    capability_counts: dict[str, int]
    ability_counts: dict[str, int]
    source_counts: dict[str, int]
    warnings: list[str]


class RpgAbilityCoverageResponse(TypedDict):
    ok: bool
    session_id: str
    ability_coverage: RpgAbilityCoverage


class RpgLocalDialogueFixtureResponse(TypedDict):
    """A dialogue benchmark case provisioned for local qualification."""

    ok: bool
    fixture_version: str
    case_id: str
    run_id: str
    session_id: str
    created: bool


# -- NPC spatial simulation ----------------------------------------------------


class RpgSpatialClock(TypedDict):
    campaign_id: str
    world_tick: int
    policy: dict[str, Any]
    aggregate_metrics: dict[str, Any]
    created_at: str
    updated_at: str


class RpgSpatialGoal(TypedDict):
    goal_id: str
    goal_revision: int
    campaign_id: str
    actor_id: str
    map_instance_id: str
    goal_type: str
    target_cell: list[int] | None
    portal_id: str | None
    target_map_instance_id: str | None
    priority: int
    issued_tick: int
    not_before_tick: int
    expires_after_tick: int | None
    status: str
    routine_id: str | None
    blocked_attempts: int
    last_decision: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class RpgSpatialRoutineState(TypedDict):
    routine_id: str
    routine_revision: int
    actor_id: str
    enabled: bool
    interval_ticks: int
    steps: list[dict[str, Any]]
    next_step_index: int
    emission_count: int
    next_due_tick: int
    last_issued_tick: int | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class RpgSpatialRoutine(RpgSpatialRoutineState):
    campaign_id: str


class RpgSpatialGoalResponse(TypedDict):
    ok: bool
    goal: RpgSpatialGoal


class RpgSpatialRoutineResponse(TypedDict):
    ok: bool
    routine: RpgSpatialRoutine


class RpgSpatialClockResponse(TypedDict):
    ok: bool
    clock: RpgSpatialClock


class RpgSpatialTickResponse(TypedDict):
    """One simulation tick: the advanced clock, the tick's result and its metrics."""

    ok: bool
    clock: RpgSpatialClock
    result: dict[str, Any]
    metrics: dict[str, Any]


class RpgSpatialStateResponse(TypedDict):
    ok: bool
    clock: RpgSpatialClock
    goals: list[RpgSpatialGoal]
    routines: list[RpgSpatialRoutineState]
    recent_ticks: list[dict[str, Any]]


# -- map observers and tactical play -------------------------------------------


class RpgObserveMapResponse(TypedDict):
    """``reused`` (status ``current``) when the observer already knew this map state."""

    ok: bool
    status: Literal["observed", "current"]
    reused: bool
    knowledge: dict[str, Any]
    projection: dict[str, Any]
    event: dict[str, Any] | None


class RpgObserverProjectionResponse(TypedDict):
    """``stale`` when the map changed after the observer last looked."""

    ok: bool
    stale: bool
    knowledge: dict[str, Any]
    projection: dict[str, Any]
    recent_events: list[dict[str, Any]]


class RpgTacticalResponse(TypedDict, total=False):
    """A committed tactical move or attack, or its replay (``idempotent_replay``, with ``turn_id``)."""

    ok: Required[bool]
    kind: Required[Literal["tactical_move", "tactical_attack"]]
    campaign_id: Required[str]
    map_instance_id: Required[str]
    map_state_revision: Required[int]
    actor_id: Required[str]
    tactical: Required[dict[str, Any]]
    idempotent_replay: Required[bool]
    campaign_revision: Required[int]
    campaign_state_hash: NotRequired[str]
    turn_id: NotRequired[str]
    map_event_id: NotRequired[str]
    actor_cell: NotRequired[list[int]]
    map_event: NotRequired[dict[str, Any]]
    target_id: NotRequired[str]


# -- map geometry and grid performance -----------------------------------------


class RpgGeometryPatchResponse(TypedDict):
    """The map event the patch recorded and the resulting map snapshot."""

    ok: bool
    event: dict[str, Any]
    snapshot: dict[str, Any]


class RpgGridPerformanceProfileResponse(TypedDict):
    ok: bool
    knowledge_source: str
    profile: dict[str, Any]


# -- narrative delivery --------------------------------------------------------


class RpgNarrativeDelivery(TypedDict):
    response_id: str
    semantic_hash: str
    mode: str
    status: str
    block_ids: list[str]
    delivered_block_ids: list[str]
    next_index: int
    revision: int
    cancel_reason: str
    metadata: dict[str, Any]


class RpgNarrativeDeliveryResponse(TypedDict):
    ok: bool
    response_id: str
    semantic_hash: str
    delivery: RpgNarrativeDelivery


class RpgNarrativeDeliveryCancelResponse(TypedDict):
    """``delivery`` is the record, or the projection's metadata when none was stored."""

    ok: bool
    response_id: str
    semantic_hash: str
    delivery: RpgNarrativeDelivery | dict[str, Any]


NARRATIVE_DELIVERY_STREAM: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Server-sent narrative delivery events (``text/event-stream``).",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}


# -- debug logging -------------------------------------------------------------


class RpgDebugLogFile(TypedDict):
    name: str
    size_bytes: int
    updated_at: str


class RpgDebugLogStatusResponse(TypedDict):
    ok: bool
    enabled: bool
    directory: str
    retention_days: int
    max_field_chars: int
    files: list[RpgDebugLogFile]


class RpgDebugClientEventResponse(TypedDict):
    ok: bool
    trace_id: str | None


# -- Hermes --------------------------------------------------------------------
# Hermes routes answer 200 with ``ok: false`` and an ``error`` for refused or
# incomplete requests, so only ``ok`` is required.


class HermesAdapterPreviewResponse(TypedDict, total=False):
    ok: Required[bool]
    source: Required[Literal["hermes_adapter"]]
    error: NotRequired[str]
    mode: NotRequired[str]
    intent: NotRequired[str]
    route: NotRequired[dict[str, Any]]
    request: NotRequired[dict[str, Any]]
    response_contract: NotRequired[dict[str, Any]]


class HermesCapabilitiesResponse(TypedDict, total=False):
    """One mode's ``policy`` when a mode is named, otherwise every mode's ``policies``."""

    ok: Required[bool]
    policy: NotRequired[dict[str, Any]]
    policies: NotRequired[list[dict[str, Any]]]
    error: NotRequired[str]
    mode: NotRequired[str]


class HermesRpgContextResponse(TypedDict, total=False):
    ok: Required[bool]
    read_only: Required[bool]
    source: Required[str]
    session_id: NotRequired[str]
    context: NotRequired[dict[str, Any]]
    error: NotRequired[str]


class HermesRpgPlanResponse(TypedDict, total=False):
    """A reviewed plan proposal; nothing changes state."""

    ok: Required[bool]
    state_changed: Required[bool]
    source: NotRequired[str]
    mode: NotRequired[str]
    error: NotRequired[str]
    planner_context: NotRequired[dict[str, Any]]
    plan: NotRequired[dict[str, Any]]
    validation: NotRequired[dict[str, Any]]
    ticket: NotRequired[dict[str, Any]]


class HermesLookupResponse(TypedDict, total=False):
    ok: Required[bool]
    name: Required[str]
    dry_run: Required[bool]
    mode: Required[Literal["lookup"]]
    payload: NotRequired[dict[str, Any]]
    error: NotRequired[str]


class HermesSequenceExecuteStepResponse(TypedDict, total=False):
    ok: Required[bool]
    source: Required[Literal["hermes_sequence_approved_executor"]]
    state_changed: Required[bool]
    status: NotRequired[Literal["accepted", "blocked", "completed"]]
    error: NotRequired[str]
    stop_reason: NotRequired[str | None]
    item_index: NotRequired[int]
    command_text: NotRequired[str]
    rpg_turn_result: NotRequired[dict[str, Any]]
    approved_flow: NotRequired[dict[str, Any]]
    assist_mode: NotRequired[dict[str, Any]]
    sequence_state: NotRequired[dict[str, Any]]
    next_item_preview: NotRequired[dict[str, Any] | None]


class HermesRpgContextPackResponse(TypedDict, total=False):
    """A bounded read-only pack of the session; over ``char_budget`` it is truncated."""

    ok: Required[bool]
    source: Required[str]
    version: Required[Literal["hermes_rpg_context_pack_v1"]]
    truncated: Required[bool]
    session_id: NotRequired[str]
    current_location: NotRequired[str]
    player_stats: NotRequired[dict[str, Any]]
    inventory: NotRequired[list[Any]]
    party: NotRequired[list[Any]]
    active_quests: NotRequired[list[Any]]
    recent_events: NotRequired[list[Any]]
    known_npcs: NotRequired[list[Any]]
    active_combat: NotRequired[dict[str, Any]]
    active_service: NotRequired[dict[str, Any]]
    active_travel: NotRequired[dict[str, Any]]
    item_limit: NotRequired[int]
    char_budget: NotRequired[int]


class HermesSequencePlanResponse(TypedDict):
    """The critiqued sequence and its checkpoint; ``ok`` is false when the critique blocks it."""

    ok: bool
    source: str
    sequence: dict[str, Any]
    critique_summary: dict[str, Any]
    checkpoint: dict[str, Any]
    loop_guard: dict[str, Any]
    state_changed: bool


class HermesNarrativeResearchResponse(TypedDict, total=False):
    """Read-only research over the campaign bible; it never changes the bible."""

    ok: Required[bool]
    source: Required[Literal["hermes_rpg_narrative_research"]]
    read_only: Required[bool]
    state_changed: Required[bool]
    campaign_bible_changed: NotRequired[bool]
    result: NotRequired[dict[str, Any]]
    evidence: NotRequired[list[dict[str, Any]]]
    policy: NotRequired[dict[str, Any]]
    error: NotRequired[str]
    detail: NotRequired[str]
