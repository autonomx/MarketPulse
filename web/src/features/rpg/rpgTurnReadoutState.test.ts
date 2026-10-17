import { describe, expect, it } from 'vitest';
import { createRpgTurnReadoutPreview } from './rpgTurnReadoutState';

describe('createRpgTurnReadoutPreview', () => {
  it('maps a successful Hermes turn readout into rail preview props', () => {
    expect(createRpgTurnReadoutPreview({
      ok: true,
      turn: { category: 'dialogue' },
      systems: ['command_parser', 'npc_dialogue'],
      effect_count: 2,
      grounding_status: 'checked',
    })).toEqual({
      category: 'dialogue',
      systems: ['command_parser', 'npc_dialogue'],
      effectCount: 2,
      groundingStatus: 'checked',
    });
  });

  it('preserves empty but successful readout details', () => {
    expect(createRpgTurnReadoutPreview({
      ok: true,
      turn: { category: 'general' },
      systems: [],
      effect_count: 0,
      grounding_status: 'not_reported',
    })).toEqual({
      category: 'general',
      systems: [],
      effectCount: 0,
      groundingStatus: 'not_reported',
    });
  });

  it('hides failed or missing readouts', () => {
    expect(createRpgTurnReadoutPreview(undefined)).toBeUndefined();
    expect(createRpgTurnReadoutPreview({ ok: false, error: 'missing_turn' })).toBeUndefined();
  });
});
