"""What the RPG web client reads from the RPG and Hermes routes (WP-9.3).

The routes return plain objects built across the RPG engine. These TypedDicts
document the fields the browser reads, so the web client takes its types from
the generated contract instead of declaring them by hand. A route publishes one
with ``responses=documented(Model)``: the model documents the response and never
filters or validates it, so a field is ``Required`` only where every response
carries it. Keep a model in step with its route when either changes.
"""
from __future__ import annotations

from typing import Any, Literal

from typing_extensions import NotRequired, Required, TypedDict


def documented(model: Any) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry that publishes ``model`` as a route's 200 response."""
    return {200: {"model": model, "description": "The fields the RPG web client reads; the response is not filtered."}}


class RpgEmptyObject(TypedDict):
    """``{}``: what a field holds when there is nothing to report yet."""


class RpgWorldBundleImportResponseLaunchPreparationPrepared(TypedDict, total=False):
    scenario_id: Required[str]
    title: Required[str]


class RpgWorldBundleImportResponseLaunchPreparation(TypedDict, total=False):
    status: NotRequired[Literal['generating', 'ready', 'not_required', 'recovery_required']]
    prepared: NotRequired[list[RpgWorldBundleImportResponseLaunchPreparationPrepared]]
    error: NotRequired[str]


class RpgWorldBundleImportResponse(TypedDict, total=False):
    ok: Required[bool]
    status: Required[str]
    world_id: Required[str]
    source_world_id: Required[str]
    bundle_sha256: Required[str]
    counts: Required[dict[str, float]]
    identifier_map: Required[dict[str, str]]
    warnings: Required[list[str]]
    launch_preparation: NotRequired[RpgWorldBundleImportResponseLaunchPreparation]


class RpgAuthoringDossierQuote(TypedDict, total=False):
    text: Required[str]
    attribution: NotRequired[str]


class RpgAuthoringDossierQuickFact(TypedDict, total=False):
    label: Required[str]
    value: Required[Any]


class RpgAuthoringDossierSection(TypedDict, total=False):
    id: Required[str]
    title: Required[str]
    paragraphs: Required[list[str]]


class RpgAuthoringEntityDossier(TypedDict, total=False):
    schema_version: Required[Literal['rpg_world_entity_dossier_v1'] | str]
    subtitle: NotRequired[str]
    quote: NotRequired[RpgAuthoringDossierQuote | None]
    quick_facts: Required[list[RpgAuthoringDossierQuickFact]]
    sections: Required[list[RpgAuthoringDossierSection]]
    related_entity_ids: Required[list[str]]
    generated_from_legacy: NotRequired[bool]
    quality_enriched: NotRequired[bool]


class RpgAuthoringCardHighlight(TypedDict, total=False):
    label: Required[str]
    value: Required[Any]


class RpgAuthoringCardGroup(TypedDict, total=False):
    label: Required[str]
    items: Required[list[Any]]
    style: Required[Literal['chips', 'list'] | str]


class RpgAuthoringCardPresentation(TypedDict, total=False):
    variant: Required[str]
    eyebrow: Required[str]
    badges: Required[list[Any]]
    highlights: Required[list[RpgAuthoringCardHighlight]]
    groups: Required[list[RpgAuthoringCardGroup]]


class RpgAuthoringEntityCard(TypedDict, total=False):
    id: Required[str]
    title: Required[str]
    summary: Required[str]
    short_summary: NotRequired[str]
    dossier: NotRequired[RpgAuthoringEntityDossier]
    kind: Required[str]
    card_type: Required[str]
    image_target_id: NotRequired[str]
    presentation: Required[RpgAuthoringCardPresentation]
    metadata: Required[dict[str, Any]]


class RpgCampaignLoreResponseDossierCards(TypedDict, total=False):
    characters: NotRequired[list[RpgAuthoringEntityCard]]
    locations: NotRequired[list[RpgAuthoringEntityCard]]
    factions: NotRequired[list[RpgAuthoringEntityCard]]


class RpgLoreDocumentSummary(TypedDict, total=False):
    document_id: Required[str]
    title: Required[str]
    topic_id: Required[str]
    category: Required[str]
    summary_120: Required[str]
    summary_500: Required[str]
    keywords: Required[list[str]]
    visibility: Required[str]
    status: Required[str]
    canon_revision: Required[float]


class RpgLoreGenerationJob(TypedDict, total=False):
    topic_id: NotRequired[str]
    status: NotRequired[str]
    generator_role: NotRequired[str]
    output_counts: NotRequired[dict[str, float]]
    error: NotRequired[str]


