import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  record: vi.fn(),
}));

vi.mock('./live-call-diagnostics-client', () => ({
  createLiveCallDiagnosticsReporter: () => ({
    traceId: 'live-call:release-observer',
    record: mocks.record,
    flush: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  }),
}));

import { initializeLiveVoiceReleaseObserver, resetLiveVoiceReleaseObserver } from './live-voice-release-observer';
import { type LiveVoiceTurnTimelineDetail } from './live-voice-turn-coordinator';
import { LIVE_VOICE_TURN_TIMELINE_EVENT } from '../../../events/bus';

function timeline(detail: LiveVoiceTurnTimelineDetail): void {
  window.dispatchEvent(new CustomEvent(LIVE_VOICE_TURN_TIMELINE_EVENT, { detail }));
}

function perf(stage: string, turnId: string, extra = {}): void {
  window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
    detail: { stage, turnId, ...extra },
  }));
}

function diagnostic(
  event: string,
  traceId: string,
  source = 'controller',
  details: Record<string, unknown> = {},
): void {
  window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
    detail: { traceId, source, event, details },
  }));
}

describe('speech-end release metric', () => {
  let now = 0;

  beforeEach(() => {
    initializeLiveVoiceReleaseObserver();
    mocks.record.mockReset();
    resetLiveVoiceReleaseObserver();
    vi.spyOn(performance, 'now').mockImplementation(() => now);
  });

  it('uses the final pause after pause/resume cycles and measures first audible playback', () => {
    timeline({
      turnId: 'voice-turn:metric',
      event: 'speech_ended',
      atMs: 100,
      state: 'endpoint_candidate',
    });
    timeline({
      turnId: 'voice-turn:metric',
      event: 'speech_ended',
      atMs: 220,
      state: 'endpoint_candidate',
    });
    now = 240;
    perf('stt_final_requested', 'voice-turn:metric');
    now = 300;
    perf('stt_final_received', 'voice-turn:metric', { sttFinalizeMs: 60 });
    diagnostic('turn_intercepted', 'live-call:voice-turn:metric');
    now = 400;
    diagnostic('llm_text_chunk_received', 'live-call:voice-turn:metric');
    now = 480;
    diagnostic(
      'phrase_first_frame_received',
      'live-call:audio:metric',
      'pcm_session',
      { output_id: 'output-metric', segment_id: 'segment-metric', segment_kind: 'speech' },
    );
    now = 520;
    diagnostic(
      'worklet_segment_started',
      'live-call:audio:metric',
      'audio_worklet',
      { output_id: 'output-metric', segment_id: 'segment-metric', segment_kind: 'speech' },
    );

    expect(mocks.record).toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({
        metric_name: 'speech_end_to_first_playback_ms',
        value_ms: 300,
        turn_id: 'voice-turn:metric',
      }),
      'release_observer',
    );
  });
});
