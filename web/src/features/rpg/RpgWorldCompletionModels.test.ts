import { describe, expect, it } from 'vitest';
import type { RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import {
  completeAuthoringSections,
  documentAnchors,
  isChronicleSection,
  parseWorldEditorRoute,
  presentLoreBlocks,
  worldEditorSearch,
} from './RpgWorldCompletionModels';

function section(id: string, group: RpgAuthoringSection['group']): RpgAuthoringSection {
  return {
    id,
    label: id,
    group,
    page_kind: 'document',
    topic_ids: [id],
    dependencies: [],
    required_before_launch: false,
    supports_generation: true,
    supports_images: false,
    supports_entity_editing: false,
    operational_status: 'complete',
    editorial_status: 'unreviewed',
    entity_count: 0,
  };
}

describe('world authoring completion models', () => {
  it('uses only profile-backed sections supplied by the authoring manifest', () => {
    const completed = completeAuthoringSections([
      section('overview', 'workspace'),
      section('networks', 'world'),
      section('augmentations', 'world'),
      section('opening_threads', 'game-master'),
    ]);

    expect(completed.map((row) => row.id)).toEqual([
      'overview',
      'networks',
      'augmentations',
      'opening_threads',
    ]);
    expect(completed.some((row) => row.id === 'spells')).toBe(false);
    expect(completed.some((row) => row.id === 'pantheon')).toBe(false);
    expect(completed.some((row) => row.id === 'hero_system')).toBe(false);
  });

  it('keeps manifest order within each navigation group', () => {
    const completed = completeAuthoringSections([
      section('pressures', 'lore'),
      section('overview', 'workspace'),
      section('actors', 'world'),
      section('places', 'world'),
    ]);

    expect(completed.map((row) => row.id)).toEqual([
      'overview',
      'actors',
      'places',
      'pressures',
    ]);
  });

  it('creates unique anchors when lore sections repeat a title', () => {
    expect(documentAnchors([
      { kind: 'section', title: 'Major Powers' },
      { kind: 'facts', title: 'Major Powers' },
      { kind: 'section', title: 'Major Powers' },
    ])).toEqual([
      { id: 'major-powers', label: 'Major Powers' },
      { id: 'major-powers-2', label: 'Major Powers' },
      { id: 'major-powers-3', label: 'Major Powers' },
    ]);
  });

  it('splits generated lore into proper headed paragraph sections', () => {
    const blocks = presentLoreBlocks('cultures', [{
      kind: 'section',
      title: 'Cultures and Peoples',
      body: '## Origins\n\nThe river clans trace their shared identity to the first flood.\n\n## Customs\n\nSeasonal oath feasts renew alliances between families.',
    }]);

    expect(blocks).toEqual([
      {
        kind: 'section',
        title: 'Origins',
        body: 'The river clans trace their shared identity to the first flood.',
      },
      {
        kind: 'section',
        title: 'Customs',
        body: 'Seasonal oath feasts renew alliances between families.',
      },
    ]);
  });

  it('uses hybrid prose and timeline presentation for history', () => {
    const blocks = presentLoreBlocks('history', [
      { kind: 'section', title: 'Overview', body: 'The realm records history through royal and monastic archives.' },
      { kind: 'section', title: 'First Age', body: 'The first age begins when the river kingdoms unite.' },
      {
        kind: 'facts',
        title: 'Turning Points',
        items: [{ label: 'The Sundering', year: 431, statement: 'The realm divides after the western succession war.' }],
      },
    ]);
    const [, firstTimeline] = blocks;
    const [firstEntry] = firstTimeline?.items ?? [];

    expect(blocks.map((block) => block.kind)).toEqual(['section', 'timeline', 'timeline']);
    expect(firstEntry).toMatchObject({ title: 'First Age', era: 'First Age' });
  });

  it('recognises profile and legacy time-based lore IDs but not active conflicts', () => {
    expect(isChronicleSection('history')).toBe(true);
    expect(isChronicleSection('history_timeline')).toBe(true);
    expect(isChronicleSection('calendar')).toBe(true);
    expect(isChronicleSection('calendar_and_eras')).toBe(true);
    expect(isChronicleSection('current_conflicts')).toBe(false);
    const [conflictBlock] = presentLoreBlocks('current_conflicts', [
      { kind: 'section', title: 'Escalation', body: 'Two rival courts are mobilising their border levies.' },
    ]);
    expect(conflictBlock?.kind).toBe('section');
  });

  it('round-trips direct entity routes through query parameters', () => {
    const search = worldEditorSearch({ worldId: 'world:aurelia', sectionId: 'classes', entityId: 'class:warden' }, '?keep=1');
    expect(search).toContain('keep=1');
    expect(parseWorldEditorRoute(search)).toEqual({
      worldId: 'world:aurelia',
      sectionId: 'classes',
      entityId: 'class:warden',
    });
  });
});