class RpgLoreDossier(TypedDict, total=False):
    id: Required[str]
    kind: Required[Literal['npc', 'location', 'faction']]
    name: Required[str]
    status: Required[str]
    appearance: NotRequired[str]
    personality: NotRequired[str]
    speech_style: NotRequired[str]
    role: NotRequired[str]
    location_id: NotRequired[str]
    faction_ids: NotRequired[list[str]]
    region_id: NotRequired[str]
    sensory_profile: NotRequired[str]
    description: NotRequired[str]
    services: NotRequired[list[str]]
    values: NotRequired[list[str]]
    public_goal: NotRequired[str]
    goals: NotRequired[list[str]]


class RpgLoreStorageCurrentLocation(TypedDict, total=False):
    id: NotRequired[str]
    name: NotRequired[str]


class RpgLoreStorage(TypedDict, total=False):
    mode: NotRequired[str]
    persisted: NotRequired[bool]
    revision: NotRequired[float]
    generated_current_location: NotRequired[bool]
    generated_document_id: NotRequired[str | None]
    current_location: NotRequired[RpgLoreStorageCurrentLocation | None]
    error: NotRequired[str]


class RpgLoreCategory(TypedDict, total=False):
    label: Required[str]
    documents: Required[list[RpgLoreDocumentSummary]]


class RpgLoreDossiers(TypedDict, total=False):
    characters: Required[list[RpgLoreDossier]]
    locations: Required[list[RpgLoreDossier]]
    factions: Required[list[RpgLoreDossier]]


class RpgLoreGeneration(TypedDict, total=False):
    status: NotRequired[str]
    stage: NotRequired[str]
    launch_ready: NotRequired[bool]
    percent: NotRequired[float]
    completed_jobs: NotRequired[float]
    total_jobs: NotRequired[float]
    jobs: NotRequired[list[RpgLoreGenerationJob]]


class RpgCampaignLoreResponse(TypedDict, total=False):
    """The campaign bible: the lore panel reads the documents and dossiers, the world editor the cards."""

    ok: Required[bool]
    session_id: Required[str]
    canon_revision: Required[float]
    content_hash: Required[str]
    categories: Required[list[RpgLoreCategory]]
    visible_count: Required[float]
    hidden_count: Required[float]
    dossiers: NotRequired[RpgLoreDossiers]
    generation: NotRequired[RpgLoreGeneration]
    storage: NotRequired[RpgLoreStorage]
    dossier_cards: NotRequired[RpgCampaignLoreResponseDossierCards]
    topic_cards: NotRequired[dict[str, list[RpgAuthoringEntityCard]]]


class RpgWorldDeletionResponse(TypedDict, total=False):
    ok: Required[bool]
    deleted: Required[bool]
    world_id: Required[str]
    world_title: Required[str]
    deleted_counts: Required[dict[str, float]]
    audit_event_id: Required[float]


class RpgWorldDeletionBlocker(TypedDict, total=False):
    code: Required[str]
    count: Required[float]
    message: Required[str]


class RpgWorldDeletionEligibility(TypedDict, total=False):
    can_delete: Required[bool]
    world_id: Required[str]
    world_title: Required[str]
    world_status: Required[str]
    blockers: Required[list[RpgWorldDeletionBlocker]]
    deleted_counts: Required[dict[str, float]]


class RpgWorldDeletionEligibilityResponse(TypedDict, total=False):
    ok: Required[bool]
    eligibility: Required[RpgWorldDeletionEligibility]


class RpgWorldProfileTargetRange(TypedDict, total=False):
    quick: NotRequired[list[float]]
    standard: NotRequired[list[float]]
    epic: NotRequired[list[float]]


class RpgWorldProfilePresentation(TypedDict, total=False):
    page_kind: NotRequired[Literal['document', 'collection'] | str]
    card_variant: NotRequired[str]
    image_role: NotRequired[str]
    group: NotRequired[Literal['world', 'lore', 'game-master'] | str]


class RpgWorldProfileDomainGenerationGuidance(TypedDict, total=False):
    presentation: NotRequired[RpgWorldProfilePresentation]


class RpgWorldProfileDomain(TypedDict, total=False):
    domain_id: Required[str]
    title: Required[str]
    entity_kind: Required[str]
    dependencies: NotRequired[list[str]]
    required_before_launch: NotRequired[bool]
    fields: NotRequired[list[dict[str, Any]]]
    target_range: NotRequired[RpgWorldProfileTargetRange]
    semantic_roles: NotRequired[list[str]]
    generation_guidance: NotRequired[RpgWorldProfileDomainGenerationGuidance]


