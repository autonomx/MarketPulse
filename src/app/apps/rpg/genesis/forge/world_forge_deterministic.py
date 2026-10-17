"""Provider-free World Forge topic generator used for tests and fallback launches."""
from __future__ import annotations

import hashlib
import random
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_domains import generate_deterministic_domain, is_structured_domain
from .world_forge_generation import GeneratedTopic


def _slug(value: str) -> str:
    return "_".join("".join(ch.casefold() if ch.isalnum() else " " for ch in value).split())


def _summary(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _keywords(*values: str) -> list[str]:
    out: list[str] = []
    for value in values:
        for word in "".join(ch.casefold() if ch.isalnum() else " " for ch in value).split():
            if len(word) >= 4 and word not in out:
                out.append(word)
    return out[:24]


def _document(
    node: CampaignTopicNode,
    title: str,
    full_text: str,
    *,
    visibility: str | None = None,
    entity_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "document_id": f"lore:{_slug(title)}",
        "topic_id": node.topic_id,
        "title": title,
        "full_text": full_text,
        "summary_500": _summary(full_text, 500),
        "summary_120": _summary(full_text, 120),
        "facts": [],
        "entities": list(entity_ids),
        "relationships": [],
        "keywords": _keywords(title, full_text),
        "visibility": visibility or node.visibility,
        "canon_revision": 0,
    }


_KAVRIX_REGIONS = (
    ("aertos", "Aertos", "A tradition-bound kingdom of elemental academies, ancient forests, and living wards."),
    ("solara", "Solara", "An innovative kingdom of glass towers, brass machinery, and harnessed arcane power."),
    ("grimstone_hold", "Grimstone Hold", "A dwarven fortress-kingdom carved into the Spinebreaker Mountains."),
    ("sylvaniar_glade", "Sylvaniar Glade", "An elven realm hidden in a sentient forest of living halls and sentinel groves."),
    ("aok", "Aok, the Celestial Isle", "A floating crystal isle whose guardians command celestial energy."),
)
_CLASSIC_REGIONS = (
    ("market_road", "Market Road", "A rain-dark trade district linking the town market, inns, and caravan yards."),
    ("whispering_woods", "Whispering Woods", "An old forest where paths move and rumors travel faster than riders."),
    ("spinebreaker_mountains", "Spinebreaker Mountains", "A hard northern range cut by mines, passes, and sealed ruins."),
    ("east_marches", "East Marches", "Border fields shaped by raids, toll roads, and disputed watchtowers."),
    ("celestial_reach", "Celestial Reach", "High plateaus where observatories track unstable magic in the sky."),
)


class DeterministicWorldForgeGenerator:
    """Generate structured canon without provider calls."""

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        local_seed = int(hashlib.sha256(f"{seed}:{node.topic_id}".encode()).hexdigest()[:16], 16)
        rng = random.Random(local_seed)
        template = str(campaign_context.get("campaign_template") or "classic_fantasy")
        if is_structured_domain(node.topic_id):
            return generate_deterministic_domain(
                node,
                template=template,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
                rng=rng,
            )
        if node.category == "regions":
            return self._regions(node, template, rng)
        if node.category == "factions":
            return self._factions(node, template, rng)
        if node.category == "locations":
            return self._locations(node, template, campaign_context, rng)
        if node.category == "npcs":
            return self._npcs(node, template, rng)
        if node.category == "story":
            return self._story_threads(node, template)
        return self._lore(node, template, campaign_context)

    def _lore(self, node: CampaignTopicNode, template: str, context: Mapping[str, Any]) -> GeneratedTopic:
        realm = "Kavrix" if "summoned" in template else str(context.get("realm_name") or "Eldervale")
        descriptions = {
            "realm": f"{realm} is a living realm whose regions, institutions, conflicts, and exceptional heroes share one connected history.",
            "cosmology": f"Reality in {realm} is layered. Mortal places, divine domains, and unstable thresholds obey explicit laws, and crossing them carries a cost.",
            "magic_technology": "Magic is constrained by source, method, cost, range, and consequence. Technology may preserve or amplify it but cannot erase those constraints.",
            "history": f"The modern order of {realm} emerged from ruptures, contested succession, and alliances formed against threats no single power could survive.",
            "calendar": "The calendar records eras, years, seasons, days, and event order so ages, travel, offices, and historical sequences remain consistent.",
            "cultures": "Cultures are defined by practice, duty, taboo, material life, and relationships with neighboring peoples rather than one visual trait.",
            "institutions": "Guilds, academies, temples, courts, and orders translate belief into recruitment, law, education, and daily power.",
            "pantheon": "Religions distinguish public doctrine, private revelation, disputed interpretation, and secrets known only to initiates or supernatural beings.",
            "hero_system": "Exceptional heroes are integrated into politics and history, with documented origins, limits, social meaning, and institutions seeking their loyalty.",
            "current_conflicts": "Current conflicts have named participants, incompatible goals, material stakes, escalation clocks, and locations where consequences enter play.",
        }
        text = descriptions.get(node.topic_id, f"{node.title} establishes structured campaign canon for {realm}.")
        entity_id = f"realm:{_slug(realm)}" if node.topic_id == "realm" else f"topic:{node.topic_id}"
        fact = {
            "id": f"fact:{node.topic_id}:foundation",
            "subject": entity_id,
            "predicate": "foundation",
            "object": text,
            "content": text,
            "authority": "generated_proposal",
            "approved_authority": "objective_canon",
            "visibility": "public" if node.visibility == "public" else node.visibility,
            "entity_refs": [entity_id],
            "category": node.category,
        }
        entity = {"id": entity_id, "name": realm if node.topic_id == "realm" else node.title, "kind": "realm" if node.topic_id == "realm" else "lore_topic", "visibility": node.visibility}
        return GeneratedTopic(node.topic_id, (_document(node, entity["name"], text, entity_ids=(entity_id,)),), (entity,), (fact,), provenance={"generator": "deterministic_world_forge_v1", "template": template})

    def _regions(self, node: CampaignTopicNode, template: str, rng: random.Random) -> GeneratedTopic:
        rows = list(_KAVRIX_REGIONS if "summoned" in template else _CLASSIC_REGIONS)
        while len(rows) < node.target_count:
            index = len(rows) + 1
            rows.append((f"frontier_{index}", f"Frontier {index}", f"A contested frontier marked by route {rng.randint(2, 9)} and an unresolved claim."))
        entities, facts, documents = [], [], []
        realm_id = "realm:kavrix" if "summoned" in template else "realm:eldervale"
        for slug, name, description in rows[: node.target_count]:
            entity_id = f"region:{slug}"
            entities.append({"id": entity_id, "name": name, "kind": "region", "realm_id": realm_id, "visibility": "public"})
            facts.append({"id": f"fact:{slug}:overview", "content": description, "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "public", "entity_refs": [entity_id, realm_id]})
            documents.append(_document(node, name, description, visibility="public", entity_ids=(entity_id, realm_id)))
        return GeneratedTopic(node.topic_id, tuple(documents), tuple(entities), tuple(facts), provenance={"generator": "deterministic_world_forge_v1"})

    def _factions(self, node: CampaignTopicNode, template: str, rng: random.Random) -> GeneratedTopic:
        rows = list((
            ("obsidian_order", "The Obsidian Order", "discipline", "control summoned heroes through law and service"),
            ("infernal_syndicate", "The Infernal Syndicate", "ambition", "turn unstable power into private dominion"),
            ("silent_chorus", "The Silent Chorus", "ritual silence", "prepare the return of the Unmaker"),
            ("academy_of_heroes", "Academy of Heroes", "instruction", "train and classify summoned champions"),
            ("adventurers_guild", "Adventurer's Guild", "pragmatism", "match dangerous work with capable outsiders"),
        ) if "summoned" in template else (
            ("northern_watch", "Northern Watch", "duty", "hold roads against raiders and winter threats"),
            ("merchant_compact", "Merchant Compact", "profit", "protect caravan routes and favorable tolls"),
            ("ash_covenant", "Ash Covenant", "secrecy", "recover sealed relics before rival powers"),
            ("academy", "Royal Academy", "knowledge", "study magic without destabilizing the realm"),
            ("adventurers_guild", "Adventurer's Guild", "pragmatism", "organize dangerous work and local bounties"),
        ))
        while len(rows) < node.target_count:
            index = len(rows) + 1
            rows.append((f"faction_{index}", f"Faction {index}", "survival", f"secure leverage over route {rng.randint(2, 9)}"))
        entities, facts, documents, relationships = [], [], [], []
        for slug, name, value, goal in rows[: node.target_count]:
            entity_id = f"faction:{slug}"
            text = f"{name} values {value} and seeks to {goal}. Its public mission and private methods do not always agree."
            entities.append({"id": entity_id, "name": name, "kind": "faction", "values": [value], "goals": [goal], "visibility": "partially_known"})
            facts.append({"id": f"fact:{slug}:goal", "content": text, "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "partially_known", "entity_refs": [entity_id]})
            documents.append(_document(node, name, text, visibility="partially_known", entity_ids=(entity_id,)))
        ids = [str(row["id"]) for row in entities]
        for left, right in zip(ids, ids[1:]):
            relationships.append({"id": f"relationship:{_slug(left)}:rivals:{_slug(right)}", "source_id": left, "target_id": right, "kind": "rivals", "content": f"{left} and {right} compete over policy, recruits, or territory.", "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "partially_known", "entity_refs": [left, right]})
        return GeneratedTopic(node.topic_id, tuple(documents), tuple(entities), tuple(facts), tuple(relationships), provenance={"generator": "deterministic_world_forge_v1"})

    def _locations(self, node: CampaignTopicNode, template: str, context: Mapping[str, Any], rng: random.Random) -> GeneratedTopic:
        opening = str(context.get("starting_location") or "rusty_flagon_tavern")
        rows = list((
            (opening, "Vanta Gate", "region:aertos", "A black summoning chamber ringed by mirror traps, delay glyphs, and sacrificial channels."),
            ("academy_courtyard", "Academy Courtyard", "region:aertos", "A warded court where summoned heroes are registered beneath elemental banners."),
            ("solara_research_hall", "Solara Research Hall", "region:solara", "A glass-and-brass laboratory built to measure unfamiliar powers."),
            ("grimstone_gate", "Grimstone Gate", "region:grimstone_hold", "A mountain gate of runed stone guarded by oath-bound smiths."),
            ("sentinel_groves", "Sentinel Groves", "region:sylvaniar_glade", "Living paths and concealed watchtowers guard the elven heartwood."),
            ("aok_crystal_spire", "Aok Crystal Spire", "region:aok", "A celestial observatory suspended above waterfalls that vanish into cloud."),
        ) if "summoned" in template else (
            (opening, "Rusty Flagon Tavern", "region:market_road", "A rain-worn tavern where Bran serves travelers and the notice board changes daily."),
            ("market_district", "Market District", "region:market_road", "A crowded square of workshops, caravan stalls, and guarded warehouses."),
            ("old_quarry", "Old Quarry", "region:east_marches", "An abandoned quarry where pale light leaks from a sealed fissure."),
            ("northern_road", "Northern Road", "region:spinebreaker_mountains", "A muddy trade road marked by broken milestones and fresh hoofprints."),
            ("watchtower", "Old Watchtower", "region:east_marches", "A damaged border tower that still commands the surrounding fields."),
        ))
        while len(rows) < node.target_count:
            index = len(rows) + 1
            rows.append((f"location_{index}", f"Location {index}", "region:market_road", f"A location shaped by pressure {rng.randint(2, 9)}, one service, and one unresolved secret."))
        entities, facts, documents = [], [], []
        for slug, name, region_id, text in rows[: node.target_count]:
            entity_id = f"location:{_slug(slug)}"
            entities.append({"id": entity_id, "name": name, "kind": "location", "region_id": region_id, "dossier_status": "complete", "sensory_profile": text, "visibility": "partially_known"})
            facts.append({"id": f"fact:{_slug(slug)}:location", "content": text, "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "partially_known", "entity_refs": [entity_id, region_id]})
            documents.append(_document(node, name, text, visibility="partially_known", entity_ids=(entity_id, region_id)))
        return GeneratedTopic(node.topic_id, tuple(documents), tuple(entities), tuple(facts), provenance={"generator": "deterministic_world_forge_v1"})

    def _npcs(self, node: CampaignTopicNode, template: str, rng: random.Random) -> GeneratedTopic:
        base = _summoned_npcs() if "summoned" in template else _classic_npcs()
        while len(base) < node.target_count:
            index = len(base) + 1
            base.append({"id": f"npc:generated_{index}", "name": f"Generated NPC {index}", "appearance": f"A distinctive traveler carrying token {rng.randint(100, 999)}.", "personality": "Goal-directed, observant, and shaped by a local obligation.", "backstory": "Their history connects one faction, one location, and one current conflict.", "goals": [f"resolve obligation {index}"], "motives": ["survival", "belonging"], "speech_style": "specific, concise, locally grounded", "faction_ids": [], "location_id": "", "secrets": [], "known_facts": []})
        entities, facts, documents, rules = [], [], [], []
        for dossier in base[: node.target_count]:
            row = {**dossier, "kind": "npc", "dossier_status": "complete", "current_emotional_state": "alert", "visibility": "game_master_canon"}
            entities.append(row)
            text = f"{row['appearance']} {row['personality']} {row['backstory']}"
            facts.append({"id": f"fact:{_slug(row['id'])}:dossier", "content": text, "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "game_master_canon", "known_by": [row["id"]], "entity_refs": [row["id"]]})
            for index, secret in enumerate(row.get("secrets") or (), start=1):
                secret_id = f"secret:{_slug(row['id'])}:{index}"
                facts.append({"id": secret_id, "content": secret, "authority": "generated_proposal", "approved_authority": "objective_canon", "visibility": "npc_private", "known_by": [row["id"]], "entity_refs": [row["id"]], "secret_owner_id": row["id"]})
                rules.append({"id": f"acl:{secret_id}", "evidence_id": secret_id, "visibility": "npc_private", "known_by": [row["id"]]})
            documents.append(_document(node, row["name"], text, visibility="game_master_canon", entity_ids=(row["id"],)))
        return GeneratedTopic(node.topic_id, tuple(documents), tuple(entities), tuple(facts), knowledge_rules=tuple(rules), provenance={"generator": "deterministic_world_forge_v1"})

    def _story_threads(self, node: CampaignTopicNode, template: str) -> GeneratedTopic:
        threads = (
            ({"id": "thread:vanta_gate_return", "title": "The Vanta Gate Pulses Again", "summary": "Vexira prepares the gate for the Unmaker while rival powers race to intervene.", "actor_ids": ["npc:vexira_umbra"], "location_ids": ["location:vanta_gate"], "faction_ids": ["faction:silent_chorus"], "status": "opening"},
             {"id": "thread:wild_summon", "title": "A Wild Summon", "summary": "An unregistered hero appears where three factions can reach them first.", "actor_ids": [], "location_ids": ["location:academy_courtyard"], "faction_ids": ["faction:academy_of_heroes"], "status": "opening"})
            if "summoned" in template else
            ({"id": "thread:missing_caravan", "title": "The Missing Caravan", "summary": "A caravan vanished between the East Road and the old quarry.", "actor_ids": ["npc:bran", "npc:elara"], "location_ids": ["location:old_quarry"], "faction_ids": ["faction:merchant_compact"], "status": "opening"},
             {"id": "thread:pale_fissure", "title": "The Pale Fissure", "summary": "Light beneath the quarry suggests a sealed magical rule is failing.", "actor_ids": ["npc:captain_aldric"], "location_ids": ["location:old_quarry"], "faction_ids": ["faction:northern_watch"], "status": "opening"})
        )
        return GeneratedTopic(node.topic_id, story_threads=threads, provenance={"generator": "deterministic_world_forge_v1"})


def _summoned_npcs() -> list[dict[str, Any]]:
    return [
        {"id": "npc:vexira_umbra", "name": "Vexira Umbra", "appearance": "Unsettlingly beautiful, with dark lacquered-bone skin, crimson lines beneath it, candle-slit eyes, blood-smoke hair, and mirrorsteel armor.", "personality": "Formal, observant, patient, ritual-minded, and cruel with disciplined purpose.", "backstory": "A former Silent Chorus assassin restored by the Unmaker, she has guarded the Vanta Gate for ten years against another premature strike.", "goals": ["protect the Unmaker's return", "humiliate opportunistic summoned heroes"], "motives": ["absolute chosen loyalty", "revenge for the previous Unmaker's death"], "speech_style": "careful measures, quiet formality, blade-like metaphors", "faction_ids": ["faction:silent_chorus"], "location_id": "location:vanta_gate", "secrets": ["She remembers the exact hero who killed the previous Unmaker."], "known_facts": ["fact:hero_system:foundation"]},
        {"id": "npc:academy_proctor", "name": "Proctor Ilyan", "appearance": "A silver-robed examiner carrying a chain of ward keys.", "personality": "Precise, skeptical, and protective of untrained summons.", "backstory": "He survived a wild summoning that destroyed an academy annex.", "goals": ["register new heroes safely"], "motives": ["prevent another uncontrolled arrival"], "speech_style": "clinical questions softened by weary courtesy", "faction_ids": ["faction:academy_of_heroes"], "location_id": "location:academy_courtyard", "secrets": [], "known_facts": []},
    ]


def _classic_npcs() -> list[dict[str, Any]]:
    return [
        {"id": "npc:bran", "name": "Bran", "appearance": "A broad-shouldered innkeeper with flour on one sleeve and old burn scars across his knuckles.", "personality": "Practical, watchful, dryly kind, and slow to trust grand claims.", "backstory": "Bran inherited the Rusty Flagon after years on caravan roads and still receives reports from drivers he once protected.", "goals": ["keep the Rusty Flagon solvent", "protect travelers who respect the house"], "motives": ["community duty", "fear of losing the tavern"], "speech_style": "plain, economical, lightly sardonic", "faction_ids": ["faction:adventurers_guild"], "location_id": "location:rusty_flagon_tavern", "secrets": ["He knows which caravan failed to return from the old quarry road."], "known_facts": ["fact:current_conflicts:foundation"]},
        {"id": "npc:elara", "name": "Elara", "appearance": "A sharp-eyed merchant in a weatherproof blue coat lined with hidden pockets.", "personality": "Quick, ambitious, socially perceptive, and protective of profitable relationships.", "backstory": "Elara built her network by moving goods through roads larger houses considered too dangerous.", "goals": ["secure a reliable eastern route"], "motives": ["independence", "commercial leverage"], "speech_style": "fast bargains, precise prices, strategic compliments", "faction_ids": ["faction:merchant_compact"], "location_id": "location:market_district", "secrets": [], "known_facts": []},
        {"id": "npc:captain_aldric", "name": "Captain Aldric", "appearance": "A watch captain in repaired mail with a rain-dark cloak and an unreadable stare.", "personality": "Disciplined, suspicious, fair when evidence is clear, and intolerant of reckless violence.", "backstory": "Aldric rose after exposing a bribed patrol and now trusts records more than reputations.", "goals": ["stabilize the eastern roads"], "motives": ["public duty", "fear of institutional corruption"], "speech_style": "formal questions, exact consequences, no wasted reassurance", "faction_ids": ["faction:northern_watch"], "location_id": "location:market_district", "secrets": [], "known_facts": []},
    ]
