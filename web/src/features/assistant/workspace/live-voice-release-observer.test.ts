import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  record: vi.fn(),
  flush: vi.fn(async () => undefined),
  close: vi.fn(async () => undefined),
}));

vi.mock('./live-call-diagnostics-client', () => ({
  createLiveCallDiagnosticsReporter: () => ({
    traceId: 'live-call:release-observer',
    record: mocks.record,
    flush: mocks.flush,
    close: mocks.close,
  }),
}));

import { initializeLiveVoiceReleaseObserver, recordLiveVoiceReleaseQuality, resetLiveVoiceReleaseObserver, type LiveVoiceReleaseObservation } from './live-voice-release-observer';
import { LIVE_VOICE_RELEASE_OBSERVATION_EVENT } from '../../../events/bus';

function perf(stage: string, turnId = 'voice-turn:1', extra = {}): void {
  window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
    detail: { stage, turnId, ...extra },
  }));
}

function diagnostic(
  event: string,
  traceId = 'live-call:voice-turn:1',
  source = 'controller',
  details: Record<string, unknown> = {},
): void {
  window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
    detail: { traceId, source, event, details },
  }));
}

describe('live voice release observer', () => {
  let now = 0;

  beforeEach(() => {
    initializeLiveVoiceReleaseObserver();
    mocks.record.mockReset();
    resetLiveVoiceReleaseObserver();
    window.localStorage.clear();
    vi.spyOn(performance, 'now').mockImplementation(() => now);
  });

  it('separates stream-open, model, PCM, playback, and endpoint latency', () => {
    const observations: LiveVoiceReleaseObservation[] = [];
    const listener = (event: Event) => observations.push(
      (event as CustomEvent<LiveVoiceReleaseObservation>).detail,
    );
    window.addEventListener(LIVE_VOICE_RELEASE_OBSERVATION_EVENT, listener);

    now = 100;
    perf('stt_final_requested');
    now = 300;
    perf('stt_final_received', 'voice-turn:1', { sttFinalizeMs: 200 });
    diagnostic('turn_intercepted');
    now = 500;
    diagnostic('chat_response_opened', 'live-call:voice-turn:1', 'chatbot_workspace');
    now = 800;
    diagnostic('llm_text_chunk_received');
    now = 1_000;
    diagnostic(
      'phrase_first_frame_received',
      'live-call:chat:s1:audio-session:a1',
      'pcm_session',
      { output_id: 'output-one', segment_id: 'speech-one', segment_kind: 'speech' },
    );
    now = 1_050;
    diagnostic(
      'worklet_segment_started',
      'live-call:chat:s1:audio-session:a1',
      'audio_worklet',
      { output_id: 'other-output', segment_id: 'silence-one', segment_kind: 'silence' },
    );
    now = 1_100;
    diagnostic(
      'worklet_segment_started',
      'live-call:chat:s1:audio-session:a1',
      'audio_worklet',
      { output_id: 'output-one', segment_id: 'speech-one', segment_kind: 'speech' },
    );

    const latency = observations.filter(
      (item): item is Extract<LiveVoiceReleaseObservation, { kind: 'latency' }> => (
        item.kind === 'latency'
      ),
    );
    const values = Object.fromEntries(
      latency.map((item) => [item.metricName, item.valueMs]),
    );
    expect(values).toMatchObject({
      stt_finalize_ms: 200,
      final_to_response_open_ms: 200,
      response_open_to_first_token_ms: 300,
      final_to_first_token_ms: 500,
      first_token_to_first_audio_ms: 200,
      final_to_first_audio_ms: 700,
      stt_request_to_first_audio_ms: 900,
      first_pcm_to_first_playback_ms: 100,
      first_token_to_first_playback_ms: 300,
      final_to_first_playback_ms: 800,
      stt_request_to_first_playback_ms: 1_000,
    });
    expect(values).not.toHaveProperty('local_pause_to_first_playback_ms');
    window.removeEventListener(LIVE_VOICE_RELEASE_OBSERVATION_EVENT, listener);
  });

  it('uses audio output time instead of delayed worklet message delivery', () => {
    now = 100;
    perf('stt_final_requested');
    now = 300;
    perf('stt_final_received', 'voice-turn:1', { sttFinalizeMs: 200 });
    diagnostic('turn_intercepted');
    now = 800;
    diagnostic('llm_text_chunk_received');
    now = 1_000;
    diagnostic(
      'phrase_first_frame_received',
      'live-call:chat:s1:audio-session:a1',
      'pcm_session',
      { output_id: 'output-one', segment_id: 'speech-one', segment_kind: 'speech' },
    );
    now = 1_400;
    diagnostic(
      'worklet_segment_started',
      'live-call:chat:s1:audio-session:a1',
      'audio_worklet',
      {
        output_id: 'output-one',
        segment_id: 'speech-one',
        segment_kind: 'speech',
        playback_performance_time_ms: 1_040,
      },
    );

    expect(mocks.record).toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({
        metric_name: 'first_pcm_to_first_playback_ms',
        value_ms: 40,
      }),
      'release_observer',
    );
    expect(mocks.record).toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({
        metric_name: 'final_to_first_playback_ms',
        value_ms: 740,
      }),
      'release_observer',
    );
  });

  it('records authoritative STT provider quality metrics', () => {
    perf('stt_provider_final', 'voice-turn:quality', {
      provider: 'nemotron_parakeet_eou',
      segmentId: 'segment-quality',
      sourceSequence: 3,
      transcriptChars: 25,
      providerMetrics: {
        authoritative_full_decode: 1,
        full_decode_ms: 241.5,
        streaming_chars: 13,
        authoritative_chars: 25,
        authoritative_changed: 1,
        final_right_context: 13,
      },
    });

    expect(mocks.record).toHaveBeenCalledWith('stt_provider_final', {
      provider: 'nemotron_parakeet_eou',
      segment_id: 'segment-quality',
      source_sequence: 3,
      transcript_chars: 25,
      authoritative_full_decode: 1,
      full_decode_ms: 241.5,
      streaming_chars: 13,
      authoritative_chars: 25,
      authoritative_changed: 1,
      final_right_context: 13,
    }, 'release_observer');
  });

  it('rejects unrelated cross-trace playback diagnostics', () => {
    now = 100;
    perf('stt_final_received', 'voice-turn:2', { sttFinalizeMs: 100 });
    diagnostic('turn_intercepted', 'live-call:voice-turn:2');
    now = 200;
    diagnostic('llm_text_chunk_received', 'live-call:voice-turn:2');
    now = 250;
    diagnostic(
      'phrase_first_frame_received',
      'live-call:audio-session:two',
      'pcm_session',
      { output_id: 'expected-output', segment_id: 'expected-segment', segment_kind: 'speech' },
    );
    now = 300;
    diagnostic(
      'worklet_segment_started',
      'live-call:unrelated',
      'audio_worklet',
      { output_id: 'wrong-output', segment_id: 'wrong-segment', segment_kind: 'speech' },
    );

    expect(mocks.record).not.toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({ metric_name: 'final_to_first_playback_ms' }),
      'release_observer',
    );
  });

  it('does not treat residual playback as the new turn before its first PCM arrives', () => {
    now = 100;
    perf('stt_final_requested', 'voice-turn:fresh');
    now = 150;
    perf('stt_final_received', 'voice-turn:fresh', { sttFinalizeMs: 50 });
    diagnostic('turn_intercepted', 'live-call:voice-turn:fresh');
    now = 200;
    diagnostic('llm_text_chunk_received', 'live-call:voice-turn:fresh');
    now = 220;
    diagnostic(
      'worklet_segment_started',
      'live-call:audio-session:old',
      'audio_worklet',
      { output_id: 'old-output', segment_id: 'old-segment', segment_kind: 'speech' },
    );

    expect(mocks.record).not.toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({ metric_name: 'final_to_first_playback_ms' }),
      'release_observer',
    );

    now = 250;
    diagnostic(
      'phrase_first_frame_received',
      'live-call:audio-session:fresh',
      'pcm_session',
      { output_id: 'fresh-output', segment_id: 'fresh-segment', segment_kind: 'speech' },
    );
    now = 270;
    diagnostic(
      'worklet_segment_started',
      'live-call:audio-session:fresh',
      'audio_worklet',
      { output_id: 'fresh-output', segment_id: 'fresh-segment', segment_kind: 'speech' },
    );

    expect(mocks.record).toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({
        metric_name: 'final_to_first_playback_ms',
        value_ms: 120,
        turn_id: 'voice-turn:fresh',
      }),
      'release_observer',
    );
  });

  it('drops empty STT finals from release latency accounting', () => {
    now = 100;
    perf('stt_final_requested', 'voice-turn:empty');
    now = 160;
    perf('stt_final_received', 'voice-turn:empty', {
      sttFinalizeMs: 60,
      inputChars: 0,
    });

    expect(mocks.record).toHaveBeenCalledWith('release_metric_skipped', {
      metric_name: 'turn_latency',
      reason: 'empty_final_transcript',
      incoming_turn_id: 'voice-turn:empty',
    }, 'release_observer');
    expect(mocks.record).not.toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({ metric_name: 'stt_finalize_ms' }),
      'release_observer',
    );
  });

  it('rejects response diagnostics relabelled as a newer rapid turn', () => {
    now = 100;
    perf('stt_final_requested', 'voice-turn:old');
    now = 170;
    perf('stt_final_received', 'voice-turn:old', { sttFinalizeMs: 70 });
    diagnostic('turn_intercepted', 'live-call:voice-turn:old');

    mocks.record.mockClear();
    now = 300;
    diagnostic(
      'chat_response_opened',
      'live-call:voice-turn:new',
      'chatbot_workspace',
    );

    expect(mocks.record).not.toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({ metric_name: 'final_to_response_open_ms' }),
      'release_observer',
    );

    now = 320;
    diagnostic(
      'chat_response_opened',
      'live-call:voice-turn:old',
      'chatbot_workspace',
    );
    expect(mocks.record).toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({
        metric_name: 'final_to_response_open_ms',
        value_ms: 150,
        turn_id: 'voice-turn:old',
      }),
      'release_observer',
    );
  });

  it('does not charge an unmatched late STT final to the previous turn', () => {
    now = 100;
    perf('stt_final_requested', 'voice-turn:one');
    now = 165;
    perf('stt_final_received', 'voice-turn:one', { sttFinalizeMs: 65 });
    diagnostic('turn_intercepted', 'live-call:voice-turn:one');
    now = 500;
    diagnostic('turn_finished', 'live-call:voice-turn:one');

    mocks.record.mockClear();
    now = 24_000;
    perf('stt_final_received', 'voice-turn:two');

    expect(mocks.record).not.toHaveBeenCalledWith(
      'release_metric',
      expect.objectContaining({ metric_name: 'stt_finalize_ms' }),
      'release_observer',
    );
    expect(mocks.record).toHaveBeenCalledWith('release_metric_skipped', {
      metric_name: 'stt_finalize_ms',
      reason: 'missing_matching_stt_final_requested',
      incoming_turn_id: 'voice-turn:two',
      requested_turn_id: null,
    }, 'release_observer');
  });

  it('uses an explicit finalize duration for a new turn without stale fallback time', () => {
    now = 100;
    perf('stt_final_requested', 'voice-turn:one');
    now = 10_000;
    perf('stt_final_received', 'voice-turn:two', { sttFinalizeMs: 72 });

    expect(mocks.record).toHaveBeenCalledWith('release_metric', expect.objectContaining({
      metric_name: 'stt_finalize_ms',
      value_ms: 72,
      turn_id: 'voice-turn:two',
    }), 'release_observer');
  });

  it('records manually labelled quality trials', () => {
    recordLiveVoiceReleaseQuality('false_interruption', false, 'system-noise');
    expect(mocks.record).toHaveBeenCalledWith('release_quality', {
      quality_name: 'false_interruption',
      occurred: false,
      scenario: 'system-noise',
    }, 'release_observer');
  });
});
