import { describe, expect, it } from 'vitest';
import { partyMembers, previewEncounter, previewHeroSummary } from './rpgUiState';
import { createRpgCombatSurfaceState } from './rpgCombatState';

describe('rpg combat surface state', () => {
  it('keeps combat controls disabled for preview or inactive encounters', () => {
    const state = createRpgCombatSurfaceState({ encounter: previewEncounter, heroSummary: previewHeroSummary, partyMembers });

    expect(state).toMatchObject({ active: false, source: 'preview', title: 'No active combat', statusLabel: 'Exploration mode' });
    expect(state.initiativeQueue).toEqual([]);
    expect(state.combatants).toEqual([]);
    expect(state.actions.every((action) => action.disabled)).toBe(true);
    expect(state.resultDeltas).toEqual(['No combat deltas for the selected session.']);
  });

  it('builds a tactical live surface from encounter combatants', () => {
    const state = createRpgCombatSurfaceState({
      encounter: {
        icon: '⚔',
        title: 'Bandit ambush',
        detail: 'Combatants: Road bandit, Lookout',
        source: 'live',
      },
      heroSummary: { ...previewHeroSummary, name: 'Mira Vale' },
      partyMembers,
    });

    expect(state.active).toBe(true);
    expect(state.title).toBe('Bandit ambush');
    expect(state.statusLabel).toBe('Combat turn gate active');
    expect(state.initiativeQueue).toEqual(['Mira Vale', 'Road bandit', 'Thorin Ironfist', 'Lookout', 'Elandra']);
    expect(state.combatants[0]).toMatchObject({ name: 'Road bandit', role: 'Hostile', tone: 'enemy' });
    expect(state.combatants).toContainEqual(expect.objectContaining({ name: 'Thorin Ironfist', tone: 'ally' }));
    expect(state.actions.find((action) => action.label === 'Attack')).toMatchObject({ disabled: false, command: 'Attack the most immediate threat in Bandit ambush.' });
    expect(state.resultDeltas[1]).toBe('Combatants: Road bandit, Lookout');
  });
});
