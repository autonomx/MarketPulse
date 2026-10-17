"""Independent image-target planning, generation, and review for authored worlds."""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from app.jobs import default_job_store
from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .generation_jobs import canonical_hash
from .library_service import read_world_detail
from .lifecycle_service import require_world_writable
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.worlds_world_images.threat_portrait_prompt', "1", 'Cinematic {v0} RPG threat key art of {v1}. Show one complete, immediately readable threat design in a three-quarter view, with the head or primary sensor cluster and full silhouette clearly separated from the background. Define anatomy, chassis, armour, weapons, movement, damage, and scale directly from this canon: {v2} Depict one controlled threatening action and one visible weakness or functional limitation. Use a simple environment with a single scale cue and minimal out-of-focus background activity. Dramatic rim light, realistic materials, restrained volumetric atmosphere, readable detail. No generic soldier or monster design, no duplicate creatures, no crowded background, no excessive armour unrelated to canon, no extra limbs, malformed anatomy, fused equipment, text, logos, watermarks, or UI.')
_PROMPT_2 = prompt_template('rpg.worlds_world_images.character_portrait_prompt', "1", 'Cinematic {v0} RPG character key art of {v1}, a {v2}. Tight chest-up portrait; head and both shoulders fully inside the frame; face occupying roughly one-third of the image; eye-level camera; three-quarter view; shallow depth of field. Establish one consistent identity from this canonical appearance: {v3} Use an alert, controlled pose that visibly expresses the {v4} tone. Prioritize the face, clothing silhouette, augmentations or signature equipment, and one unique identifying feature. Use a simple environment with only one clear story element and minimal out-of-focus background figures. Realistic materials and skin, dramatic rim light, restrained blue-orange contrast, volumetric atmosphere, intricate but readable detail. No generic model appearance, symmetrical implants unless canonical, excessive armour, bulky exoskeleton, superhero pose, glowing lines covering the face, crowded background, extra limbs or fingers, distorted ears, fused clothing, text, logos, watermarks, UI, or modern photography artifacts.')
_PROMPT_3 = prompt_template('rpg.worlds_world_images.prompt', "1", '{v0}. {v1} World: {v2}. Genre: {v3}. Tone: {v4}. Premium cinematic, poster-quality illustration, dramatic composition, theatrical lighting, volumetric atmosphere, rich colour grading, intricate but readable detail, cohesive art direction. Preserve canonical features and avoid rendered text, logos, watermarks, UI, or modern photography artifacts.')
_PROMPT_4 = prompt_template('rpg.worlds_world_images.character_portrait_prompt_2', "1", 'Cinematic cyberpunk RPG character key art of {v0}, shown in a tight chest-up portrait. His head and both shoulders are fully visible, with his face occupying roughly one-third of the composition. He is a lean, battle-worn covert operative in his early thirties with a narrow angular face, pale olive skin, short dark hair shaved at the sides, grey-green eyes, light stubble, and a thin diagonal scar crossing his right eyebrow. A distinctive matte-titanium augmentation runs from his left temple, around the ear, and down the jaw, built from fitted surgical plates, flexible black synthetic joints, visible mounting seams, and a compact optical-camouflage emitter behind the ear. The augmentation is functional and restrained, not decorative. His left iris contains a subtle mechanical aperture. He wears a weathered asymmetric stealth coat made from matte black technical fabric, with one reinforced ceramic shoulder panel and concealed magnetic fasteners. No bulky armour, excessive pouches, or superhero styling. He turns sharply toward the viewer while touching the camouflage control behind his ear. One edge of his shoulder is partially obscured by physically believable optical distortion, bending rain and background light around his silhouette. His expression is controlled, suspicious, and defiant. Behind him is a simplified cyberpunk maintenance district with one elevated catwalk, large filtration pipes, rain, drifting steam, and a distant corporate searchlight sweeping through the haze. A dismantled surveillance drone on a workbench subtly suggests rebel activity. Keep background figures minimal and out of focus. Eye-level camera, three-quarter facial angle, shallow depth of field, strong cool blue rim light on one side and restrained warm industrial light on the other, realistic skin texture, believable metal and fabric materials, volumetric rain and steam, rich cinematic colour grading, premium poster-quality realistic illustration, intricate but readable detail. Preserve the canonical subtle neural interfaces and faint eye patterns. No text, logos, watermarks, UI, generic fashion-model appearance, symmetrical implants, glowing facial lines, excessive cybernetics, crowded background, distorted anatomy, extra fingers, malformed ears, fused clothing, or modern photography artifacts.')
_PROMPT_5 = prompt_template('rpg.worlds_world_images.prompt_2', "1", "cinematic RPG key art with a striking, poster-quality composition")
_PROMPT_6 = prompt_template('rpg.worlds_world_images.prompt_3', "1", "vertical cinematic key art for a premium RPG poster, with an iconic central composition")
_PROMPT_7 = prompt_template('rpg.worlds_world_images.prompt_4', "1", "widescreen cinematic key art for a premium RPG poster, with a bold focal point and negative space for title treatment")
_PROMPT_8 = prompt_template('rpg.worlds_world_images.prompt_5', "1", "cinematic character key art, shoulders and face visible, expressive pose and dramatic rim lighting")
_PROMPT_9 = prompt_template('rpg.worlds_world_images.prompt_6', "1", "premium collectible RPG inventory icon, dramatically lit with a clean readable silhouette")
_PROMPT_10 = prompt_template('rpg.worlds_world_images.prompt_7', "1", "premium heraldic faction emblem, dramatically lit with a distinct readable silhouette")
_PROMPT_11 = prompt_template('rpg.worlds_world_images.prompt_8', "1", "cinematic establishing shot, sweeping environmental key art with a strong foreground, midground, and background")
_PROMPT_12 = prompt_template('rpg.worlds_world_images.prompt_9', "1", "cinematic environmental key art with a strong focal point, story details, and a sense of scale")
_PROMPT_13 = prompt_template('rpg.worlds_world_images.prompt_10', "1", "cinematic editorial RPG key art with a striking, poster-quality composition")