class RpgWorldGenreProfile(TypedDict, total=False):
    profile_id: Required[str]
    version: Required[float]
    display_name: Required[str]
    domains: Required[list[RpgWorldProfileDomain]]
    aliases: NotRequired[list[str]]
    genre_tags: NotRequired[list[str]]
    launch_requirements: NotRequired[dict[str, Any]]
    runtime_capability_defaults: NotRequired[dict[str, bool]]
    provenance: NotRequired[dict[str, Any]]
    scope: NotRequired[str]


class RpgWorldProfileReview(TypedDict, total=False):
    world_id: Required[str]
    status: Required[str]
    profile_revision: Required[float]
    profile_hash: Required[str]
    approved_profile_hash: Required[str]
    approved_at: NotRequired[str | None]
    approved_by: NotRequired[str | None]
    profile: Required[RpgWorldGenreProfile | RpgEmptyObject]
    requested_genre: Required[str]
    normalized_genre: Required[str]
    source: Required[str]
    generated: Required[bool]
    route: Required[dict[str, Any]]
    review_findings: Required[list[Any]]
    error: Required[dict[str, Any]]


class RpgWorldProfileResponse(TypedDict, total=False):
    ok: Required[bool]
    review: Required[RpgWorldProfileReview]
    stale_topic_ids: NotRequired[list[str]]


class RpgWorldGenerationRun(TypedDict, total=False):
    run_id: Required[str]
    world_id: Required[str]
    draft_revision: Required[float]
    status: Required[str]
    graph: Required[dict[str, Any]]
    context: Required[dict[str, Any]]
    settings: Required[dict[str, Any]]
    plan: Required[dict[str, Any]]
    progress: Required[dict[str, Any]]
    error: Required[dict[str, Any]]
    parent_run_id: NotRequired[str | None]
    lineage: NotRequired[dict[str, Any]]
    created_at: Required[str]
    updated_at: Required[str]
    completed_at: NotRequired[str | None]


class RpgWorldSummary(TypedDict, total=False):
    id: Required[str]
    title: Required[str]
    description: Required[str]
    status: Required[str]
    source_mode: Required[str]
    genre: Required[str]
    tone: Required[str]
    seed: Required[float]
    draft_revision: Required[float]
    metadata: Required[dict[str, Any]]
    scenario_count: NotRequired[float]
    generation: NotRequired[RpgWorldGenerationRun | None]
    created_at: Required[str]
    updated_at: Required[str]


class RpgWorldImageAttempt(TypedDict, total=False):
    job_id: Required[str]
    prompt: Required[str]
    source_content_hash: Required[str]
    status: Required[str]
    asset_id: NotRequired[str | None]
    error: Required[dict[str, Any]]
    created_at: Required[str]
    updated_at: Required[str]


class RpgWorldImageTarget(TypedDict, total=False):
    world_id: Required[str]
    target_id: Required[str]
    target_type: Required[str]
    entity_id: Required[str]
    role: Required[str]
    source_content_hash: Required[str]
    status: Required[str]
    review_state: Required[str]
    suggested_prompt: Required[str]
    active_asset_id: NotRequired[str | None]
    latest_job_id: NotRequired[str | None]
    metadata: Required[dict[str, Any]]
    attempts: Required[list[RpgWorldImageAttempt]]
    created_at: Required[str]
    updated_at: Required[str]


class RpgWorldImageTargetsResponse(TypedDict, total=False):
    ok: Required[bool]
    world: Required[RpgWorldSummary]
    targets: Required[list[RpgWorldImageTarget]]


class RpgWorldGenerationReviewWaiver(TypedDict, total=False):
    status: Required[Literal['active', 'none'] | str]
    reason: NotRequired[str]
    accepted_by: NotRequired[str]
    accepted_at: NotRequired[str]


class RpgWorldGenerationReviewIssue(TypedDict, total=False):
    code: Required[str]
    topic_id: Required[str]
    entity_id: NotRequired[str]
    field_id: NotRequired[str]
    message: NotRequired[str]
    expected: NotRequired[str]
    allowed_domains: NotRequired[list[str]]
    candidates: NotRequired[list[str]]
    supplied_value: NotRequired[Any]


class RpgWorldGenerationValidationAttempt(TypedDict, total=False):
    schema_version: Required[str]
    attempt_id: Required[str]
    attempt_number: Required[float]
    trigger: Required[Literal['generation', 'manual_retry'] | str]
    run_id: Required[str]
    topic_id: Required[str]
    job_id: Required[str]
    candidate_hash: Required[str]
    result_status: Required[str]
    validation_status: Required[str]
    reason_codes: Required[list[str]]
    issues: Required[list[RpgWorldGenerationReviewIssue]]
    validation_hash: Required[str]
    provider: Required[dict[str, Any]]


