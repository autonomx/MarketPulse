"""Campaign genesis contract helpers for deterministic RPG session creation."""

from .bootstrap import bootstrap_session_from_compiled_genesis
from .compiler import GENESIS_COMPILER_VERSION, compile_campaign_genesis
from .contract import (
    CAMPAIGN_GENESIS_CONTRACT_VERSION,
    DEFAULT_GENESIS_CREATED_BY,
    CampaignGenesisContract,
    GenesisDrivers,
    GenesisIdentity,
    GenesisInitialStats,
    GenesisMotivation,
    GenesisStoryOptions,
    GenesisSystemOptions,
    GenesisTalent,
    GenesisWorldOptions,
    canonical_genesis_payload,
    genesis_contract_hash,
)
from .legacy_adapter import (
    adapt_genesis_payload_to_new_game_payload,
    attach_genesis_to_created_session,
)
from .pipeline_adapter import (
    attach_compiled_genesis_to_session,
    create_new_game_from_genesis_payload,
)

__all__ = [
    "CAMPAIGN_GENESIS_CONTRACT_VERSION",
    "DEFAULT_GENESIS_CREATED_BY",
    "GENESIS_COMPILER_VERSION",
    "CampaignGenesisContract",
    "GenesisDrivers",
    "GenesisIdentity",
    "GenesisInitialStats",
    "GenesisMotivation",
    "GenesisStoryOptions",
    "GenesisSystemOptions",
    "GenesisTalent",
    "GenesisWorldOptions",
    "adapt_genesis_payload_to_new_game_payload",
    "attach_compiled_genesis_to_session",
    "attach_genesis_to_created_session",
    "bootstrap_session_from_compiled_genesis",
    "canonical_genesis_payload",
    "compile_campaign_genesis",
    "create_new_game_from_genesis_payload",
    "genesis_contract_hash",
]