_ROLE_BY_TOPIC: Mapping[str, str] = {
    "realm": "landscape",
    "regions": "landscape",
    "locations": "scene",
    "points_of_interest": "scene",
    "npcs": "portrait",
    "races": "illustration",
    "classes": "illustration",
    "factions": "emblem",
    "monsters": "portrait",
    "items": "icon",
    "spells": "illustration",
    "feats": "illustration",
    "quests": "cover",
    "encounter_seeds": "scene",
    "one_shots": "cover",
    "opening_scenarios": "cover",
}

_IMAGE_PROMPT_VERSION = "world-cinematic-poster-v8"
_MAP_NO_TEXT_CONSTRAINT = (
    "Absolutely no typography or written marks anywhere in the artwork: no place names, labels, "
    "letters, words, numbers, runes, legends, cartouches, compass labels, signage, watermarks, "
    "or UI. Render pure unlabeled pictorial geography; names are supplied only by application overlay markers."
)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value is not None and str(value).strip() else fallback


def _dossier_section_text(entity: Mapping[str, Any], section_id: str) -> str:
    sections = _record(entity.get("dossier")).get("sections") or ()
    for section_value in sections:
        section = _record(section_value)
        if _text(section.get("id")).casefold() != section_id.casefold():
            continue
        paragraphs = [
            _text(paragraph)
            for paragraph in section.get("paragraphs") or ()
            if _text(paragraph)
        ]
        return " ".join(paragraphs)
    return ""