class RpgWorldGenerationReviewReport(TypedDict, total=False):
    schema_version: NotRequired[str]
    status: Required[str]
    blocking: Required[bool]
    validation_blocking: NotRequired[bool]
    review_decision: NotRequired[str]
    validation_status: NotRequired[Literal['passed', 'failed', 'not_run'] | str]
    waiver_status: NotRequired[Literal['active', 'none'] | str]
    waiver: NotRequired[RpgWorldGenerationReviewWaiver]
    error_type: NotRequired[str]
    reason_codes: Required[list[str]]
    issues: Required[list[RpgWorldGenerationReviewIssue]]
    outstanding_reason_codes: NotRequired[list[str]]
    outstanding_findings: NotRequired[list[RpgWorldGenerationReviewIssue]]
    attempt_history_schema: NotRequired[str]
    attempt_history: NotRequired[list[RpgWorldGenerationValidationAttempt]]
    summary: NotRequired[str]
    accepted_at: NotRequired[str]
    accepted_by: NotRequired[str]
    previous_validation: NotRequired[RpgWorldGenerationReviewReport]


class RpgWorldGenerationFindingFingerprint(TypedDict, total=False):
    code: Required[str]
    topic_id: Required[str]
    entity_id: Required[str]
    field_id: Required[str]
    bad_value: Required[str]


class RpgWorldGenerationRepairEvaluationChangedBadValues(TypedDict, total=False):
    location: Required[list[str]]
    previous_bad_value: Required[str]
    current_bad_value: Required[str]


class RpgWorldGenerationRepairEvaluation(TypedDict, total=False):
    schema_version: Required[str]
    outcome: Required[Literal['repaired', 'partially_repaired', 'no_op', 'regressed', 'replaced_with_new_failure', 'not_applicable'] | str]
    candidate_changed: Required[bool]
    original_finding_count: Required[float]
    remaining_finding_count: Required[float]
    repaired_finding_count: Required[float]
    introduced_finding_count: Required[float]
    remaining_finding_fingerprints: Required[list[RpgWorldGenerationFindingFingerprint]]
    repaired_finding_fingerprints: Required[list[RpgWorldGenerationFindingFingerprint]]
    introduced_finding_fingerprints: Required[list[RpgWorldGenerationFindingFingerprint]]
    changed_bad_values: Required[list[RpgWorldGenerationRepairEvaluationChangedBadValues]]


class RpgWorldGenerationReviewState(TypedDict, total=False):
    review_decision: Required[Literal['accepted', 'pending'] | str]
    validation_status: Required[Literal['passed', 'failed', 'not_run'] | str]
    waiver_status: Required[Literal['active', 'none'] | str]
    outstanding_finding_count: Required[float]
    outstanding_findings: Required[list[RpgWorldGenerationReviewIssue]]
    outstanding_reason_codes: Required[list[str]]
    attempt_count: Required[float]
    attempt_history: Required[list[RpgWorldGenerationValidationAttempt]]
    repair_evaluation: Required[RpgWorldGenerationRepairEvaluation | None]
    consecutive_no_op_count: Required[float]
    retry_budget_exhausted: Required[bool]


class RpgWorldGenerationFailureArtifactIssues(TypedDict, total=False):
    stage: Required[str]
    path: Required[str]
    code: Required[str]
    message: Required[str]


class RpgWorldGenerationFailureArtifact(TypedDict, total=False):
    schema_version: Required[Literal['rpg_world_forge_failure_artifact_v1'] | str]
    artifact_id: Required[str]
    run_id: Required[str]
    job_id: Required[str]
    topic_id: Required[str]
    attempt: Required[float]
    stage: Required[str]
    provider: Required[str]
    model: Required[str]
    structured_mode: Required[str]
    strategy_identity: Required[str]
    provider_schema_hash: Required[str]
    canonical_contract_hash: Required[str]
    raw_response_hash: Required[str]
    raw_response_bytes: Required[float]
    sanitized_excerpt: Required[str]
    issues: Required[list[RpgWorldGenerationFailureArtifactIssues]]
    deterministic_repairs: Required[list[str]]
    correction_attempted: Required[bool]
    correction_result: Required[str]
    created_at: Required[str]


class RpgWorldGenerationReviewDecision(TypedDict, total=False):
    decision: Required[Literal['accept', 'keep', 'replace']]
    candidate_hash: Required[str]
    promoted_hash: NotRequired[str]
    decided_at: NotRequired[str]
    edited: NotRequired[bool]
    review_decision: NotRequired[str]
    validation_status: NotRequired[str]
    waiver_status: NotRequired[str]
    outstanding_finding_count: NotRequired[float]


