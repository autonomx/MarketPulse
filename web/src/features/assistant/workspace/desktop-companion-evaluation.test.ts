import { describe, expect, it } from 'vitest';

import { DesktopCompanionEvaluationAccumulator } from './desktop-companion-evaluation';


describe('desktop companion evaluation accumulator', () => {
  it('produces content-free bounded metrics for release gates', () => {
    const startedAt = new Date('2026-07-14T12:00:00Z');
    const accumulator = new DesktopCompanionEvaluationAccumulator({
      runId: 'run-1',
      sessionId: 'chat-1',
      exactCommitSha: '94a179154dc98f6e455c604bed100c0beee06046',
      appVersion: 'test',
      characterId: 'character-1',
      profileVersion: 2,
      observationSchemaVersion: 1,
      attentionPolicyVersion: 1,
      rolloutStage: 'shadow',
      visionProvider: 'lmstudio',
      visionModelHash: 'hash',
      remoteProvider: false,
      startedAt,
    });

    accumulator.recordCapture();
    accumulator.recordMeaningfulChange();
    accumulator.recordVisionRequest({ latencyMs: 200, callsThisMinute: 4 });
    accumulator.recordVisionRequest({ latencyMs: 600, callsThisMinute: 5, stale: true });
    accumulator.recordObservation();
    accumulator.recordCommentary({ latencyMs: 80, duplicate: true, skipped: true });
    accumulator.recordCommentary({ latencyMs: 120 });
    accumulator.recordDelivery({ collision: true, interrupted: true });
    accumulator.addScenario('scene-change');
    accumulator.addScenario('not valid content!');

    const payload = accumulator.finalize(new Date('2026-07-14T12:01:00Z'));

    expect(payload.counts?.max_vision_calls_per_minute).toBe(5);
    expect(payload.latency_ms?.observation_p95).toBe(600);
    expect(payload.rates?.stale_output_rate).toBe(0.5);
    expect(payload.rates?.duplicate_comment_rate).toBe(0.5);
    expect(payload.rates?.collision_rate).toBe(1);
    expect(payload.scenario_labels).toEqual(['scene-change']);
    expect(JSON.stringify(payload)).not.toContain('image_data_url');
  });
});
