import { describe, expect, it } from 'vitest';
import { quickActionsFromHermesSuggestions } from './RpgWorkspace';

describe('quickActionsFromHermesSuggestions', () => {
  it('projects grounded Hermes suggestions into response options', () => {
    expect(quickActionsFromHermesSuggestions([
      {
        command: 'focus on the objective: Strike confrontation',
        kind: 'objective',
        label: 'Pursue objective: Strike confrontation',
      },
      {
        command: 'ask Juno Rask about the current situation',
        kind: 'dialogue',
        label: 'Ask Juno Rask what they know',
      },
    ])).toEqual([
      {
        command: 'focus on the objective: Strike confrontation',
        icon: '◆',
        label: 'Pursue objective: Strike confrontation',
      },
      {
        command: 'ask Juno Rask about the current situation',
        icon: '☯',
        label: 'Ask Juno Rask what they know',
      },
    ]);
  });
});
