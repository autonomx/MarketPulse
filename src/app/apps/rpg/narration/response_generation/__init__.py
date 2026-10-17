"""Canonical RPG response-generation contracts and policies.

Simulation remains authoritative. Modules in this package operate on presentation
contracts, evidence, proposals, validation, rendering, and approved delivery only.
"""

from .baseline import BaselineMetrics, BaselineObservation, BaselineScenario, evaluate_baseline, load_baseline_scenarios, validate_scenarios
from .candidate_ranker import CandidateRanker, NoEligibleCandidateError
from .claim_ledger import ClaimLedger, ClaimRecord, derive_claim_ledger
from .context_compiler import ContextTrace, EvidenceCard, NarrationContext, NarrationContextCompiler
from .contracts import AgencyEffect, CandidateSource, GateDecision, RenderedResponse, ResponseCandidate, ResponseMode, ResponseRequest, Reversibility, SectionType, SemanticResponsePlan, SemanticSection
from .eligibility import EligibilityPolicy, eligibility_reasons
from .fallback_library import DeterministicFallbackLibrary, FallbackInput
from .forward_motion import ForwardMotionPlan, ForwardMotionPolicy, RecoveryHistoryEntry, validate_agency
from .hermes_adapter import HermesCircuitBreaker, HermesEvidence, HermesProposal, HermesRecoveryResult, RpgHermesRecoveryAdapter
from .intent_affordance import IntentAnalysis, IntentHypothesis, NarrativeAffordanceClassifier
from .legacy_bridge import CANONICAL_NARRATION_SOURCE, narrate_scene_canonical
from .orchestration import RpgResponseGenerator, build_runtime_shadow_report, build_world_scene_shadow_report, semantic_plan_from_legacy_payload
from .performance import BlockingPathDecision, LatencyBenchmark, LatencyTrace, VersionedResponseCache, blocking_path_decision, evaluate_latency_benchmark
from .production_pipeline import HermesSidecarRecoveryClient, ProfileBoundProvider, RpgProductionResponsePipeline
from .profiled_generator import ProfiledRpgResponseGenerator
from .profiles import DeliveryMode, ResponseGenerationProfile, ResponseProfileRegistry, validate_response_profile
from .proposal_policy import ProposalBudget, ProposalDecision, ProposalPolicy, ProposalPolicyResult, ProposalPromotionEvent, ProposalRisk, ProposalStore, WorldProposal
from .quality_gate import QualityGate, QualityReport
from .recovery import LocalRecoveryAnalysis, LocalRecoveryCoordinator
from .renderer import ResponseRenderer
from .retrieval import EvidenceRecord, LocalKnowledgeRetriever, RetrievalResult, build_retrieval_sources
from .semantic_plan import SemanticPlanValidation, validate_semantic_plan
from .strict_proposal_policy import StrictProposalPolicy
from .truth_lifetime import LifetimeTransition, SoftTruthRecord, TruthClass, TruthLifetime
from .validated_delivery import ApprovedDeliveryUnit, DeliveryCheckpoint, DeliveryState, ValidatedDeliverySession, approved_delivery_units, validate_publishable_response

__all__ = [
    "AgencyEffect", "ApprovedDeliveryUnit", "BaselineMetrics", "BaselineObservation", "BaselineScenario", "BlockingPathDecision", "CANONICAL_NARRATION_SOURCE", "CandidateRanker", "CandidateSource", "ClaimLedger", "ClaimRecord", "ContextTrace", "DeliveryCheckpoint", "DeliveryMode", "DeliveryState", "DeterministicFallbackLibrary", "EligibilityPolicy", "EvidenceCard", "EvidenceRecord", "FallbackInput", "ForwardMotionPlan", "ForwardMotionPolicy", "GateDecision", "HermesCircuitBreaker", "HermesEvidence", "HermesProposal", "HermesRecoveryResult", "HermesSidecarRecoveryClient", "IntentAnalysis", "IntentHypothesis", "LatencyBenchmark", "LatencyTrace", "LifetimeTransition", "LocalKnowledgeRetriever", "LocalRecoveryAnalysis", "LocalRecoveryCoordinator", "NarrationContext", "NarrationContextCompiler", "NarrativeAffordanceClassifier", "NoEligibleCandidateError", "ProfileBoundProvider", "ProfiledRpgResponseGenerator", "ProposalBudget", "ProposalDecision", "ProposalPolicy", "ProposalPolicyResult", "ProposalPromotionEvent", "ProposalRisk", "ProposalStore", "QualityGate", "QualityReport", "RecoveryHistoryEntry", "RenderedResponse", "ResponseCandidate", "ResponseGenerationProfile", "ResponseMode", "ResponseProfileRegistry", "ResponseRenderer", "ResponseRequest", "RetrievalResult", "Reversibility", "RpgHermesRecoveryAdapter", "RpgProductionResponsePipeline", "RpgResponseGenerator", "SectionType", "SemanticPlanValidation", "SemanticResponsePlan", "SemanticSection", "SoftTruthRecord", "StrictProposalPolicy", "TruthClass", "TruthLifetime", "ValidatedDeliverySession", "VersionedResponseCache", "WorldProposal", "approved_delivery_units", "blocking_path_decision", "build_retrieval_sources", "build_runtime_shadow_report", "build_world_scene_shadow_report", "derive_claim_ledger", "eligibility_reasons", "evaluate_baseline", "evaluate_latency_benchmark", "load_baseline_scenarios", "narrate_scene_canonical", "semantic_plan_from_legacy_payload", "validate_agency", "validate_publishable_response", "validate_response_profile", "validate_scenarios", "validate_semantic_plan",
]
