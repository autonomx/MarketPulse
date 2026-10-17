import { describe, expect, it } from 'vitest';
import { createRpgWorkspaceState } from './rpgUiState';

describe('RPG environment UI state', () => {
  it('uses environment_snapshot values for the live world rail', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [
          {
            session_id: 'snapshot-session',
            state: {
              environment_snapshot: {
                region_id: 'market_road',
                calendar: { season_label: 'Early Spring', time_label: 'Day 1' },
                display: {
                  season: 'Early Spring',
                  day_time: 'Day 1',
                  weather: 'Rain',
                  temperature: '7C',
                  wind: 'Light',
                  visibility: 'Interior',
                  light: 'Lamp Lit',
                  terrain: 'Interior Floor',
                  context: 'Indoor',
                },
                context: { location_label: 'Rusty Flagon Tavern' },
              },
            },
          },
        ],
      },
    });
    const rows = Object.fromEntries(state.worldStateRows.map((row) => [row.label, row.value]));

    expect(rows['Calendar / Season']).toBe('Early Spring');
    expect(rows.Region).toBe('market_road');
    expect(rows.Weather).toBe('Rain');
    expect(rows.Temperature).toBe('7C');
    expect(rows.Hazards).toBe('Not tracked yet');
    expect(rows.Reputation).toBeUndefined();
    expect(state.selectedSessionSummary.location).toBe('Rusty Flagon Tavern');
  });
});
