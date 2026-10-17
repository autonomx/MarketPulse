import { describe, expect, it } from 'vitest';

import {
  abortDesktopCompanionObservationForPause,
  activityPayload,
  createDesktopCompanionTickScheduler,
  parseShadowWatchSettings,
  scenarioForActivity,
  scenarioForObservationOutcome,
  shouldRecordPausedAnalysisInterruption,
  shouldResumeDesktopCompanion,
} from './desktop-companion-watch-controller';

describe('desktop companion watch scheduling', () => {
  it('serializes overlapping ticks and reruns once for pending state changes', async () => {
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => { release = resolve; });
    let calls = 0;
    const schedule = createDesktopCompanionTickScheduler(async () => {
      calls += 1;
      await blocked;
    });

    const first = schedule();
    const second = schedule();
    const third = schedule();
    await Promise.resolve();

    expect(calls).toBe(1);
    expect(second).toBe(first);
    expect(third).toBe(first);

    release();
    await first;
    expect(calls).toBe(2);
    await schedule();
    expect(calls).toBe(3);
  });

  it('resumes a paused runtime even though Watch remains enabled', () => {
    expect(shouldResumeDesktopCompanion({ phase: 'paused', watchEnabled: true })).toBe(true);
    expect(shouldResumeDesktopCompanion({ phase: 'watching_idle', watchEnabled: true })).toBe(false);
    expect(shouldResumeDesktopCompanion({ phase: 'sharing', watchEnabled: false })).toBe(true);
  });

  it('records one fallback interruption when a completed request leaves the UI analyzing', () => {
    expect(shouldRecordPausedAnalysisInterruption({ phase: 'analyzing' }, false, false)).toBe(true);
    expect(shouldRecordPausedAnalysisInterruption({ phase: 'analyzing' }, true, false)).toBe(false);
    expect(shouldRecordPausedAnalysisInterruption({ phase: 'analyzing' }, false, true)).toBe(false);
    expect(shouldRecordPausedAnalysisInterruption({ phase: 'paused' }, false, false)).toBe(false);
  });

  it('aborts an active observation synchronously when Pause is requested', () => {
    const controller = new AbortController();

    expect(abortDesktopCompanionObservationForPause(true, controller)).toBe(true);
    expect(controller.signal.aborted).toBe(true);
    expect(controller.signal.reason).toBe('paused_by_user');
    expect(abortDesktopCompanionObservationForPause(true, controller)).toBe(false);
    expect(abortDesktopCompanionObservationForPause(false, new AbortController())).toBe(false);
    expect(abortDesktopCompanionObservationForPause(true, null)).toBe(false);
  });
});

describe('desktop companion watch settings', () => {
  it('parses the configured rollout request without treating it as effective', () => {
    const payload = {
      settings: {
        settings_control_center: {
          assistant: {
            desktopCompanionEnabled: true,
            desktopCompanionRolloutStage: 'text',
            desktopCompanionVisionModelId: 'qwen-vl',
            desktopCompanionRemoteVisionAllowed: true,
            desktopCompanionBackgroundCallsPerMinute: 8,
            desktopCompanionMinimumObservationIntervalMs: 9000,
            desktopCompanionObservationTimeoutMs: 11000,
            desktopCompanionObservationTtlMs: 13000,
            desktopCompanionCommentaryCooldownMs: 26000,
            desktopCompanionMinimumChangeConfidence: 0.61,
          },
        },
      },
    };

    expect(parseShadowWatchSettings(payload)).toEqual({
      enabled: true,
      requestedStage: 'text',
      visionModelId: 'qwen-vl',
      remoteVisionAllowed: true,
      backgroundCallsPerMinute: 8,
      minimumObservationIntervalMs: 9000,
      observationTimeoutMs: 11000,
      observationTtlMs: 13000,
      commentaryCooldownMs: 26000,
      minimumChangeConfidence: 0.61,
    });
  });

  it('keeps disabled settings off but allows text and speech to reach the backend gate', () => {
    const disabled = parseShadowWatchSettings({
      settings: { settings_control_center: { assistant: {
        desktopCompanionEnabled: true,
        desktopCompanionRolloutStage: 'disabled',
      } } },
    });
    expect(disabled).toMatchObject({ enabled: false, requestedStage: 'disabled' });

    for (const stage of ['shadow', 'text', 'speech'] as const) {
      const result = parseShadowWatchSettings({
        settings: { settings_control_center: { assistant: {
          desktopCompanionEnabled: true,
          desktopCompanionRolloutStage: stage,
        } } },
      });
      expect(result).toMatchObject({ enabled: true, requestedStage: stage });
      expect(result.remoteVisionAllowed).toBe(false);
    }
  });
});

describe('desktop companion activity request', () => {
  it('maps browser activity fields to the strict gateway contract', () => {
    expect(activityPayload({
      activity: 'full_scene_change',
      hypothesis: 'likely_app_switch',
      confidence: 0.9,
      changedRatio: 0.8,
      meanDifference: 0.7,
      horizontalShift: 1,
      verticalShift: 2,
      focus: 0.3,
      capturedAtMs: 1000,
    }, 1920, 1080)).toMatchObject({
      activity: 'full_scene_change',
      hypothesis: 'likely_app_switch',
      changed_ratio: 0.8,
      mean_difference: 0.7,
      source_width: 1920,
      source_height: 1080,
    });
  });

  it('maps activity to identifier-only evaluation scenarios', () => {
    const base = {
      confidence: 0.9,
      changedRatio: 0.8,
      meanDifference: 0.7,
      horizontalShift: 0,
      verticalShift: 0,
      focus: 0.3,
      capturedAtMs: 1000,
    };
    const behavior = {
      currentPattern: 'settled' as const,
      settledSeconds: 1,
      browsingPace: 0,
      rapidBrowsing: false,
      likelyTyping: false,
      likelyMedia: false,
      transition: null,
      sampleCount: 4,
    };
    expect(scenarioForActivity({ ...base, activity: 'static', hypothesis: 'none' }, behavior)).toBe('static-screen');
    expect(scenarioForActivity({ ...base, activity: 'full_scene_change', hypothesis: 'likely_app_switch' }, behavior)).toBe('scene-change');
    expect(scenarioForActivity(
      { ...base, activity: 'localized_change', hypothesis: 'likely_typing' },
      { ...behavior, likelyTyping: true },
    )).toBe('typing');
    expect(scenarioForActivity(
      { ...base, activity: 'localized_change', hypothesis: 'likely_navigation' },
      { ...behavior, rapidBrowsing: true },
    )).toBe('rapid-browsing');
  });

  it('maps genuine observation outcomes to content-free qualification scenarios', () => {
    expect(scenarioForObservationOutcome('scene-change', 'screen-prompt-injection', false))
      .toBe('screen-prompt-injection');
    expect(scenarioForObservationOutcome('scene-change', null, true)).toBe('interruption');
    expect(scenarioForObservationOutcome('scene-change', null, false)).toBe('scene-change');
  });
});
