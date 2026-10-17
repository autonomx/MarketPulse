import { describe, expect, it } from 'vitest';

import { CompanionInitiativeArbiter } from './companion-initiative-arbiter';

describe('CompanionInitiativeArbiter', () => {
  it('allows only one active companion initiative at a time', () => {
    const arbiter = new CompanionInitiativeArbiter();
    const desktop = arbiter.begin({ source: 'desktop', nowMs: 1000, cooldownMs: 5000 });
    expect(desktop.accepted).toBe(true);
    expect(desktop.token).toBeTruthy();

    const social = arbiter.begin({ source: 'social', nowMs: 1100, cooldownMs: 5000 });
    expect(social).toMatchObject({ accepted: false, reason: 'initiative_active' });

    expect(arbiter.finish(desktop.token, 1200, true)).toBe(true);
    expect(arbiter.snapshot()).toMatchObject({
      activeSource: null,
      lastDeliveredSource: 'desktop',
      lastDeliveredAtMs: 1200,
    });
  });

  it('enforces a shared cooldown but lets critical desktop reactions bypass it', () => {
    const arbiter = new CompanionInitiativeArbiter();
    const first = arbiter.begin({ source: 'social', nowMs: 1000, cooldownMs: 5000 });
    arbiter.finish(first.token, 1200, true);

    const ambient = arbiter.begin({ source: 'ambient', nowMs: 2000, cooldownMs: 5000 });
    expect(ambient.accepted).toBe(false);
    expect(ambient.reason).toBe('initiative_cooldown');
    expect(ambient.eligibleInMs).toBe(4200);

    const critical = arbiter.begin({
      source: 'desktop',
      priority: 'critical',
      nowMs: 2000,
      cooldownMs: 5000,
    });
    expect(critical.accepted).toBe(true);
  });

  it('does not consume cooldown when generation is discarded before delivery', () => {
    const arbiter = new CompanionInitiativeArbiter();
    const first = arbiter.begin({ source: 'ambient', nowMs: 1000, cooldownMs: 5000 });
    expect(arbiter.finish(first.token, 1100, false)).toBe(true);

    const second = arbiter.begin({ source: 'social', nowMs: 1200, cooldownMs: 5000 });
    expect(second.accepted).toBe(true);
  });
});
