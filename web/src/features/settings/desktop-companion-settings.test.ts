import { describe, expect, it } from 'vitest';

import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';


describe('desktop companion central settings', () => {
  it('ships disabled with conservative local-first limits', () => {
    const value = DEFAULT_SETTINGS_DOCUMENT.assistant;

    expect(value.desktopCompanionEnabled).toBe(false);
    expect(value.desktopCompanionRolloutStage).toBe('disabled');
    expect(value.desktopCompanionRemoteVisionAllowed).toBe(false);
    expect(value.desktopCompanionShowDiagnostics).toBe(false);
    expect(value.desktopCompanionBackgroundCallsPerMinute).toBe(6);
    expect(value.desktopCompanionMinimumObservationIntervalMs).toBe(8_000);
    expect(value.desktopCompanionObservationTimeoutMs).toBe(10_000);
    expect(value.desktopCompanionObservationTtlMs).toBe(12_000);
    expect(value.desktopCompanionCommentaryCooldownMs).toBe(25_000);
    expect(value.desktopCompanionMinimumChangeConfidence).toBe(0.55);
  });
});
