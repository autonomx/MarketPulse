import { describe, expect, it } from 'vitest';

import { createOnsetTimingPlan, naturalPauseAfterClause } from './live-voice-natural-timing';

describe('live voice natural timing', () => {
  it('uses existing latency as perceived thinking time', () => {
    expect(createOnsetTimingPlan(120)).toEqual({
      desiredPerceivedOnsetMs: 450,
      elapsedMs: 120,
      extraDelayMs: 330,
    });
    expect(createOnsetTimingPlan(600).extraDelayMs).toBe(0);
    expect(createOnsetTimingPlan(0, { urgent: true }).extraDelayMs).toBe(0);
  });

  it('caps additional onset delay', () => {
    expect(createOnsetTimingPlan(0, {
      desiredPerceivedOnsetMs: 800,
      maximumAdditionalDelayMs: 250,
    }).extraDelayMs).toBe(250);
  });

  it('creates deterministic clause, thought, and reflection pauses', () => {
    const clause = naturalPauseAfterClause('That is the first result.', 0);
    const thought = naturalPauseAfterClause('There are two considerations:', 1);
    const reflection = naturalPauseAfterClause('I think the safer option is to wait.', 2);

    expect(clause?.reason).toBe('clause');
    expect(clause?.durationMs).toBeGreaterThanOrEqual(80);
    expect(clause?.durationMs).toBeLessThanOrEqual(140);
    expect(thought?.reason).toBe('thought');
    expect(thought?.durationMs).toBeGreaterThanOrEqual(150);
    expect(thought?.durationMs).toBeLessThanOrEqual(240);
    expect(reflection?.reason).toBe('reflection');
    expect(reflection?.durationMs).toBeGreaterThanOrEqual(280);
    expect(reflection?.durationMs).toBeLessThanOrEqual(420);
    expect(naturalPauseAfterClause('That is the first result.', 0)).toEqual(clause);
  });
});
