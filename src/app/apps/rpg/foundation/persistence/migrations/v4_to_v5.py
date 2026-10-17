"""Phase 9.1 — Migration v4 to v5.

Adds party_state to player state for saved games that predate the party system.
"""


def migrate_v4_to_v5(package):
    """Migrate an RPG save package from schema version 4 to 5.
    
    Ensures party_state is present in the simulation state.
    """
    state = package.get("simulation_state") or {}
    player = state.get("player_state") or {}

    if "party_state" not in player:
        player["party_state"] = {
            "companions": [],
            "max_size": 3,
        }

    state["player_state"] = player
    package["simulation_state"] = state
    package["schema_version"] = 5

    return package