def _canon_value_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "; ".join(
            f"{str(key).replace('_', ' ')}: {_canon_value_text(item)}"
            for key, item in value.items()
            if _canon_value_text(item)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "; ".join(filter(None, (_canon_value_text(item) for item in value)))
    return _text(value)


def _entity_canon_details(
    entity: Mapping[str, Any],
    *,
    target_type: str,
    role: str,
) -> str:
    fields_by_role = {
        "portrait": ("short_summary", "registry_distinction", "appearance", "behaviour", "capabilities", "weaknesses"),
        "icon": ("short_summary", "function", "capability", "limitations", "availability", "cost", "failure_mode"),
        "emblem": ("short_summary", "registry_distinction", "observable_signs", "resources", "current_objective", "internal_divisions"),
        "scene": ("short_summary", "setup", "complications", "outcomes", "observable_evidence", "current_pressure", "current_state"),
        "landscape": ("short_summary", "identity", "boundaries", "landmarks", "dangers", "current_pressure"),
        "cover": ("short_summary", "premise", "objectives", "stakes", "complications", "beats", "rewards"),
        "illustration": (
            "short_summary", "registry_distinction", "capability", "dependency", "failure_mode",
            "values", "customs", "internal_tensions", "observable_effects", "observable_consequences",
            "capabilities", "equipment_ids", "cause", "consequences", "present_day_legacies",
        ),
    }
    sections_by_role = {
        "portrait": ("appearance", "overview", "details"),
        "icon": ("overview", "details"),
        "emblem": ("overview", "details", "connections"),
        "scene": ("situation", "atmosphere", "setting", "overview", "complications"),
        "landscape": ("geography", "identity", "landmarks", "dangers", "overview"),
        "cover": ("overview", "opening", "objectives", "stakes", "complications"),
        "illustration": ("overview", "details", "history", "connections"),
    }
    fragments: list[str] = []
    for field in ("description", "appearance", "sensory_profile", "summary"):
        value = _canon_value_text(entity.get(field))
        if value:
            fragments.append(f"{field.replace('_', ' ')}: {value}")
    for field in fields_by_role.get(role, ("short_summary", "registry_distinction")):
        value = _canon_value_text(entity.get(field))
        if value:
            fragments.append(f"{field.replace('_', ' ')}: {value}")
    for section_id in sections_by_role.get(role, ("overview", "details")):
        value = _dossier_section_text(entity, section_id)
        if value:
            fragments.append(f"{section_id.replace('_', ' ')}: {value}")
    if not fragments:
        fragments.append(f"structured {target_type.replace('_', ' ')} canon")
    return " ".join(fragments)[:1400]


def _threat_portrait_prompt(*, subject: str, genre: str, details: str) -> str:
    return (
        _PROMPT_1.format(v0=(genre), v1=(subject), v2=(details))
    )


def _character_portrait_prompt(
    *,
    subject: str,
    entity: Mapping[str, Any],
    genre: str,
    tone: str,
    details: str,
) -> str:
    subject_key = subject.casefold().replace("’", "'")
    if "kaelen" in subject_key and "voss" in subject_key:
        return (
            _PROMPT_4.format(v0=(subject))
        )

    appearance = _dossier_section_text(entity, "appearance") or details
    role = _text(entity.get("registry_role") or entity.get("kind"), "character")
    return (
        _PROMPT_2.format(v0=(genre), v1=(subject), v2=(role), v3=(appearance), v4=(tone))
    )


def _visual_subject_brief(*, subject: str, details: str, genre: str, role: str) -> str:
    """Turn a canon label into concrete, image-model-friendly art direction."""
    subject_key = subject.casefold()
    genre_key = genre.casefold()
    if "atmospheric filtration" in subject_key or "filtration system" in subject_key:
        return (
            f"Depict {subject} as a colossal life-support machine rising above a polluted "
            "cyberpunk city: towering purification stacks, turbine-sized intake vents, pressure "
            "chambers, industrial ducts, and dense illuminated pipes. Show enormous rotating fans "
            "drawing toxic orange smog inward and blue-white clean vapour venting into the upper "
            "atmosphere, creating shafts of light through the haze. Its dark weathered metal is "
            "heavily reinforced and covered with maintenance platforms, cables, valves, modular "
            "filters, and hacked additions from underground technicians. Add neon indicators, "
            "electrical sparks, steam bursts, wet reflective surfaces, and small augmented rebels "
            "in practical techwear on surrounding catwalks to establish immense scale and corporate "
            "control."
        )

    setting = "cyberpunk" if "cyberpunk" in genre_key else genre
    direction = {
        "icon": (
            "Show the complete object alone in a three-quarter product view with a clean silhouette, believable "
            "materials, visible operating parts, wear, and one small functional effect. Use one neutral supporting "
            "surface and no background characters."
        ),
        "emblem": (
            "Design one bold physical insignia using motifs, materials, damage, hierarchy, and a limited colour "
            "palette derived from the faction canon. Centered, symmetrical overall silhouette, no letters or words."
        ),
        "scene": (
            "Stage one decisive action at its most readable moment. Use a clear foreground subject, one environmental "
            "focal landmark, and a restrained background. Make the conflict, participants, consequence, and location visible."
        ),
        "landscape": (
            "Create one establishing view with a strong foreground anchor, readable geography, signature landmarks, "
            "inhabitants at scale, and visible environmental pressure."
        ),
        "cover": (
            "Build an iconic poster composition around one protagonist or objective, one opposing force, and one "
            "location cue. Make the stakes visually legible without depicting every event."
        ),
        "illustration": (
            "Show one concrete subject or representative figure performing the defining function. Make materials, "
            "mechanism, cultural markers, cost, and consequence visible; keep secondary elements subordinate."
        ),
        "map": "Make the named subject a readable place at map scale, with distinct terrain and landmarks.",
    }.get(role, "Use one unambiguous focal subject with a clear foreground, midground, and background.")
    return f"Depict {subject} as a physically credible {setting} image. {direction} Canon source material: {details}."


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _target_row(row: Any) -> dict[str, Any]:
    return {
        "world_id": str(row[0]),
        "target_id": str(row[1]),
        "target_type": str(row[2]),
        "entity_id": str(row[3]),
        "role": str(row[4]),
        "source_content_hash": str(row[5]),
        "status": str(row[6]),
        "review_state": str(row[7]),
        "suggested_prompt": str(row[8]),
        "active_asset_id": str(row[9]) if row[9] is not None else None,
        "latest_job_id": str(row[10]) if row[10] is not None else None,
        "metadata": dict(row[11]),
        "created_at": row[12].isoformat(),
        "updated_at": row[13].isoformat(),
    }


def _attempt_row(row: Any) -> dict[str, Any]:
    return {
        "job_id": str(row[0]),
        "prompt": str(row[1]),
        "source_content_hash": str(row[2]),
        "status": str(row[3]),
        "asset_id": str(row[4]) if row[4] is not None else None,
        "error": dict(row[5]),
        "created_at": row[6].isoformat(),
        "updated_at": row[7].isoformat(),
    }


def _entity_rows(content: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    values = content.get("entities")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for value in values:
            if isinstance(value, Mapping):
                yield dict(value)


def _clip(value: Any, maximum: int) -> str:
    text = _text(value)
    return text[:maximum].rstrip() if text else ""


def _is_cyberpunk(genre: Any) -> bool:
    """Keep map art aligned with the world family, not a fantasy-atlas default."""
    normalized = _text(genre).casefold().replace("_", " ").replace("-", " ")
    return "cyberpunk" in normalized or "corporate dystopia" in normalized


def _map_format_hint(genre: Any) -> str:
    if _is_cyberpunk(genre):
        return (
            "premium illustrated cyberpunk city atlas, viewed from a high oblique top-down angle; "
            "a dense vertical megacity with layered expressways, elevated rail, corporate arcologies, "
            "stacked districts, industrial pipes, flood barriers, utility infrastructure, and pools of neon "
            "light reflected on rain-dark surfaces. Make class divisions and surveillance infrastructure visible "
            "in the urban layout; avoid medieval, pastoral, village, parchment, and fantasy-town architecture; "
            + _MAP_NO_TEXT_CONSTRAINT
        )
    return (
        "premium illustrated top-down RPG atlas with cinematic colour grading, legible terrain, "
        "landmarks, and travel routes; " + _MAP_NO_TEXT_CONSTRAINT
    )


def _map_blueprint_direction(blueprints: Sequence[Mapping[str, Any]]) -> str:
    """Extract human-authored spatial direction without dumping blueprint internals into a prompt."""
    useful_keys = (
        "description", "summary", "layout", "districts", "zones", "landmarks",
        "features", "terrain", "routes", "connections", "constraints", "environment",
    )
    fragments: list[str] = []
    for blueprint in blueprints:
        document = _record(blueprint.get("document"))
        values = [
            f"{key.replace('_', ' ')}: {_canon_value_text(document[key])}"
            for key in useful_keys
            if _canon_value_text(document.get(key))
        ]
        if values:
            fragments.append("; ".join(values))
    return _clip(" ".join(fragments), 1400)


def _map_canon_direction(
    *,
    world: Mapping[str, Any],
    blueprints: Sequence[Mapping[str, Any]],
) -> str:
    """Give map art the setting's own visual premise before applying genre fallback guidance."""
    world_description = _clip(world.get("description"), 1000)
    blueprint_direction = _map_blueprint_direction(blueprints)
    fragments = [
        "Use this authored world canon as the primary visual direction; do not substitute a generic genre setting.",
    ]
    if world_description:
        fragments.append(f"World premise: {world_description}")
    if blueprint_direction:
        fragments.append(f"Authored map layout and landmark constraints: {blueprint_direction}")
    return " ".join(fragments)


def _location_details(entity: Mapping[str, Any]) -> str:
    dossier = _record(entity.get("dossier"))
    metadata = _record(entity.get("metadata"))
    metadata_dossier = _record(metadata.get("dossier"))
    for value in (
        entity.get("description"),
        entity.get("short_summary"),
        entity.get("summary"),
        entity.get("sensory_profile"),
        dossier.get("description"),
        dossier.get("sensory_profile"),
        metadata.get("description"),
        metadata.get("summary"),
        metadata_dossier.get("description"),
        _dossier_section_text(entity, "overview"),
        _dossier_section_text(entity, "details"),
        _dossier_section_text(entity, "setting"),
    ):
        if _text(value):
            return _clip(value, 520)
    return ""


def _location_landmark(entity: Mapping[str, Any], *, genre: Any = "") -> dict[str, str]:
    """Turn a semantic location into a visual instruction for regional map art."""
    name = _text(entity.get("name") or entity.get("title"), "Unnamed location")
    explicit_type = _text(
        entity.get("location_type")
        or entity.get("subtype")
        or entity.get("category")
        or entity.get("type")
    )
    details = _location_details(entity)
    cues = " ".join((name, explicit_type, details)).lower()
    is_vertical_structure = any(
        keyword in cues
        for keyword in (
            "tower", "stack", "spire", "arcology", "high-rise", "highrise", "skyscraper",
            "building", "complex", "clinic", "laboratory", "lab", "dormitory", "residence",
        )
    )
    landmark_by_cue = (
        (("town", "city", "village", "settlement", "market", "port"), "a compact settlement with streets, roofs, and a clear civic centre"),
        (("castle", "fortress", "keep", "citadel", "gate", "watchtower"), "a fortified stronghold with walls and towers"),
        (("mountain", "peak", "pass", "ridge", "highland"), "a dramatic mountain landmark with a visible pass or ridge"),
        (("forest", "wood", "grove", "jungle"), "a dense forest landmark with a distinctive canopy"),
        (("river", "lake", "waterfall", "coast", "harbor", "harbour"), "a prominent waterway or shoreline landmark"),
        (("ruin", "temple", "shrine", "monastery"), "ancient ruins or a sacred complex"),
        (("mine", "quarry", "cavern", "cave"), "a mine or cave entrance set into the terrain"),
        (("swamp", "marsh", "bog"), "a winding wetland with dark pools and reed beds"),
        (("desert", "dune", "wasteland"), "a desert landmark with dunes or weathered stone"),
        (("bridge", "crossroads", "road", "trail"), "a travel landmark with a visible bridge, crossroads, or route"),
    )
    if _is_cyberpunk(genre):
        cyberpunk_landmarks = (
            (
                ("spire", "arcology", "tower", "corporate", "executive"),
                "a towering corporate arcology or needle-like megastructure with stacked skybridges and controlled access",
            ),
            (
                ("market", "bazaar", "settlement", "town", "city", "district", "quarter"),
                "a dense multi-level urban district with neon market lanes, stacked housing, service alleys, elevated transit, and utility conduits",
            ),
            (
                ("port", "harbor", "harbour", "waterfront", "dock"),
                "a floodlit industrial waterfront with container stacks, seawalls, cargo cranes, and elevated freight routes",
            ),
            (
                ("factory", "forge", "plant", "filtration", "industrial"),
                "a heavy industrial complex of processing towers, pipe forests, cooling stacks, and maintenance catwalks",
            ),
            (
                ("slum", "under", "undercity", "sublevel"),
                "a crowded undercity of improvised stacked dwellings beneath transit decks, saturated with cables, vents, and scavenged lighting",
            ),
        )
        visual = next(
            (description for keywords, description in cyberpunk_landmarks if any(keyword in cues for keyword in keywords)),
            "a distinct cyberpunk urban landmark integrated into dense vertical infrastructure",
        )
    else:
        visual = next(
            (description for keywords, description in landmark_by_cue if any(keyword in cues for keyword in keywords)),
            "a distinct regional landmark that reflects its canonical setting",
        )
    return {
        "name": name,
        "visual": visual,
        "details": details,
        "map_scope": "vertical_interior" if is_vertical_structure else "district",
    }


def _map_locations(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Merge lightweight topic rows with canonical locations for map-art direction."""
    entities: dict[str, dict[str, Any]] = {}

    def merge(entity_id: Any, entity: Mapping[str, Any]) -> None:
        identifier = _text(entity_id)
        if identifier:
            entities[identifier] = {"id": identifier, **entities.get(identifier, {}), **dict(entity)}

    for topic in detail.get("topics") or ():
        topic = _record(topic)
        if _text(topic.get("topic_id")) not in {"locations", "places", "regions", "points_of_interest"}:
            continue
        for entity in _entity_rows(_record(topic.get("content"))):
            merge(entity.get("id") or entity.get("entity_id"), entity)

    revisions = detail.get("revisions") or ()
    revision = _record(revisions[0]) if revisions else {}
    document = _record(revision.get("document"))
    topology_locations = {
        _text(location)
        for location in _record(document.get("topology")).get("locations") or ()
        if _text(location)
    }
    for source in (
        _record(_record(document.get("entity_manifest")).get("entities")),
        _record(_record(document.get("canon")).get("entities")),
    ):
        for entity_id, raw_entity in source.items():
            entity = _record(raw_entity)
            if (
                entity_id in entities
                or entity_id in topology_locations
                or _text(entity.get("kind")).lower() == "location"
            ):
                merge(entity_id, entity)
    return list(entities.values())


def _prompt(
    *,
    world: Mapping[str, Any],
    target_type: str,
    role: str,
    entity: Mapping[str, Any] | None = None,
) -> str:
    title = _text(world.get("title"), "Fantasy World")
    genre = _text(world.get("genre"), "fantasy").replace("_", " ")
    tone = _text(world.get("tone"), "heroic adventure")
    if entity is None:
        subject = title
        details = _text(world.get("description"), "a reusable campaign setting")
    else:
        subject = _text(entity.get("name") or entity.get("title"), "World entity")
        details = _entity_canon_details(
            entity,
            target_type=target_type,
            role=role,
        )
    character_kinds = {"actor", "character", "npc", "person"}
    entity_kind = _text(entity.get("kind") if entity else None, target_type).casefold()
    if role == "portrait" and entity is not None and entity_kind in character_kinds:
        return _character_portrait_prompt(
            subject=subject,
            entity=entity,
            genre=genre,
            tone=tone,
            details=details,
        )
    if role == "portrait" and entity is not None:
        return _threat_portrait_prompt(subject=subject, genre=genre, details=details)
    format_hint = {
        "cover": _PROMPT_6.text,
        "banner": _PROMPT_7.text,
        "portrait": _PROMPT_8.text,
        "icon": _PROMPT_9.text,
        "emblem": _PROMPT_10.text,
        "landscape": _PROMPT_11.text,
        "map": _map_format_hint(genre),
        "scene": _PROMPT_12.text,
        "illustration": _PROMPT_13.text,
    }.get(role, _PROMPT_5.text)
    visual_brief = _visual_subject_brief(
        subject=subject,
        details=details,
        genre=genre,
        role=role,
    )
    return (
        _PROMPT_3.format(v0=(format_hint), v1=(visual_brief), v2=(title), v3=(genre), v4=(tone))
    )


def _desired_targets(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    world = _record(detail.get("world"))
    map_blueprints = [
        {
            "map_id": _text(blueprint.get("map_id")),
            "revision": blueprint.get("blueprint_revision"),
            "document": _record(blueprint.get("document")),
        }
        for blueprint in (_record(row) for row in detail.get("map_blueprints") or ())
    ]
    map_locations = _map_locations(detail)
    map_landmarks = [_location_landmark(entity, genre=world.get("genre")) for entity in map_locations][:12]
    map_context = {"landmarks": map_landmarks, "blueprints": map_blueprints}
    canon_direction = _map_canon_direction(world=world, blueprints=map_blueprints)
    landmark_directions = "; ".join(
        f"{landmark['name']}: show {landmark['visual']}"
        + (f"; canonical cues: {landmark['details']}" if landmark["details"] else "")
        for landmark in map_landmarks
    )
    map_subject = {
        "name": f"{_text(world.get('title'), 'Fantasy World')} world map",
        "description": (
            "A coherent regional atlas. Depict every listed canonical location as "
            "a distinct visible landmark at map scale. " + _MAP_NO_TEXT_CONSTRAINT + " "
            + canon_direction + " " + landmark_directions
            if landmark_directions
            else "A coherent regional atlas showing the world’s major areas. " + canon_direction
        ),
    }
    targets = [
        {
            "target_id": "world:cover",
            "target_type": "world",
            "entity_id": str(world.get("id") or ""),
            "role": "cover",
            "source_content_hash": canonical_hash(
                {
                    "title": world.get("title"),
                    "description": world.get("description"),
                    "genre": world.get("genre"),
                    "tone": world.get("tone"),
                    "role": "cover",
                    "image_prompt_version": _IMAGE_PROMPT_VERSION,
                }
            ),
            "suggested_prompt": _prompt(
                world=world,
                target_type="world",
                role="cover",
            ),
            "metadata": {"topic_id": "overview", "aspect": "portrait"},
        },
        {
            "target_id": "world:banner",
            "target_type": "world",
            "entity_id": str(world.get("id") or ""),
            "role": "banner",
            "source_content_hash": canonical_hash(
                {
                    "title": world.get("title"),
                    "description": world.get("description"),
                    "genre": world.get("genre"),
                    "tone": world.get("tone"),
                    "role": "banner",
                    "image_prompt_version": _IMAGE_PROMPT_VERSION,
                }
            ),
            "suggested_prompt": _prompt(
                world=world,
                target_type="world",
                role="banner",
            ),
            "metadata": {"topic_id": "overview", "aspect": "landscape"},
        },
        {
            "target_id": "world:map",
            "target_type": "map",
            "entity_id": str(world.get("id") or ""),
            "role": "map",
            "source_content_hash": canonical_hash(
                {
                    "world": {
                        "title": world.get("title"),
                        "description": world.get("description"),
                        "genre": world.get("genre"),
                        "tone": world.get("tone"),
                    },
                    "map": map_context,
                    "image_prompt_version": _IMAGE_PROMPT_VERSION,
                }
            ),
            "suggested_prompt": _prompt(
                world=world,
                target_type="map",
                role="map",
                entity=map_subject,
            ),
            "metadata": {"topic_id": "map", "aspect": "landscape", "entity_name": "World map"},
        },
    ]
    blueprints_by_location = {
        _text(cast(Any, blueprint["document"]).get("location_id")): blueprint
        for blueprint in map_blueprints
        if _text(cast(Any, blueprint["document"]).get("location_id"))
    }
    for location in map_locations:
        location_id = _text(location.get("id") or location.get("entity_id"))
        if not location_id:
            continue
        landmark = _location_landmark(location, genre=world.get("genre"))
        blueprint = blueprints_by_location.get(location_id, {})
        location_canon_direction = _map_canon_direction(
            world=world,
            blueprints=[blueprint] if blueprint else (),
        )
        local_map_direction = (
            "Create a detailed, navigable interior cutaway and floor-plan map of this single vertical structure. "
            "Keep the named building as the full subject: show distinct floors or decks, elevator shafts, stairs, "
            "corridors, rooms, service ducts, access control points, and one clear vertical circulation route. "
            "Do not substitute a citywide aerial map or an unrelated surrounding district."
            if landmark["map_scope"] == "vertical_interior"
            else "Create a detailed, navigable local district map centered on this single canonical location. "
            "Show its approaches, roads or paths, boundaries, and the immediate surrounding landmarks at local scale."
        )
        local_map_subject = {
            "name": f"{landmark['name']} local map",
            "description": (
                local_map_direction + " "
                f"Show {landmark['visual']} as the central place. "
                "Follow the canonical cues exactly. " + _MAP_NO_TEXT_CONSTRAINT + " "
                + location_canon_direction + " "
                + (f"Canonical cues: {landmark['details']}" if landmark["details"] else "")
            ),
        }
        targets.append(
            {
                "target_id": f"entity:{location_id}:map",
                "target_type": "map",
                "entity_id": location_id,
                "role": "map",
                "source_content_hash": canonical_hash(
                    {
                        "location": location,
                        "blueprint": blueprint,
                        "role": "location_map",
                        "image_prompt_version": _IMAGE_PROMPT_VERSION,
                    }
                ),
                "suggested_prompt": _prompt(
                    world=world,
                    target_type="location map",
                    role="map",
                    entity=local_map_subject,
                ),
                "metadata": {
                    "topic_id": "map",
                    "map_level": "location",
                    "parent_target_id": "world:map",
                    "entity_name": landmark["name"],
                },
            }
        )
    for topic in detail.get("topics") or []:
        topic = _record(topic)
        topic_id = _text(topic.get("topic_id"))
        role = _ROLE_BY_TOPIC.get(topic_id)
        if not role:
            continue
        for entity in _entity_rows(_record(topic.get("content"))):
            entity_id = _text(entity.get("id") or entity.get("entity_id"))
            if not entity_id:
                continue
            entity_type = _text(entity.get("kind"), topic_id.rstrip("s"))
            targets.append(
                {
                    "target_id": f"entity:{entity_id}:{role}",
                    "target_type": entity_type,
                    "entity_id": entity_id,
                    "role": role,
                    "source_content_hash": canonical_hash(
                        {"entity": entity, "image_prompt_version": _IMAGE_PROMPT_VERSION}
                    ),
                    "suggested_prompt": _prompt(
                        world=world,
                        target_type=entity_type,
                        role=role,
                        entity=entity,
                    ),
                    "metadata": {
                        "topic_id": topic_id,
                        "entity_name": _text(
                            entity.get("name") or entity.get("title"),
                            entity_id,
                        ),
                    },
                }
            )
    return targets


def _upsert_targets(
    work: Any,
    context: Any,
    world_id: str,
    desired: Sequence[Mapping[str, Any]],
) -> None:
    for target in desired:
        work.connection.execute(
            """
            INSERT INTO omnix_rpg_world_image_targets (
                workspace_id, world_id, target_id, target_type, entity_id, role,
                source_content_hash, status, review_state, suggested_prompt,
                metadata_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'missing', 'pending', %s,
                      %s::jsonb)
            ON CONFLICT (workspace_id, world_id, target_id) DO UPDATE
               SET target_type = EXCLUDED.target_type,
                   entity_id = EXCLUDED.entity_id,
                   role = EXCLUDED.role,
                   status = CASE
                       WHEN omnix_rpg_world_image_targets.source_content_hash
                            = EXCLUDED.source_content_hash
                       THEN omnix_rpg_world_image_targets.status
                       WHEN omnix_rpg_world_image_targets.active_asset_id IS NOT NULL
                       THEN 'stale'
                       ELSE 'missing'
                   END,
                   review_state = CASE
                       WHEN omnix_rpg_world_image_targets.source_content_hash
                            = EXCLUDED.source_content_hash
                       THEN omnix_rpg_world_image_targets.review_state
                       ELSE 'pending'
                   END,
                   source_content_hash = EXCLUDED.source_content_hash,
                   suggested_prompt = EXCLUDED.suggested_prompt,
                   metadata_jsonb = EXCLUDED.metadata_jsonb,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (
                context.workspace_id,
                world_id,
                str(target["target_id"]),
                str(target["target_type"]),
                str(target["entity_id"]),
                str(target["role"]),
                str(target["source_content_hash"]),
                str(target["suggested_prompt"]),
                json.dumps(dict(target.get("metadata") or {}), sort_keys=True),
            ),
        )


def _job_asset_id(job: Any) -> str | None:
    for ref in getattr(job, "output_refs", ()) or ():
        if not isinstance(ref, Mapping):
            continue
        asset_id = _text(ref.get("asset_id"))
        if asset_id:
            return asset_id
    return None


def _sync_jobs(work: Any, context: Any, world_id: str) -> None:
    store = default_job_store()
    rows = work.connection.execute(
        "SELECT target_id, source_content_hash, latest_job_id, active_asset_id "
        "FROM omnix_rpg_world_image_targets WHERE workspace_id = %s "
        "AND world_id = %s AND latest_job_id IS NOT NULL",
        (context.workspace_id, world_id),
    ).fetchall()
    for target_id, source_hash, job_id, active_asset_id in rows:
        job = store.get_job(str(job_id))
        if job is None:
            continue
        job_status = _status(job.status)
        asset_id = _job_asset_id(job)
        mapped = {
            "queued": "queued",
            "running": "generating",
            "completed": "ready",
            "failed": "failed",
            "canceled": "failed",
            "stale": "failed",
        }.get(job_status, "generating")
        error = _record(getattr(job, "error", {}))
        work.connection.execute(
            "UPDATE omnix_rpg_world_image_attempts SET status = %s, asset_id = %s, "
            "error_jsonb = %s::jsonb, updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND job_id = %s",
            (
                job_status,
                asset_id,
                json.dumps(error, sort_keys=True),
                context.workspace_id,
                str(job_id),
            ),
        )
        if mapped == "ready" and asset_id and str(target_id) in {"world:cover", "world:banner"}:
            metadata_key = (
                "cover_image_asset_id"
                if str(target_id) == "world:cover"
                else "hero_image_asset_id"
            )
            work.connection.execute(
                "UPDATE omnix_rpg_worlds SET metadata_jsonb = "
                "COALESCE(metadata_jsonb, '{}'::jsonb) || %s::jsonb, "
                "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s "
                "AND EXISTS (SELECT 1 FROM omnix_rpg_world_image_targets "
                "WHERE workspace_id = %s AND world_id = %s AND target_id = %s "
                "AND review_state <> 'rejected')",
                (
                    json.dumps({metadata_key: asset_id}, sort_keys=True),
                    context.workspace_id,
                    world_id,
                    context.workspace_id,
                    world_id,
                    str(target_id),
                ),
            )
        if mapped == "ready" and not asset_id:
            mapped = "failed"
        work.connection.execute(
            "UPDATE omnix_rpg_world_image_targets SET status = %s, "
            "active_asset_id = CASE WHEN %s = 'ready' AND %s::text IS NOT NULL "
            "AND review_state <> 'rejected' THEN %s ELSE active_asset_id END, "
            "review_state = CASE WHEN %s = 'ready' AND %s::text IS NOT NULL "
            "AND review_state = 'pending' THEN 'approved' ELSE review_state END, "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s "
            "AND world_id = %s AND target_id = %s",
            (
                mapped,
                mapped,
                asset_id,
                asset_id,
                mapped,
                asset_id,
                context.workspace_id,
                world_id,
                str(target_id),
            ),
        )


def _list_targets(work: Any, context: Any, world_id: str) -> list[dict[str, Any]]:
    rows = work.connection.execute(
        "SELECT world_id, target_id, target_type, entity_id, role, "
        "source_content_hash, status, review_state, suggested_prompt, "
        "active_asset_id, latest_job_id, metadata_jsonb, created_at, updated_at "
        "FROM omnix_rpg_world_image_targets WHERE workspace_id = %s "
        "AND world_id = %s ORDER BY target_type, role, target_id",
        (context.workspace_id, world_id),
    ).fetchall()
    targets = [_target_row(row) for row in rows]
    for target in targets:
        attempts = work.connection.execute(
            "SELECT job_id, prompt, source_content_hash, status, asset_id, "
            "error_jsonb, created_at, updated_at FROM omnix_rpg_world_image_attempts "
            "WHERE workspace_id = %s AND world_id = %s AND target_id = %s "
            "ORDER BY created_at DESC LIMIT 20",
            (context.workspace_id, world_id, target["target_id"]),
        ).fetchall()
        target["attempts"] = [_attempt_row(row) for row in attempts]
    return targets


def read_world_image_targets(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    detail = read_world_detail(world_id, database=database)
    context = current_tenant()
    with unit_of_work(database) as work:
        require_world_writable(work, context, world_id)
        _upsert_targets(work, context, world_id, _desired_targets(detail))
        _sync_jobs(work, context, world_id)
        targets = _list_targets(work, context, world_id)
        work.commit()
    return {"ok": True, "world": detail["world"], "targets": targets}


def _selected_targets(
    all_targets: Sequence[Mapping[str, Any]],
    target_ids: Sequence[str],
) -> list[Mapping[str, Any]]:
    by_id = {str(target["target_id"]): target for target in all_targets}
    selected = [by_id[target_id] for target_id in target_ids if target_id in by_id]
    missing = sorted(set(target_ids) - set(by_id))
    if missing:
        raise KeyError("world_image_targets_not_found:" + ",".join(missing))
    if not selected:
        raise ValueError("world_image_generation_targets_required")
    return selected


def generate_world_images(
    world_id: str,
    *,
    target_ids: Sequence[str],
    prompts: Mapping[str, str] | None = None,
    provider_id: str = "",
    width: int = 768,
    height: int = 768,
    style: str = "",
    no_cache: bool = False,
    database: Any | None = None, target_reader: Callable[..., dict[str, Any]] = read_world_image_targets,
) -> dict[str, Any]:
    from .world_image_jobs import create_world_image_job

    materialized = target_reader(world_id, database=database)
    selected = _selected_targets(materialized["targets"], target_ids)
    context = current_tenant()
    jobs: list[dict[str, Any]] = []
    with unit_of_work(database) as work:
        require_world_writable(work, context, world_id)
        for target in selected:
            prompt = _text(
                (prompts or {}).get(str(target["target_id"])),
                str(target["suggested_prompt"]),
            )
            is_location_map = _text(_record(target.get("metadata")).get("map_level")) == "location"
            # Flux Klein caps requests at 1,048,576 pixels. Keep local maps square
            # at the maximum supported resolution so they remain useful for deep zoom.
            if is_location_map:
                target_width, target_height = 1024, 1024
            else:
                target_width = 1024 if target["role"] in {"banner", "map"} else width
                target_height = 576 if target["role"] == "banner" else 768 if target["role"] == "map" else height
            job = create_world_image_job(
                # Job ownership is a user foreign key.  The workspace ID scopes
                # the record separately in the PostgreSQL job store, but is not
                # itself a valid job owner.
                owner_id=str(context.user_id),
                payload={
                    "prompt": prompt,
                    "provider_id": provider_id,
                    "width": target_width,
                    "height": target_height,
                    "style": style,
                    "no_cache": no_cache,
                    "metadata": {
                        "world_id": world_id,
                        "target_id": target["target_id"],
                        "target_type": target["target_type"],
                        "entity_id": target["entity_id"],
                        "role": target["role"],
                        "source_content_hash": target["source_content_hash"],
                    },
                },
                job_store=default_job_store(),
            )
            work.connection.execute(
                "INSERT INTO omnix_rpg_world_image_attempts (workspace_id, "
                "world_id, target_id, job_id, prompt, source_content_hash, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'queued')",
                (
                    context.workspace_id,
                    world_id,
                    target["target_id"],
                    job.id,
                    prompt,
                    target["source_content_hash"],
                ),
            )
            work.connection.execute(
                "UPDATE omnix_rpg_world_image_targets SET latest_job_id = %s, "
                "suggested_prompt = %s, status = 'queued', review_state = 'pending', "
                "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s "
                "AND world_id = %s AND target_id = %s",
                (
                    job.id,
                    prompt,
                    context.workspace_id,
                    world_id,
                    target["target_id"],
                ),
            )
            jobs.append(
                {
                    "job_id": job.id,
                    "target_id": target["target_id"],
                    "status": _status(job.status),
                }
            )
        work.commit()
    return {"ok": True, "world_id": world_id, "jobs": jobs}


def update_world_image_target(
    world_id: str,
    target_id: str,
    *,
    review_state: str | None = None,
    active_asset_id: str | None = None,
    suggested_prompt: str | None = None,
    database: Any | None = None, target_reader: Callable[..., dict[str, Any]] = read_world_image_targets,
) -> dict[str, Any]:
    if review_state is not None and review_state not in {"pending", "approved", "rejected"}:
        raise ValueError(f"invalid_image_review_state:{review_state}")
    context = current_tenant()
    with unit_of_work(database) as work:
        world = require_world_writable(work, context, world_id)
        row = work.connection.execute(
            "SELECT latest_job_id, active_asset_id, role, metadata_jsonb "
            "FROM omnix_rpg_world_image_targets WHERE workspace_id = %s "
            "AND world_id = %s AND target_id = %s FOR UPDATE",
            (context.workspace_id, world_id, target_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"world_image_target_not_found:{world_id}:{target_id}")
        latest_asset = active_asset_id
        if review_state == "approved" and not latest_asset and row[0]:
            job = default_job_store().get_job(str(row[0]))
            latest_asset = _job_asset_id(job) if job is not None else None
            if not latest_asset:
                raise ValueError(f"world_image_target_has_no_completed_asset:{target_id}")
        status = "rejected" if review_state == "rejected" else "ready" if latest_asset else None
        work.connection.execute(
            "UPDATE omnix_rpg_world_image_targets SET review_state = COALESCE(%s, "
            "review_state), active_asset_id = COALESCE(%s, active_asset_id), "
            "suggested_prompt = COALESCE(%s, suggested_prompt), status = COALESCE(%s, "
            "status), updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s "
            "AND world_id = %s AND target_id = %s",
            (
                review_state,
                latest_asset,
                suggested_prompt,
                status,
                context.workspace_id,
                world_id,
                target_id,
            ),
        )
        if review_state == "approved" and latest_asset and target_id == "world:cover":
            metadata = {
                **_record(world.get("metadata")),
                "cover_image_asset_id": latest_asset,
            }
            work.connection.execute(
                "UPDATE omnix_rpg_worlds SET metadata_jsonb = %s::jsonb, "
                "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
                (
                    json.dumps(metadata, sort_keys=True),
                    context.workspace_id,
                    world_id,
                ),
            )
        if review_state == "approved" and latest_asset and target_id == "world:banner":
            metadata = {
                **_record(world.get("metadata")),
                "hero_image_asset_id": latest_asset,
            }
            work.connection.execute(
                "UPDATE omnix_rpg_worlds SET metadata_jsonb = %s::jsonb, "
                "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
                (
                    json.dumps(metadata, sort_keys=True),
                    context.workspace_id,
                    world_id,
                ),
            )
        work.commit()
    return target_reader(world_id, database=database)


def approved_world_asset_bindings(
    work: Any, context: Any, world_id: str
) -> dict[str, Any]:
    rows = work.connection.execute(
        "SELECT target_id, target_type, entity_id, role, source_content_hash, "
        "active_asset_id FROM omnix_rpg_world_image_targets WHERE workspace_id = %s "
        "AND world_id = %s AND review_state = 'approved' "
        "AND active_asset_id IS NOT NULL ORDER BY target_id",
        (context.workspace_id, world_id),
    ).fetchall()
    return {
        str(row[0]): {
            "target_type": str(row[1]),
            "entity_id": str(row[2]),
            "role": str(row[3]),
            "source_content_hash": str(row[4]),
            "asset_id": str(row[5]),
        }
        for row in rows
    }
