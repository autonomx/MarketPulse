"""Preview, template, and validation helpers for adventure setup."""
from __future__ import annotations

from typing import Any, cast

from app.apps.rpg.genesis.creator.defaults import (
    apply_adventure_defaults,
    infer_default_opening,
    list_setup_templates,
)
from app.apps.rpg.genesis.creator.schema import AdventureSetup
from app.apps.rpg.genesis.creator.validation import (
    validate_adventure_setup_payload,
    validate_adventure_setup_semantics,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ADVENTURE_PREVIEW_RESPONSE_VERSION = 1


# ---------------------------------------------------------------------------
# Preview response builder — stabilises the contract for the frontend
# ---------------------------------------------------------------------------


def _build_preview_contract(prepared: dict[str, Any]) -> dict[str, Any]:
    """Convert the internal the new-adventure preparation output to a
    deterministic preview response shape.

    Parameters
    ----------
    prepared:
        The dict returned by the new-adventure preparation.
        Expected keys: ``ok``, ``validation``, ``preview``, ``resolved_context``.

    Returns
    -------
    dict
        Frontend-friendly payload with ``response_version``, ``success``,
        ``ok``, ``validation``, ``preview``, and ``resolved_context``.
    """
    validation = _safe_dict(prepared.get("validation"))
    preview = _safe_dict(prepared.get("preview"))
    resolved_context = _safe_dict(prepared.get("resolved_context"))

    counts = _safe_dict(preview.get("counts"))
    warnings = _safe_list(preview.get("warnings"))

    return {
        "success": True,
        "response_version": ADVENTURE_PREVIEW_RESPONSE_VERSION,
        "ok": bool(prepared.get("ok")),
        "validation": {
            "issues": _safe_list(validation.get("issues")),
            "blocking": bool(validation.get("blocking")),
            "hints": _safe_list(validation.get("hints")),
        },
        "preview": {
            "title": preview.get("title") or "",
            "genre": preview.get("genre") or "",
            "setting": preview.get("setting") or "",
            "premise": preview.get("premise") or "",
            "counts": {
                "factions": counts.get("factions", 0),
                "locations": counts.get("locations", 0),
                "npcs": counts.get("npcs", 0),
            },
            "warnings": warnings,
            "player_role": preview.get("player_role") or "",
            "player_archetype": preview.get("player_archetype") or "",
            "player_background": preview.get("player_background") or "",
            "campaign_objective": preview.get("campaign_objective") or "",
            "opening_hook": preview.get("opening_hook") or "",
            "starter_conflict": preview.get("starter_conflict") or "",
            "core_world_laws": _safe_list(preview.get("core_world_laws")),
            "genre_rules": _safe_list(preview.get("genre_rules")),
            "desired_content_mix": _safe_dict(preview.get("desired_content_mix")),
            "starting_gear": _safe_list(preview.get("starting_gear")),
            "starting_resources": _safe_dict(preview.get("starting_resources")),
        },
        "resolved_context": {
            "location_id": resolved_context.get("location_id"),
            "location_name": resolved_context.get("location_name") or "",
            "npc_ids": _safe_list(resolved_context.get("npc_ids")),
            "npc_names": _safe_list(resolved_context.get("npc_names")),
        },
        "opening_preview": _safe_dict(prepared.get("opening_preview")),
        "adventure_preview": _safe_dict(prepared.get("adventure_preview")),
    }


# ---------------------------------------------------------------------------
# Phase D — Rich preview helpers (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* characters, adding '…' if trimmed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def summarize_understood_setup(setup: dict[str, Any]) -> dict[str, Any]:
    """Summarize what the system understood from the setup.

    Returns
    -------
    dict
        ``player_fantasy``, ``world_shape``, ``campaign_feel``, ``core_rules``.
    """
    role = setup.get("player_role", "") or ""
    archetype = setup.get("player_archetype", "") or ""
    background = setup.get("player_background", "") or ""
    objective = setup.get("campaign_objective", "") or ""
    genre = setup.get("genre", "") or ""
    setting = setup.get("setting", "") or ""
    mood = setup.get("mood", "") or ""
    premise = setup.get("premise", "") or ""
    content_mix = setup.get("desired_content_mix") or {}
    core_world_laws = list(setup.get("core_world_laws") or [])
    genre_rules = list(setup.get("genre_rules") or [])

    # Player fantasy
    parts = [p for p in [archetype, role] if p]
    fantasy = " — ".join(parts) if parts else "Adventurer"
    if background:
        fantasy += f" ({background})"
    if objective:
        fantasy += f" seeking to {objective.lower().rstrip('.')}"

    # World shape
    world_parts = [p for p in [genre, setting] if p]
    world_shape = ", ".join(world_parts) if world_parts else "Unnamed world"
    if premise:
        world_shape += f". {_truncate(premise, 120)}"

    # Campaign feel
    feel_parts: list[str] = []
    if mood:
        feel_parts.append(mood.capitalize())
    if content_mix:
        top = sorted(content_mix.items(), key=lambda kv: kv[1], reverse=True)[:2]
        feel_parts.extend(k.capitalize() for k, _ in top)
    campaign_feel = " / ".join(feel_parts) if feel_parts else "Balanced"

    rules = (core_world_laws + genre_rules)[:6] or ["Actions have consequences"]

    return {
        "player_fantasy": fantasy,
        "world_shape": world_shape,
        "campaign_feel": campaign_feel,
        "core_rules": rules,
    }


def infer_campaign_loop_preview(setup: dict[str, Any]) -> dict[str, Any]:
    """Infer a campaign loop preview from objective, content mix, genre, mood,
    conflict, and seeds.

    Returns
    -------
    dict
        ``primary_loop`` and ``secondary_loops``.
    """
    genre = (setup.get("genre") or "").lower()
    objective = setup.get("campaign_objective", "") or ""
    conflict = setup.get("starter_conflict", "") or ""
    content_mix = setup.get("desired_content_mix") or {}
    factions = list(setup.get("factions") or [])
    npc_seeds = list(setup.get("npc_seeds") or [])

    # Primary loop — use objective if available, else infer from genre
    if objective:
        primary = f"Pursue objective: {_truncate(objective, 120)}"
    elif "mystery" in genre or "noir" in genre:
        primary = "Investigate leads → uncover hidden truths → confront the mastermind"
    elif "horror" in genre:
        primary = "Survive threats → unravel the source → escape or destroy the evil"
    elif "sci-fi" in genre or "scifi" in genre or "science" in genre:
        primary = "Explore unknown sectors → gather intel → confront the anomaly"
    elif "political" in genre or "intrigue" in genre:
        primary = "Build alliances → outmanoeuvre rivals → seize or protect power"
    else:
        primary = "Explore the world → overcome challenges → achieve your goal"

    # Secondary loops
    secondary: list[str] = []
    if factions:
        faction_names = [
            (f.get("name") or f.get("faction_id", "a faction"))
            if isinstance(f, dict) else str(f)
            for f in factions[:3]
        ]
        secondary.append(f"Navigate faction politics ({', '.join(faction_names)})")
    if content_mix.get("combat", 0) >= 0.25:
        secondary.append("Survive combat encounters and grow stronger")
    if content_mix.get("mystery", 0) >= 0.2:
        secondary.append("Solve mysteries and gather clues")
    if content_mix.get("social", 0) >= 0.2:
        secondary.append("Build relationships and negotiate alliances")
    if content_mix.get("exploration", 0) >= 0.25:
        secondary.append("Discover new locations and hidden secrets")
    if npc_seeds and not secondary:
        secondary.append("Interact with key characters and shape relationships")
    if conflict and not secondary:
        secondary.append(f"Resolve: {_truncate(conflict, 100)}")
    if not secondary:
        secondary.append("Advance personal story arcs")

    return {
        "primary_loop": primary,
        "secondary_loops": secondary,
    }


def build_opening_narration_preview(
    setup: dict[str, Any], opening_context: dict[str, Any]
) -> str:
    """Build a 2-4 paragraph opening narration preview (max ~800 chars).

    Deterministic template — no LLM required.
    """
    location = opening_context.get("location_name") or "an unknown place"
    scene_frame = opening_context.get("scene_frame") or ""
    problem = opening_context.get("immediate_problem") or ""
    involvement = opening_context.get("player_involvement_reason") or ""
    tension = opening_context.get("tension_level") or "medium"
    time_of_day = opening_context.get("time_of_day") or "evening"
    weather = opening_context.get("weather") or "clear"

    role = setup.get("player_role") or "adventurer"
    hook = setup.get("opening_hook") or ""

    # Paragraph 1 — scene setting
    weather_phrase = f"under {weather} skies" if weather != "clear" else ""
    p1_parts = [f"The {time_of_day} settles over {location}"]
    if weather_phrase:
        p1_parts[0] += f" {weather_phrase}"
    p1_parts[0] += "."
    if scene_frame:
        p1_parts.append(_truncate(scene_frame, 160) + ".")

    # Paragraph 2 — hook and tension
    p2_parts: list[str] = []
    if hook:
        p2_parts.append(_truncate(hook, 180) + ".")
    elif problem:
        p2_parts.append(_truncate(problem, 180) + ".")
    if tension == "high":
        p2_parts.append("Danger hangs thick in the air.")
    elif tension == "low":
        p2_parts.append("A moment of calm — but it won't last.")

    # Paragraph 3 — player involvement
    p3_parts: list[str] = []
    if involvement:
        p3_parts.append(f"As a {role}, {_truncate(involvement, 160)}.")
    else:
        p3_parts.append(f"As a {role}, you find yourself drawn into the unfolding events.")

    paragraphs = [
        " ".join(p1_parts),
    ]
    if p2_parts:
        paragraphs.append(" ".join(p2_parts))
    paragraphs.append(" ".join(p3_parts))

    narration = "\n\n".join(paragraphs)
    return _truncate(narration, 800)


def build_adventure_preview(setup: dict[str, Any]) -> dict[str, Any]:
    """Build a rich preview of the adventure setup.

    All content is deterministic — no LLM calls.

    Returns
    -------
    dict
        Full preview contract with ``opening_preview``, ``launch_cast``,
        ``starter_conflict``, ``campaign_loop_preview``,
        ``likely_first_actions``, ``understood_setup``.
    """
    opening_ctx = build_opening_context(setup)

    # --- opening_preview ---
    narration = build_opening_narration_preview(setup, opening_ctx)
    opening_preview = {
        "title": setup.get("title") or "",
        "location_name": opening_ctx.get("location_name", ""),
        "scene_frame": opening_ctx.get("scene_frame", ""),
        "narration_preview": narration,
        "immediate_problem": opening_ctx.get("immediate_problem", ""),
        "player_involvement_reason": opening_ctx.get("player_involvement_reason", ""),
        "tension_level": opening_ctx.get("tension_level", "medium"),
        "time_of_day": opening_ctx.get("time_of_day", "evening"),
        "weather": opening_ctx.get("weather", "clear"),
    }

    # --- launch_cast ---
    npc_seeds = list(setup.get("npc_seeds") or [])
    present_ids = set(opening_ctx.get("present_npc_ids") or [])
    launch_cast: list[dict[str, str]] = []
    for npc in npc_seeds:
        if not isinstance(npc, dict):
            continue
        npc_id = npc.get("npc_id", "")
        if npc_id in present_ids or not present_ids:
            launch_cast.append({
                "npc_id": npc_id,
                "name": cast(str, npc.get("name", npc_id)),
                "role": npc.get("role", "unknown"),
                "reason_present": npc.get("reason_present", "Part of the opening scene"),
            })
        if len(launch_cast) >= 5:
            break

    # --- starter_conflict ---
    conflict_text = setup.get("starter_conflict") or ""
    factions = list(setup.get("factions") or [])
    parties = [
        (f.get("name") or f.get("faction_id", "Unknown"))
        if isinstance(f, dict) else str(f)
        for f in factions[:4]
    ]
    hook = setup.get("opening_hook") or ""
    starter_conflict = {
        "summary": conflict_text or hook or "An emerging situation demands attention",
        "parties": parties or ["Unknown forces"],
        "stakes": setup.get("campaign_objective") or "The fate of the region hangs in the balance",
    }

    # --- campaign_loop_preview ---
    loop_preview = infer_campaign_loop_preview(setup)

    # --- likely_first_actions ---
    first_choices = list((setup.get("opening") or {}).get("first_choices") or [])
    if not first_choices:
        first_choices = list(opening_ctx.get("first_choices") or [])
    likely_first_actions = first_choices[:5] if first_choices else [
        "Investigate the immediate surroundings",
        "Talk to nearby characters",
        "Assess the situation before acting",
    ]

    # --- understood_setup ---
    understood = summarize_understood_setup(setup)

    return {
        "opening_preview": opening_preview,
        "launch_cast": launch_cast,
        "starter_conflict": starter_conflict,
        "campaign_loop_preview": loop_preview,
        "likely_first_actions": likely_first_actions,
        "understood_setup": understood,
    }


# ---------------------------------------------------------------------------
# Phase A/B — Opening context builder
# ---------------------------------------------------------------------------


def build_opening_context(setup: dict[str, Any]) -> dict[str, Any]:
    """Build a rich opening context dict from an adventure setup payload.

    Combines explicit ``opening`` data with inferred defaults so every
    opening field is populated.

    Returns
    -------
    dict
        Keys: ``location_id``, ``location_name``, ``present_npc_ids``,
        ``scene_frame``, ``immediate_problem``, ``player_involvement_reason``,
        ``first_choices``, ``tension_level``, ``time_of_day``, ``weather``.
    """
    setup = _safe_dict(setup)
    opening = _safe_dict(setup.get("opening"))
    inferred = infer_default_opening(setup)

    starting_npc_ids = _safe_list(setup.get("starting_npc_ids"))
    opening_present = _safe_list(opening.get("present_npc_ids"))
    inferred_present = _safe_list(inferred.get("present_npc_ids"))

    merged_present = []
    seen = set()
    for npc_id in opening_present + starting_npc_ids + inferred_present:
        npc_id = str(npc_id).strip()
        if npc_id and npc_id not in seen:
            seen.add(npc_id)
            merged_present.append(npc_id)

    return {
        "location_id": opening.get("location_id") or inferred.get("location_id", ""),
        "location_name": opening.get("location_name") or inferred.get("location_name", ""),
        "present_npc_ids": merged_present,
        "scene_frame": opening.get("scene_frame") or inferred.get("scene_frame", ""),
        "immediate_problem": opening.get("immediate_problem") or inferred.get("immediate_problem", ""),
        "player_involvement_reason": opening.get("player_involvement_reason") or inferred.get("player_involvement_reason", ""),
        "first_choices": opening.get("first_choices") or inferred.get("first_choices", []),
        "tension_level": opening.get("tension_level") or inferred.get("tension_level", "medium"),
        "time_of_day": opening.get("time_of_day") or inferred.get("time_of_day", "evening"),
        "weather": opening.get("weather") or inferred.get("weather", "clear"),
    }


# ---------------------------------------------------------------------------
# Template listing / building
# ---------------------------------------------------------------------------


def get_templates() -> list[dict[str, Any]]:
    """Return available adventure setup templates with metadata."""
    return list_setup_templates()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an adventure setup payload.

    Returns the validation result dict with ``issues`` and ``blocking``.
    """
    result = validate_adventure_setup_payload(payload)
    semantics = validate_adventure_setup_semantics(payload)
    return {
        "success": True,
        "validation": result.to_dict(),
        "warnings": semantics.get("warnings", []),
        "notices": semantics.get("notices", []),
        "semantic_scores": semantics.get("scores", {}),
    }


# ---------------------------------------------------------------------------
# Preview (normalize + defaults + validation + summary)
# ---------------------------------------------------------------------------


def preview_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Prepare a rich preview of the adventure setup.

    Normalizes → applies defaults → validates → builds a presenter summary,
    then resolves the starting context so the frontend can show "Opening in:
    <location>, Present: <actors>".
    """
    from app.apps.rpg.genesis.creator.presenters import CreatorStatePresenter

    data = apply_adventure_defaults(dict(payload))
    validation = validate_adventure_setup_payload(data)
    semantics = validate_adventure_setup_semantics(data)

    if validation.is_blocking():
        return _build_preview_contract({
            "ok": False,
            "validation": validation.to_dict(),
            "preview": {},
            "resolved_context": {},
        })

    setup = AdventureSetup.from_dict(data).normalize().with_defaults()
    presenter = CreatorStatePresenter()
    presenter.present_setup_summary(setup)

    # Resolve starting context locally (mirrors StartupGenerationPipeline)
    location_id = setup.starting_location_id
    if not location_id and setup.locations:
        location_id = setup.locations[0].location_id

    npc_ids = list(setup.starting_npc_ids)
    if not npc_ids and setup.npc_seeds:
        npc_ids = [npc.npc_id for npc in setup.npc_seeds[:3]]

    # Human-readable names for resolved context
    location_name = location_id or ""
    for loc in setup.locations:
        if loc.location_id == location_id:
            location_name = loc.name
            break

    npc_names: list[str] = []
    npc_lookup = {npc.npc_id: npc.name for npc in setup.npc_seeds}
    for npc_id in npc_ids:
        npc_names.append(npc_lookup.get(npc_id, npc_id))

    resolved_context = {
        "location_id": location_id,
        "location_name": location_name,
        "npc_ids": npc_ids,
        "npc_names": npc_names,
    }

    counts = {
        "factions": len(setup.factions),
        "locations": len(setup.locations),
        "npcs": len(setup.npc_seeds),
    }

    # Phase A — build opening context
    setup_dict = setup.to_dict()
    opening_ctx = build_opening_context(setup_dict)

    # Phase D — build rich adventure preview
    adventure_preview = build_adventure_preview(setup_dict)

    prepared = {
        "ok": True,
        "validation": validation.to_dict(),
        "preview": {
            "title": setup.title,
            "genre": setup.genre,
            "setting": setup.setting,
            "premise": setup.premise,
            "counts": counts,
            "warnings": semantics.get("warnings", []),
            "player_role": setup.player_role,
            "player_archetype": setup.player_archetype,
            "player_background": setup.player_background,
            "campaign_objective": setup.campaign_objective,
            "opening_hook": setup.opening_hook,
            "starter_conflict": setup.starter_conflict,
            "core_world_laws": list(setup.core_world_laws),
            "genre_rules": list(setup.genre_rules),
            "desired_content_mix": dict(setup.desired_content_mix),
            "starting_gear": list(setup.starting_gear),
            "starting_resources": dict(setup.starting_resources),
        },
        "resolved_context": resolved_context,
        "opening_preview": opening_ctx,
        "adventure_preview": adventure_preview,
        "semantic_scores": semantics.get("scores", {}),
        "notices": semantics.get("notices", []),
    }

    return _build_preview_contract(prepared)


# ---------------------------------------------------------------------------
