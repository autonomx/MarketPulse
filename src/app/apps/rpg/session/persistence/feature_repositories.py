"""Repository registrations owned by the RPG feature."""
from __future__ import annotations

from typing import Any

from app.persistence.repository_registry import RepositorySpec

from app.apps.rpg.foundation.persistence.rpg_campaign_bible_repository import PostgresRpgCampaignBibleRepository
from app.apps.rpg.foundation.persistence.rpg_campaign_genesis_repository import PostgresRpgCampaignGenesisRepository
from app.apps.rpg.foundation.persistence.rpg_hermes_research_repository import PostgresRpgHermesResearchRepository
from app.apps.rpg.foundation.persistence.rpg_map_instance_repository import PostgresRpgMapInstanceRepository
from app.apps.rpg.narration.persistence.rpg_narrative_delivery_repository import PostgresRpgNarrativeDeliveryRepository
from app.apps.rpg.narration.persistence.rpg_narrative_response_repository import PostgresRpgNarrativeResponseRepository
from app.apps.rpg.narration.persistence.rpg_narrative_retirement_repository import PostgresRpgNarrativeRetirementRepository
from app.apps.rpg.world.persistence.rpg_npc_spatial_repository import PostgresRpgNpcSpatialRepository
from app.apps.rpg.world.persistence.rpg_observer_repository import PostgresRpgObserverRepository
from app.apps.rpg.foundation.persistence.rpg_repository import PostgresRpgRepository
from app.apps.rpg.genesis.persistence.rpg_trusted_world_scenario_repository import PostgresTrustedRpgWorldScenarioRepository
from app.apps.rpg.foundation.persistence.rpg_world_forge_repository import PostgresRpgWorldForgeRepository
from app.apps.rpg.foundation.persistence.rpg_world_generation_repository import PostgresRpgWorldGenerationRepository
from app.apps.rpg.foundation.persistence.rpg_world_library_repository import PostgresRpgWorldLibraryRepository


def _foreground_submissions(connection: Any) -> Any:
    # Imported on first use: only foreground turns need it, not gateway startup.
    from app.apps.rpg.foundation.persistence.foreground_submission_repository import PostgresForegroundSubmissionRepository

    return PostgresForegroundSubmissionRepository(connection)


RPG_REPOSITORY_SPECS = (
    RepositorySpec(PostgresRpgRepository, PostgresRpgRepository, "rpg"),
    RepositorySpec(PostgresRpgCampaignBibleRepository, PostgresRpgCampaignBibleRepository, "campaign_bibles"),
    RepositorySpec(PostgresRpgCampaignGenesisRepository, PostgresRpgCampaignGenesisRepository, "campaign_genesis"),
    RepositorySpec(PostgresRpgWorldForgeRepository, PostgresRpgWorldForgeRepository, "world_forge"),
    RepositorySpec(PostgresTrustedRpgWorldScenarioRepository, PostgresTrustedRpgWorldScenarioRepository, "world_scenarios"),
    RepositorySpec(PostgresRpgWorldGenerationRepository, PostgresRpgWorldGenerationRepository, "world_generation"),
    RepositorySpec(PostgresRpgWorldLibraryRepository, PostgresRpgWorldLibraryRepository, "world_library"),
    RepositorySpec(PostgresRpgMapInstanceRepository, PostgresRpgMapInstanceRepository, "map_instances"),
    RepositorySpec(PostgresRpgNpcSpatialRepository, PostgresRpgNpcSpatialRepository, "npc_spatial"),
    RepositorySpec(PostgresRpgObserverRepository, PostgresRpgObserverRepository, "observers"),
    RepositorySpec(PostgresRpgHermesResearchRepository, PostgresRpgHermesResearchRepository, "hermes_research"),
    RepositorySpec(PostgresRpgNarrativeResponseRepository, PostgresRpgNarrativeResponseRepository, "narrative_responses"),
    RepositorySpec(PostgresRpgNarrativeDeliveryRepository, PostgresRpgNarrativeDeliveryRepository, "narrative_deliveries"),
    RepositorySpec(PostgresRpgNarrativeRetirementRepository, PostgresRpgNarrativeRetirementRepository, "narrative_retirement"),
    RepositorySpec("rpg.foreground_submissions", _foreground_submissions, "foreground_submissions"),
)