class RpgWorldGenerationTopicResult(TypedDict, total=False):
    run_id: Required[str]
    world_id: Required[str]
    draft_revision: Required[float]
    topic_id: Required[str]
    status: Required[Literal['accepted', 'needs_review', 'failed', 'blocked']]
    candidate: Required[dict[str, Any] | None]
    candidate_hash: Required[str]
    validation: Required[RpgWorldGenerationReviewReport]
    review_state: NotRequired[RpgWorldGenerationReviewState]
    provider: Required[dict[str, Any]]
    failure_artifact: NotRequired[RpgWorldGenerationFailureArtifact | None]
    dependency_hashes: Required[dict[str, str]]
    dependency_trust: Required[dict[str, str]]
    job_id: Required[str]
    created_at: Required[str]
    updated_at: Required[str]
    previous_result: NotRequired[RpgWorldGenerationTopicResult | None]
    decision: NotRequired[RpgWorldGenerationReviewDecision | None]


class RpgWorldGenerationReviewAnalytics(TypedDict, total=False):
    status: Required[dict[str, float]]
    by_code: Required[dict[str, float]]
    by_field: Required[dict[str, float]]
    by_topic: Required[dict[str, float]]
    by_domain: Required[dict[str, float]]
    by_model: Required[dict[str, float]]
    by_prompt_version: Required[dict[str, float]]
    by_provider: Required[dict[str, float]]


class RpgWorldGenerationReviewListResponse(TypedDict, total=False):
    ok: Required[bool]
    run_id: Required[str]
    parent_run_id: NotRequired[str | None]
    topic_results: Required[list[RpgWorldGenerationTopicResult]]
    analytics: Required[RpgWorldGenerationReviewAnalytics]
    review_decisions: Required[dict[str, RpgWorldGenerationReviewDecision]]


class RpgAuthoringDossierPreviewResponse(TypedDict, total=False):
    ok: Required[bool]
    preview_only: Required[bool]
    world_id: Required[str]
    topic_id: Required[str]
    entity_id: Required[str]
    expected_draft_revision: Required[float]
    expected_content_hash: Required[str]
    short_summary: Required[str]
    dossier: Required[RpgAuthoringEntityDossier]
    generation: Required[dict[str, Any]]
    canonical_fields_preserved: Required[bool]
    stored: Required[Literal[False]]


class RpgAuthoringTopic(TypedDict, total=False):
    topic_id: Required[str]
    draft_revision: Required[float]
    source: Required[str]
    status: Required[str]
    content: Required[dict[str, Any]]
    directives: Required[dict[str, Any]]
    dependency_hashes: Required[dict[str, str]]
    input_hash: Required[str]
    content_hash: Required[str]
    provenance: Required[dict[str, Any]]
    updated_at: Required[str]


class RpgAuthoringEntityMutationResponse(TypedDict, total=False):
    ok: Required[bool]
    topic: Required[RpgAuthoringTopic]
    entity: Required[dict[str, Any]]
    stale_topic_ids: Required[list[str]]
    stale_entity_ids: Required[list[str]]
    canonical_fields_preserved: NotRequired[bool]
    editorial_only: NotRequired[bool]


class RpgDossierEnrichmentCandidate(TypedDict, total=False):
    topic_id: Required[str]
    entity_id: Required[str]
    title: Required[str]
    word_count: Required[float]
    generated_from_legacy: Required[bool]
    issues: Required[list[str]]


class RpgWorldDossierEnrichmentResponseCompleted(TypedDict, total=False):
    topic_id: Required[str]
    entity_id: Required[str]
    content_hash: Required[str]


class RpgWorldDossierEnrichmentResponseFailed(TypedDict, total=False):
    topic_id: Required[str]
    entity_id: Required[str]
    error: Required[str]


class RpgDossierQualityMetrics(TypedDict, total=False):
    entities: Required[float]
    rich_dossiers: Required[float]
    projected_legacy_dossiers: Required[float]
    invalid_or_thin_dossiers: Required[float]
    heading_repairs: Required[float]
    coverage_percent: Required[float]
    average_words: Required[float]
    unresolved_related_entity_ids: Required[float]


class RpgDossierQualityTopicMetrics(TypedDict, total=False):
    topic_id: Required[str]
    entities: Required[float]
    rich: Required[float]
    projected: Required[float]
    invalid: Required[float]
    words: Required[float]
    coverage_percent: Required[float]
    average_words: Required[float]


