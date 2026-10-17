"""Handcrafted evidence fixtures used to prove the engine before World Forge."""
from __future__ import annotations

from .authority import AuthorityClass, VisibilityClass
from .contracts import EvidenceRecord


def bran_fixture_evidence() -> tuple[EvidenceRecord, ...]:
    return (
        EvidenceRecord(
            evidence_id="npc:bran:identity",
            content="Bran keeps the Rusty Flagon Tavern and listens carefully to news carried by travelers.",
            authority=AuthorityClass.OBJECTIVE_CANON,
            visibility=VisibilityClass.PUBLIC,
            entity_refs=("npc:bran", "location:rusty_flagon"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:bran:personality",
            content="Bran is practical, dryly humorous, observant, and reluctant to dramatize his worries.",
            authority=AuthorityClass.OBJECTIVE_CANON,
            visibility=VisibilityClass.NARRATOR_ONLY,
            known_by=("npc:bran",),
            entity_refs=("npc:bran",),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:bran:habit:cup",
            content="Bran often polishes a cup while thinking and pauses with the cloth wrapped around it before answering difficult questions.",
            authority=AuthorityClass.OBJECTIVE_CANON,
            visibility=VisibilityClass.NARRATOR_ONLY,
            known_by=("npc:bran",),
            entity_refs=("npc:bran",),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:bran:road_knowledge",
            content="Bran knows the old road is muddy after heavy rain and has heard that caravans remain able to pass with care.",
            authority=AuthorityClass.PUBLIC_KNOWLEDGE,
            visibility=VisibilityClass.NPC_PRIVATE,
            known_by=("npc:bran",),
            entity_refs=("npc:bran", "location:old_road"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:bran:private_debt",
            content="Bran privately owes a substantial debt to a merchant syndicate and hides the amount from guests.",
            authority=AuthorityClass.SECRET_CANON,
            visibility=VisibilityClass.NPC_PRIVATE,
            known_by=("npc:bran",),
            entity_refs=("npc:bran", "faction:merchant_syndicate"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="location:rusty_flagon:atmosphere",
            content="The Rusty Flagon smells of woodsmoke, wet wool, and stew; rain ticks against its shutters while the hearth burns low and steady.",
            authority=AuthorityClass.SCENE_OBSERVATION,
            visibility=VisibilityClass.PUBLIC,
            entity_refs=("location:rusty_flagon",),
            source_revision=1,
        ),
    )


def vexira_fixture_evidence() -> tuple[EvidenceRecord, ...]:
    return (
        EvidenceRecord(
            evidence_id="npc:vexira:appearance",
            content="Vexira is unsettlingly beautiful, with dark lacquered skin, candle-slit eyes, blood-smoke hair, mirrorsteel armor, and twin daggers.",
            authority=AuthorityClass.OBJECTIVE_CANON,
            visibility=VisibilityClass.NARRATOR_ONLY,
            known_by=("npc:vexira",),
            entity_refs=("npc:vexira",),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:vexira:personality",
            content="Vexira speaks formally and softly, measures every word, mocks failed ceremony, and expresses devotion with patient, deliberate menace.",
            authority=AuthorityClass.OBJECTIVE_CANON,
            visibility=VisibilityClass.NARRATOR_ONLY,
            known_by=("npc:vexira",),
            entity_refs=("npc:vexira",),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:vexira:history:gate_vigil",
            content="Vexira has guarded the Vanta Gate for ten years since the previous Unmaker was slain by a summoned hero.",
            authority=AuthorityClass.SECRET_CANON,
            visibility=VisibilityClass.NPC_PRIVATE,
            known_by=("npc:vexira",),
            entity_refs=("npc:vexira", "location:vanta_gate"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:vexira:belief:returned_unmaker",
            content="Vexira believes with absolute conviction that the newly summoned player may be the returned Unmaker.",
            authority=AuthorityClass.NPC_BELIEF,
            visibility=VisibilityClass.NPC_PRIVATE,
            known_by=("npc:vexira",),
            entity_refs=("npc:vexira", "lore:unmaker"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:vexira:goal:restore_unmaker",
            content="Vexira wants the Unmaker restored and will pressure the player to accept that role while preventing an immediate heroic ambush.",
            authority=AuthorityClass.NPC_BELIEF,
            visibility=VisibilityClass.NPC_PRIVATE,
            known_by=("npc:vexira",),
            entity_refs=("npc:vexira", "lore:unmaker"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="location:vanta_gate:mirrorstone",
            content="The Vanta Gate chamber is ringed by obsidian monoliths above a mirrorstone floor cut with violet summoning glyphs.",
            authority=AuthorityClass.SCENE_OBSERVATION,
            visibility=VisibilityClass.PUBLIC,
            entity_refs=("location:vanta_gate",),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="lore:summoning:ritual",
            content="Kavrix performs a sanctioned great summoning every ten years, but unstable wild summons also pull people through without ceremony.",
            authority=AuthorityClass.PUBLIC_KNOWLEDGE,
            visibility=VisibilityClass.PUBLIC,
            entity_refs=("lore:summoning", "realm:kavrix"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="lore:academy:aertos_solara",
            content="The Academy of Heroes recruits across Aertos and Solara while trying to reconcile Aertos tradition with Solara innovation.",
            authority=AuthorityClass.PUBLIC_KNOWLEDGE,
            visibility=VisibilityClass.PUBLIC,
            entity_refs=("institution:academy", "realm:aertos", "realm:solara"),
            source_revision=1,
        ),
        EvidenceRecord(
            evidence_id="npc:vexira:gm_secret",
            content="The Vanta Gate is responding to an unknown third force rather than confirming that the player is the Unmaker.",
            authority=AuthorityClass.SECRET_CANON,
            visibility=VisibilityClass.GAME_MASTER_ONLY,
            known_by=(),
            entity_refs=("location:vanta_gate", "lore:unmaker"),
            source_revision=1,
        ),
    )


def narrative_fixture_evidence() -> tuple[EvidenceRecord, ...]:
    return (*bran_fixture_evidence(), *vexira_fixture_evidence())
