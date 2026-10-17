from __future__ import annotations

from typing import Any

from .canon import CreatorCanonFact, CreatorCanonState
from .schema import AdventureSetup


class StartupGenerationPipeline:
    """Deterministic startup materialization pipeline.

    This v1 implementation is schema-driven and deterministic. It does not
    require live LLM generation. Future phases can add LLM-assisted expansion
    through the existing LLMGateway, but the state contract remains explicit.
    """

    def __init__(
        self,
        llm_gateway: Any,
        coherence_core: Any,
        creator_canon_state: CreatorCanonState | None = None,
    ) -> None:
        self.llm_gateway = llm_gateway
        self.coherence_core = coherence_core
        self.creator_canon_state = creator_canon_state or CreatorCanonState()

    def resolve_starting_context(self, setup: AdventureSetup) -> dict:
        location_id = setup.starting_location_id
        if not location_id and setup.locations:
            location_id = setup.locations[0].location_id

        npc_ids = list(setup.starting_npc_ids)
        if not npc_ids and setup.npc_seeds:
            npc_ids = [npc.npc_id for npc in setup.npc_seeds[:3]]

        return {
            "location_id": location_id,
            "npc_ids": npc_ids,
        }

    def generate(self, setup: AdventureSetup) -> dict:
        setup.validate()
        world_frame = self.generate_world_frame(setup)
        opening = self.generate_opening_situation(setup, world_frame)
        npcs = self.generate_seed_npcs(setup, world_frame)
        factions = self.generate_seed_factions(setup, world_frame)
        locations = self.generate_seed_locations(setup, world_frame)
        threads = self.generate_initial_threads(setup, opening)
        generated = {
            "world_frame": world_frame,
            "opening_situation": opening,
            "seed_npcs": npcs,
            "seed_factions": factions,
            "seed_locations": locations,
            "initial_threads": threads,
        }
        self.materialize_into_coherence(generated)
        generated["initial_scene_anchor"] = self.create_initial_scene_anchor(generated)
        return generated

    def generate_world_frame(self, setup: AdventureSetup) -> dict:
        return {
            "setup_id": setup.setup_id,
            "title": setup.title,
            "genre": setup.genre,
            "setting": setup.setting,
            "premise": setup.premise,
            "hard_rules": list(setup.hard_rules),
            "soft_tone_rules": list(setup.soft_tone_rules),
            "canon_notes": list(setup.canon_notes),
            "forbidden_content": list(setup.forbidden_content),
        }

    def generate_opening_situation(self, setup: AdventureSetup, world_frame: dict) -> dict:
        context = self.resolve_starting_context(setup)

        first_location = setup.setting
        if context["location_id"]:
            for location in setup.locations:
                if location.location_id == context["location_id"]:
                    first_location = location.name
                    break
            else:
                first_location = context["location_id"]

        first_npcs = []
        npc_lookup = {npc.npc_id: npc for npc in setup.npc_seeds}
        for npc_id in context["npc_ids"]:
            npc = npc_lookup.get(npc_id)
            if npc:
                first_npcs.append(npc.name)

        return {
            "location": first_location,
            "summary": f"{setup.premise} The story opens in {first_location}.",
            "present_actors": first_npcs,
            "active_tensions": list(setup.hard_rules[:2]) or ["The world has expectations the player must navigate."],
        }

    def generate_seed_npcs(self, setup: AdventureSetup, world_frame: dict) -> list[dict]:
        return [npc.to_dict() for npc in setup.npc_seeds]

    def generate_seed_factions(self, setup: AdventureSetup, world_frame: dict) -> list[dict]:
        return [faction.to_dict() for faction in setup.factions]

    def generate_seed_locations(self, setup: AdventureSetup, world_frame: dict) -> list[dict]:
        return [location.to_dict() for location in setup.locations]

    def generate_initial_threads(self, setup: AdventureSetup, opening_situation: dict) -> list[dict]:
        return [
            {
                "thread_id": f"setup_thread:{setup.setup_id}:opening",
                "title": setup.premise,
                "status": "unresolved",
                "priority": "high",
                "source": "startup_pipeline",
                "summary": opening_situation["summary"],
            }
        ]

    def materialize_into_coherence(self, generated: dict) -> None:
        from app.apps.rpg.narration.coherence.models import FactRecord, SceneAnchor, ThreadRecord

        world_frame = generated["world_frame"]
        opening = generated["opening_situation"]

        creator_facts = [
            CreatorCanonFact(
                fact_id=f"setup:{world_frame['setup_id']}:genre",
                subject="world",
                predicate="genre",
                value=world_frame["genre"],
            ),
            CreatorCanonFact(
                fact_id=f"setup:{world_frame['setup_id']}:setting",
                subject="world",
                predicate="setting",
                value=world_frame["setting"],
            ),
            CreatorCanonFact(
                fact_id=f"setup:{world_frame['setup_id']}:premise",
                subject="world",
                predicate="premise",
                value=world_frame["premise"],
            ),
        ]
        for fact in creator_facts:
            self.creator_canon_state.add_fact(fact)
        self.creator_canon_state.setup_id = world_frame["setup_id"]
        # Canon application is owned by whatever starts the adventure.
        # This pipeline populates canonical creator state only.

        for faction in generated["seed_factions"]:
            self.coherence_core.insert_fact(
                FactRecord(
                    fact_id=f"faction:{faction['faction_id']}:exists",
                    category="world",
                    subject=faction["faction_id"],
                    predicate="exists",
                    value=True,
                    authority="creator_canon",
                    status="confirmed",
                    metadata={"name": faction["name"]},
                )
            )

        for location in generated["seed_locations"]:
            self.coherence_core.insert_fact(
                FactRecord(
                    fact_id=f"location:{location['location_id']}:name",
                    category="world",
                    subject=location["location_id"],
                    predicate="name",
                    value=location["name"],
                    authority="creator_canon",
                    status="confirmed",
                    metadata={"description": location["description"]},
                )
            )

        for npc in generated["seed_npcs"]:
            self.coherence_core.insert_fact(
                FactRecord(
                    fact_id=f"npc:{npc['npc_id']}:name",
                    category="world",
                    subject=npc["npc_id"],
                    predicate="name",
                    value=npc["name"],
                    authority="creator_canon",
                    status="confirmed",
                    metadata={"role": npc["role"], "must_survive": npc.get("must_survive", False)},
                )
            )
            if npc.get("location_id"):
                self.coherence_core.insert_fact(
                    FactRecord(
                        fact_id=f"{npc['npc_id']}:location",
                        category="world",
                        subject=npc["npc_id"],
                        predicate="location",
                        value=npc["location_id"],
                        authority="creator_canon",
                        status="confirmed",
                    )
                )

        for thread in generated["initial_threads"]:
            self.coherence_core.insert_thread(
                ThreadRecord(
                    thread_id=thread["thread_id"],
                    title=thread["title"],
                    status="unresolved",
                    priority=thread.get("priority", "normal"),
                    notes=[thread.get("summary", "")],
                    metadata={"source": "startup_pipeline"},
                )
            )

        self.coherence_core.push_anchor(
            SceneAnchor(
                anchor_id=f"setup_anchor:{world_frame['setup_id']}",
                tick=0,
                location=opening.get("location"),
                present_actors=list(opening.get("present_actors", [])),
                active_tensions=list(opening.get("active_tensions", [])),
                unresolved_thread_ids=[t["thread_id"] for t in generated["initial_threads"]],
                summary=opening.get("summary", ""),
                scene_fact_ids=["scene:location"],
                source_event_id="startup_pipeline",
                metadata={"setup_id": world_frame["setup_id"]},
            )
        )

    def create_initial_scene_anchor(self, generated: dict) -> dict:
        opening = generated["opening_situation"]
        threads = generated["initial_threads"]
        return {
            "anchor_id": f"setup_anchor:{generated['world_frame']['setup_id']}",
            "tick": 0,
            "location": opening.get("location"),
            "present_actors": list(opening.get("present_actors", [])),
            "active_tensions": list(opening.get("active_tensions", [])),
            "unresolved_thread_ids": [t["thread_id"] for t in threads],
            "summary": opening.get("summary", ""),
            "metadata": {"source": "startup_pipeline"},
        }

    # ── Targeted regeneration helpers ──────────────────────────────────
    # Each method regenerates a single section while leaving the others
    # untouched.  They all delegate to the core generate_* methods so
    # the logic stays in one place.

# Phase 18.3A — Seed origin marking and expansion caps

