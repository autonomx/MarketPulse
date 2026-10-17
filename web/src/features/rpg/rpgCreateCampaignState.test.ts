import { describe, expect, it } from 'vitest';
import { buildRpgNewGameRequest, initialStats, type CampaignCreationSelections } from './rpgCreateCampaignState';

const baseSelections: CampaignCreationSelections = {
  background: 'wanderer',
  buildKey: 'scout',
  capabilities: {
    combat: true,
    influence: false,
    knowledge: false,
    support: false,
    survival: true,
    technical: true,
  },
  characterName: 'Mira',
  combatLethality: 'deadly',
  difficulty: 'hard',
  economyPressure: 'tight',
  openingHook: 'bandit-trail',
  openingPace: 'immediate-action',
  powerSource: 'arcane',
  primaryCapability: 'craft',
  pronouns: 'she/her',
  relationshipPreset: 'known-contact',
  seed: '9137',
  startingLocation: 'watch-post',
  stats: { ...initialStats, perception: 11 },
  systems: {
    autosave: true,
    companions: true,
    grounding: true,
    images: false,
    narration: true,
    permadeath: false,
    softAudit: true,
    stt: true,
    tts: true,
  },
  worldActivity: 'busy',
};

describe('RPG campaign creation state', () => {
  it('maps wizard selections to the supported RPG new-game request contract', () => {
    const request = buildRpgNewGameRequest(baseSelections) as Record<string, unknown>;

    expect(request).toMatchObject({
      campaign_template: 'deterministic_rpg_campaign',
      combat_lethality: 'deadly',
      difficulty: 'harsh',
      economy_pressure: 'strict',
      opening_hook: 'bandit_trail',
      opening_pace: 'immediate_action',
      power_source: 'magic',
      primary_capability: 'technical',
      relationship_preset: 'known_contact_nearby',
      seed: 9137,
      starting_location: 'northern_watch_post',
      world_activity: 'living_world',
    });
    expect(request.player).toMatchObject({ build: 'ranger', name: 'Mira', portrait_seed: 9137, pronouns: 'she/her' });
    expect(request.secondary_capabilities).toEqual(['combat', 'survival']);
    expect(request.initial_stats).toMatchObject({ perception: 14 });
    expect(request.starter_gear).toContain('Shortbow');
    expect(request.features).toMatchObject({ autosave: true, background_soft_audit: true, image_generation: false, validator: true });
    expect(request.generated_class_summary).toContain('Road Scout');
    expect(request.generated_class_summary).toContain('Opens with Bandit Trail');
    expect(request.generated_class_summary).not.toContain('Stats:');
    expect(request.generated_class_summary).not.toContain('Starter gear:');
    expect(request.story_options).toMatchObject({
      opening_hook: 'bandit_trail',
      opening_hook_label: 'Bandit Trail',
      opening_pace: 'immediate_action',
      relationship_label: 'Known contact nearby',
      relationship_preset: 'known_contact_nearby',
    });
  });

  it('falls back to nullable seed and normalized safe defaults', () => {
    const request = buildRpgNewGameRequest({
      ...baseSelections,
      buildKey: 'negotiator',
      combatLethality: 'forgiving',
      difficulty: 'story',
      economyPressure: 'low',
      openingHook: undefined,
      openingPace: undefined,
      powerSource: 'technique',
      primaryCapability: 'recon',
      relationshipPreset: undefined,
      seed: '',
      startingLocation: 'rusty-flagons',
      worldActivity: 'quiet',
    }) as Record<string, unknown>;

    expect(request).toMatchObject({
      combat_lethality: 'safe',
      difficulty: 'story',
      economy_pressure: 'relaxed',
      opening_hook: 'tavern_rumor',
      opening_pace: 'balanced',
      power_source: 'martial',
      primary_capability: 'recon',
      relationship_preset: 'unknown_outsider',
      seed: null,
      starting_location: 'rusty_flagon_tavern',
      world_activity: 'quiet',
    });
    expect(request.generated_class_summary).toContain('Silver-Tongued Agent');
    expect(request.generated_class_summary).toContain('Opens with Tavern Rumor at Balanced pace');
    expect(request.generated_class_summary).not.toContain('Pace:');
    expect(request.generated_class_summary).not.toContain('Relationship:');
    expect(request.player).toMatchObject({ build: 'silver_tongue', portrait_seed: null });
  });
});
