import { describe, expect, it } from 'vitest';
import type {
  RpgAuthoringCollectionPage,
  RpgAuthoringEntityCard,
  RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import { mergeRuntimeLoreCards } from './RpgWorldEditorShell';

function card(
  id: string,
  title: string,
  topicId = 'actors',
): RpgAuthoringEntityCard {
  return {
    id,
    title,
    summary: `${title} summary`,
    kind: 'npc',
    card_type: 'actors',
    dossier: {
      schema_version: 'rpg_world_entity_dossier_v1',
      quick_facts: [],
      sections: [{
        id: 'overview',
        title: 'Overview',
        paragraphs: [`${title} has a complete gameplay dossier.`],
      }],
      related_entity_ids: [],
    },
    presentation: {
      variant: 'actors',
      eyebrow: 'Actor / NPC',
      badges: [],
      highlights: [],
      groups: [],
    },
    metadata: { lore_origin: 'gameplay', lore_topic_id: topicId },
  };
}

const actors: RpgAuthoringSection = {
  id: 'actors',
  label: 'Actors and NPCs',
  group: 'world',
  page_kind: 'collection',
  topic_ids: ['actors'],
  entity_kind: 'actor',
  dependencies: [],
  required_before_launch: true,
  supports_generation: true,
  supports_images: true,
  supports_entity_editing: true,
  operational_status: 'complete',
  editorial_status: 'approved',
  entity_count: 1,
};

const page: RpgAuthoringCollectionPage = {
  ok: true,
  section_id: 'actors',
  page_kind: 'collection',
  title: 'Actors and NPCs',
  entities: [card('npc:juno', 'Juno Rask')],
  filters: [],
  sort_options: ['name'],
};

describe('mergeRuntimeLoreCards', () => {
  it('adds gameplay-created characters to the Actors and NPCs catalogue', () => {
    const merged = mergeRuntimeLoreCards(
      page,
      actors,
      [card('npc:helix', 'Helix')],
    );

    expect(merged?.page_kind).toBe('collection');
    if (merged?.page_kind !== 'collection') throw new Error('expected collection');
    expect(merged.entities.map((entity) => entity.title)).toEqual([
      'Juno Rask',
      'Helix',
    ]);
    expect(merged.entities[1].dossier?.sections[0].paragraphs[0]).toContain(
      'complete gameplay dossier',
    );
  });

  it('keeps the published World Forge card when a campaign projection repeats it', () => {
    const merged = mergeRuntimeLoreCards(
      page,
      actors,
      [card('NPC:JUNO', 'Runtime Juno')],
    );

    if (merged?.page_kind !== 'collection') throw new Error('expected collection');
    expect(merged.entities).toHaveLength(1);
    expect(merged.entities[0].title).toBe('Juno Rask');
  });

  it('routes new items into a profile-specific equipment collection', () => {
    const equipmentSection: RpgAuthoringSection = {
      ...actors,
      id: 'equipment_vehicles',
      label: 'Weapons, Equipment, Vehicles and Commodities',
      topic_ids: ['equipment_vehicles'],
      entity_kind: 'item',
    };
    const equipmentPage: RpgAuthoringCollectionPage = {
      ...page,
      section_id: 'equipment_vehicles',
      title: equipmentSection.label,
      entities: [],
    };

    const merged = mergeRuntimeLoreCards(
      equipmentPage,
      equipmentSection,
      [card('item:ghost-key', 'Ghost Key', 'equipment_vehicles')],
    );

    if (merged?.page_kind !== 'collection') throw new Error('expected collection');
    expect(merged.entities.map((entity) => entity.title)).toEqual(['Ghost Key']);
  });
});
