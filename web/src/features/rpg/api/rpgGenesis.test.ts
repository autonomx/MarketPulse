/* eslint-disable @typescript-eslint/no-explicit-any -- baseline WP-9.x */
import { describe, expect, it } from 'vitest';
import type { RpgNewGameRequest } from './rpgSessionClient';
import { withRpgGenesisContract } from './rpgGenesis';

describe('RPG genesis request helper', () => {
  it('adds a v2 genesis contract to legacy-shaped new-game requests', () => {
    const request: RpgNewGameRequest & Record<string, unknown> = {
      campaign_template: 'deterministic_rpg_campaign',
      tone: 'careful setup',
      background: 'wanderer',
      starting_location: 'northern_watch_post',
      player: {
        name: 'Mira',
        pronouns: 'she/her',
        background: 'wanderer',
        build: 'ranger',
        portrait_seed: 9137,
      },
      primary_capability: 'recon',
      secondary_capabilities: ['survival', 'technical'],
      power_source: 'magic',
      generated_class_name: 'Road Scout',
      generated_class_summary: 'Presentation text only.',
      difficulty: 'harsh',
      world_activity: 'living_world',
      economy_pressure: 'strict',
      combat_lethality: 'deadly',
      companions_enabled: true,
      permadeath: false,
      seed: 9137,
      initial_stats: { strength: 8, agility: 9, endurance: 8, intellect: 8, charisma: 8, perception: 11, archery: 8, survival: 8 },
      opening_hook: 'bandit_trail',
      opening_pace: 'immediate_action',
      relationship_preset: 'known_contact_nearby',
      story_options: {
        opening_hook: 'bandit_trail',
        opening_pace: 'immediate_action',
        relationship_preset: 'known_contact_nearby',
      },
      system_options: {
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
      features: {
        autosave: true,
        validator: true,
        background_soft_audit: true,
        llm_narration: true,
        image_generation: false,
        stt: true,
        tts: true,
      },
    };

    const promoted = withRpgGenesisContract(request) as RpgNewGameRequest & { genesis: Record<string, any> };

    expect(promoted.generated_class_summary).toBe('Presentation text only.');
    expect(promoted.genesis.contract_version).toBe('rpg_genesis_v2');
    expect(promoted.genesis.identity).toMatchObject({ name: 'Mira', origin: 'wanderer', power_source: 'magic' });
    expect(promoted.genesis.initial_stats).toMatchObject({ perception: 11, archery: 8 });
    expect(promoted.genesis.story_options).toMatchObject({ opening_hook: 'bandit_trail', opening_pace: 'immediate_action' });
    expect(promoted.genesis.world_options).toMatchObject({ starting_location: 'northern_watch_post', difficulty: 'harsh', seed: 9137 });
    expect(promoted.genesis.system_options).toMatchObject({ companions: true, stt: true, tts: true });
    expect(promoted.genesis.drivers.talents).toEqual([
      { id: 'reconnaissance', rank: 2 },
      { id: 'survival_sense', rank: 1 },
      { id: 'technical_handling', rank: 1 },
    ]);
    expect(promoted.genesis.starter_gear_tags).toEqual(expect.arrayContaining(['survival_tool', 'field_notes']));
  });

  it('does not rewrite requests that already provide genesis', () => {
    const request: RpgNewGameRequest = {
      genesis: {
        contract_version: 'rpg_genesis_v2',
        identity: { name: 'Existing' },
      },
    };

    expect(withRpgGenesisContract(request)).toBe(request);
  });
});
