import { describe, expect, it } from 'vitest';
import type { paths } from './generated';

describe('RPG generated gateway types', () => {
  it('holds the RPG and Hermes operations', () => {
    const adventureTemplates = '/api/rpg/adventure/templates' satisfies keyof paths;
    const sessionList = '/api/rpg/session/list' satisfies keyof paths;
    const playerState = '/api/rpg/player/state' satisfies keyof paths;
    const inspectTimeline = '/api/rpg/inspect/timeline' satisfies keyof paths;
    const hermesStatus = '/api/hermes/status' satisfies keyof paths;

    expect([adventureTemplates, sessionList, playerState, inspectTimeline, hermesStatus]).toHaveLength(5);
  });
});