class RpgWorldDossierQualityResponse(TypedDict, total=False):
    ok: Required[bool]
    world_id: Required[str]
    draft_revision: Required[float]
    schema_version: Required[str]
    metrics: Required[RpgDossierQualityMetrics]
    by_topic: Required[list[RpgDossierQualityTopicMetrics]]
    unresolved_related_entity_ids: Required[list[str]]
    enrichment_candidates: Required[list[RpgDossierEnrichmentCandidate]]


class RpgWorldDossierEnrichmentResponse(TypedDict, total=False):
    ok: Required[bool]
    world_id: Required[str]
    dry_run: Required[bool]
    candidate_count: NotRequired[float]
    candidates: NotRequired[list[RpgDossierEnrichmentCandidate]]
    attempted: NotRequired[float]
    completed: NotRequired[list[RpgWorldDossierEnrichmentResponseCompleted]]
    failed: NotRequired[list[RpgWorldDossierEnrichmentResponseFailed]]
    metrics: NotRequired[RpgDossierQualityMetrics]
    quality: NotRequired[RpgWorldDossierQualityResponse]


RpgAuthoringGroup = Literal['workspace', 'world', 'lore', 'game-master']

RpgAuthoringPageKind = Literal['document', 'collection']

class RpgAuthoringSection(TypedDict, total=False):
    id: Required[str]
    label: Required[str]
    group: Required[RpgAuthoringGroup]
    page_kind: Required[RpgAuthoringPageKind]
    topic_ids: Required[list[str]]
    entity_kind: NotRequired[str]
    dependencies: Required[list[str]]
    required_before_launch: Required[bool]
    supports_generation: Required[bool]
    supports_images: Required[bool]
    supports_entity_editing: Required[bool]
    operational_status: Required[str]
    editorial_status: Required[str]
    entity_count: Required[float]


class RpgWorldTokenUsage(TypedDict, total=False):
    prompt_tokens: Required[float]
    completion_tokens: Required[float]
    total_tokens: Required[float]
    provider_reported_topics: Required[float]
    estimated_topics: Required[float]
    unavailable_topics: Required[float]
    topic_count: Required[float]
    in_flight_topics: NotRequired[float]
    generation_duration_ms: NotRequired[float]
    timed_topics: NotRequired[float]
    repair_count: NotRequired[float]
    repair_tokens: NotRequired[float]
    provider_reported_repairs: NotRequired[float]
    estimated_repairs: NotRequired[float]


class RpgAuthoringManifestResponse(TypedDict, total=False):
    ok: Required[bool]
    world: Required[RpgWorldSummary]
    sections: Required[list[RpgAuthoringSection]]
    generation: Required[RpgWorldGenerationRun | RpgEmptyObject]
    token_usage: NotRequired[RpgWorldTokenUsage]


class RpgPublishedLaunchResponse(TypedDict, total=False):
    ok: Required[bool]
    status: NotRequired[str]
    session_id: NotRequired[str]
    session: NotRequired[dict[str, Any]]
    game: NotRequired[dict[str, Any]]
    binding: NotRequired[dict[str, Any]]
    launch_mode: NotRequired[str]
    world_forge_invoked: NotRequired[bool]
    error: NotRequired[str]


class RpgDeferredMaterializationResponse(TypedDict, total=False):
    ok: Required[bool]
    status: Required[str]
    reused: Required[bool]
    materialization: Required[dict[str, Any]]


class RpgScenarioRevision(TypedDict, total=False):
    revision: Required[float]
    world_id: Required[str]
    world_revision: Required[float]
    document: Required[dict[str, Any]]
    content_hash: Required[str]
    created_at: Required[str]


class RpgWorldLaunchRepairResponse(TypedDict, total=False):
    ok: Required[bool]
    status: Required[str]
    world: Required[dict[str, Any]]
    promotion: Required[dict[str, Any]]
    scenario_revision: Required[RpgScenarioRevision]
    certification: Required[dict[str, Any]]


class RpgStarterBubblePromotionResponse(TypedDict, total=False):
    ok: Required[bool]
    status: Required[str]
    reused: Required[bool]
    promotion: Required[dict[str, Any]]


class RpgStarterBubbleResponse(TypedDict, total=False):
    ok: Required[bool]
    starter_bubble: Required[dict[str, Any]]
    predictive_materialization: Required[list[dict[str, Any]]]


class RpgWorldGenerationMutationResponse(TypedDict, total=False):
    ok: Required[bool]
    run: Required[RpgWorldGenerationRun]
    worker_started: Required[bool]
    scope: NotRequired[dict[str, Any]]
    retry_of_run_id: NotRequired[str]
    continue_of_run_id: NotRequired[str]
    diagnostic_id: NotRequired[str]
    diagnostic_log: NotRequired[str]


