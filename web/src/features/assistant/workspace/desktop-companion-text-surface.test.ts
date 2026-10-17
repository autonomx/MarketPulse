import { describe, expect, it } from 'vitest';

import { normalizeDesktopCompanionTextNotice } from './desktop-companion-text-surface';

describe('desktop companion text surface', () => {
  it('normalizes one bounded transient comment', () => {
    expect(normalizeDesktopCompanionTextNotice({
      sessionId: 'chat:1',
      observationId: 'obs:1',
      turnId: 'desktop:1',
      content: '  That inventory change looks important.  ',
      priority: 'normal',
      expiresAtMs: 20_000,
    })).toEqual({
      sessionId: 'chat:1',
      observationId: 'obs:1',
      turnId: 'desktop:1',
      content: 'That inventory change looks important.',
      priority: 'normal',
      expiresAtMs: 20_000,
    });
  });

  it('rejects incomplete notices', () => {
    expect(normalizeDesktopCompanionTextNotice({ content: 'missing ids' })).toBeNull();
  });
});