class RpgWorldTopic(TypedDict, total=False):
    topic_id: Required[str]
    draft_revision: Required[float]
    source: Required[str]
    status: Required[str]
    content: Required[dict[str, Any]]
    directives: Required[dict[str, Any]]
    dependency_hashes: Required[dict[str, str]]
    input_hash: Required[str]
    content_hash: Required[str]
    provenance: Required[dict[str, Any]]
    updated_at: Required[str]


class RpgMapBlueprintRevision(TypedDict, total=False):
    world_id: Required[str]
    map_id: Required[str]
    blueprint_revision: Required[float]
    document: Required[dict[str, Any]]
    content_hash: Required[str]
    semantic_interface_hash: Required[str]
    status: Required[Literal['ready', 'invalid']]
    findings: Required[list[dict[str, Any]]]
    created_at: Required[str]


class RpgWorldRevision(TypedDict, total=False):
    revision: Required[float]
    document: Required[dict[str, Any]]
    content_hash: Required[str]
    created_at: Required[str]


class RpgWorldRelease(TypedDict, total=False):
    world_revision: Required[float]
    release: Required[float]
    document: Required[dict[str, Any]]
    release_hash: Required[str]
    created_at: Required[str]


class RpgScenarioSummary(TypedDict, total=False):
    id: Required[str]
    world_id: Required[str]
    title: Required[str]
    description: Required[str]
    status: Required[str]
    metadata: Required[dict[str, Any]]
    created_at: Required[str]
    updated_at: Required[str]


class RpgWorldDetailResponse(TypedDict, total=False):
    ok: Required[bool]
    world: Required[RpgWorldSummary]
    topics: Required[list[RpgWorldTopic]]
    map_blueprints: Required[list[RpgMapBlueprintRevision]]
    revisions: Required[list[RpgWorldRevision]]
    releases: Required[list[RpgWorldRelease]]
    scenarios: Required[list[RpgScenarioSummary]]
    scenario_revisions: Required[dict[str, list[RpgScenarioRevision]]]
    generation_runs: Required[list[RpgWorldGenerationRun]]


class RpgWorldCampaignSummary(TypedDict, total=False):
    campaign_id: Required[str]
    title: Required[str]
    status: Required[str]
    revision: Required[float]
    updated_at: Required[str]
    world_id: Required[str]
    world_revision: Required[float]
    world_release: Required[float]
    scenario_id: Required[str]
    scenario_revision: Required[float]
    binding: Required[dict[str, Any]]


class RpgWorldLibraryResponse(TypedDict, total=False):
    ok: Required[bool]
    worlds: Required[list[RpgWorldSummary]]
    scenarios: Required[list[RpgScenarioSummary]]
    campaigns: Required[list[RpgWorldCampaignSummary]]
    generation_runs: Required[list[RpgWorldGenerationRun]]


class RpgLaunchResponse(TypedDict, total=False):
    ok: Required[bool]
    session_id: NotRequired[str]
    status: NotRequired[str]
    session: NotRequired[dict[str, Any]]
    game: NotRequired[dict[str, Any]]
    environment_snapshot: NotRequired[dict[str, Any]]
    creation_server_trace: NotRequired[dict[str, Any]]
    creation_job: NotRequired[dict[str, Any]]
    creation_progress: NotRequired[dict[str, Any]]
    error: NotRequired[str]


class RpgLoadoutActionResponse(RpgLaunchResponse, total=False):
    event: NotRequired[dict[str, Any]]


class RpgSessionMutationResponse(TypedDict, total=False):
    ok: Required[bool]
    session_id: NotRequired[str]
    session: NotRequired[dict[str, Any]]
    archived: NotRequired[bool]
    deleted: NotRequired[str]
    error: NotRequired[str]


class RpgPresetSummary(TypedDict, total=False):
    preset_id: Required[str]
    name: Required[str]
    description: Required[str]
    kind: Required[str]
    level: NotRequired[float]
    location: NotRequired[str]
    clone_on_start: NotRequired[bool]


class RpgSessionListResponse(TypedDict, total=False):
    ok: Required[bool]
    sessions: Required[list[dict[str, Any]]]
    presets: NotRequired[list[RpgPresetSummary]]


class RpgPresetListResponse(TypedDict, total=False):
    ok: Required[bool]
    presets: Required[list[RpgPresetSummary]]


class HermesRpgSequenceStateResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    state: NotRequired[dict[str, Any] | None]
    error: NotRequired[str | None]


class HermesRpgSequenceResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    validation: NotRequired[dict[str, Any]]
    sequence: NotRequired[dict[str, Any]]
    gate: NotRequired[dict[str, Any] | None]
    checkpoint: NotRequired[dict[str, Any] | None]
    loop_guard: NotRequired[dict[str, Any] | None]
    assist_mode: NotRequired[dict[str, Any] | None]
    sequence_state: NotRequired[dict[str, Any] | None]
    state_changed: NotRequired[bool]


class HermesRpgExecutionLedgerItem(TypedDict, total=False):
    execution_id: NotRequired[str]
    created_at: NotRequired[str]
    session_id: NotRequired[str | None]
    sequence_id: NotRequired[str | None]
    item_id: NotRequired[str | None]
    command_text: NotRequired[str | None]
    approval_source: NotRequired[str | None]
    checkpoint_reason: NotRequired[str | None]
    state_changed: NotRequired[bool]
    result_summary: NotRequired[str | None]
    error: NotRequired[str | None]


class HermesRpgExecutionLedgerResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    items: NotRequired[list[HermesRpgExecutionLedgerItem]]
    count: NotRequired[float]


class HermesRpgApprovedFlowConfig(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    feature_flag: NotRequired[str]
    default_enabled: NotRequired[bool]
    enabled: NotRequired[bool]
    requires_payload_enabled: NotRequired[bool]
    simulation_owned: NotRequired[bool]


class HermesRpgApprovedFlowResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    enabled: NotRequired[bool]
    config: NotRequired[HermesRpgApprovedFlowConfig]
    flow: NotRequired[dict[str, Any]]
    readout: NotRequired[dict[str, Any]]
    ledger_entry: NotRequired[dict[str, Any]]
    error: NotRequired[str | None]
    state_changed: NotRequired[bool]


class HermesTestResponseResultResult(TypedDict, total=False):
    response: NotRequired[str]


class HermesTestResponseResult(TypedDict, total=False):
    backend: NotRequired[str]
    result: NotRequired[HermesTestResponseResultResult]


class HermesTestResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    dry_run: NotRequired[bool]
    result: NotRequired[HermesTestResponseResult]
    error: NotRequired[str | None]


class HermesRpgTurnReadoutResponseTurn(TypedDict, total=False):
    turn_id: NotRequired[str | float | None]
    command: NotRequired[str]
    category: NotRequired[str]
    narration_present: NotRequired[bool]


class HermesRpgTurnReadoutResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    read_only: NotRequired[bool]
    source: NotRequired[str]
    session_id: NotRequired[str | None]
    turn: NotRequired[HermesRpgTurnReadoutResponseTurn]
    systems: NotRequired[list[str]]
    effect_count: NotRequired[float]
    grounding_status: NotRequired[str]
    notes: NotRequired[list[str]]
    error: NotRequired[str | None]


class HermesRpgSuggestion(TypedDict, total=False):
    id: NotRequired[str]
    label: NotRequired[str]
    command: NotRequired[str]
    kind: NotRequired[str]
    risk: NotRequired[str]
    requires_user_click: NotRequired[bool]
    direct_state_write: NotRequired[bool]
    processed_by: NotRequired[str]
    reason: NotRequired[str]


class HermesRpgSuggestionsResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    read_only: NotRequired[bool]
    source: NotRequired[str]
    suggestions: NotRequired[list[HermesRpgSuggestion]]
    count: NotRequired[float]
    error: NotRequired[str | None]


class HermesApprovalResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    approved: NotRequired[bool]
    error: NotRequired[str | None]
    mode: NotRequired[str]
    request: NotRequired[dict[str, Any]]


class HermesRouteDecisionResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    source: NotRequired[str]
    mode: NotRequired[str]
    role: NotRequired[str]
    owner: NotRequired[str]
    review_required: NotRequired[bool]
    capabilities: NotRequired[list[str]]
    boundary: NotRequired[str]
    error: NotRequired[str | None]


class HermesCandidatePreview(TypedDict, total=False):
    name: NotRequired[str]
    target: NotRequired[str]
    before: NotRequired[dict[str, Any]]
    after: NotRequired[dict[str, Any]]
    risk: NotRequired[str]
    note: NotRequired[str]


class HermesCandidatePreviewResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    candidate: NotRequired[HermesCandidatePreview]
    preview_only: NotRequired[bool]


class HermesRecentResponse(TypedDict, total=False):
    ok: NotRequired[bool]
    items: NotRequired[list[dict[str, Any]]]
    count: NotRequired[float]
    source: NotRequired[str]

