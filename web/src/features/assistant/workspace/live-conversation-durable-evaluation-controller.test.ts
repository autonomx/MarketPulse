import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const report = {
  eventCount: 12,
  firstAudioLatencyMs: { average: 510, p95: 720 },
  falseEndpointRate: 0,
  talkOverDurationMs: 80,
  interruptionSuccessRate: 1,
  cancellationLatencyMs: { average: 120, p95: 180 },
  silenceFillRegretRate: 0.1,
  proactiveAcceptanceRate: 0.8,
  backchannelCollisionRate: 0,
  questionDensity: 0.3,
  assistantUserSpeakingRatio: 0.8,
  turnDurationMs: { median: 1_500, p95: 4_000 },
  repairSuccessRate: 1,
  repeatedTopicRate: 0,
  unansweredObligationRate: 0,
  perceivedListeningScore: 5,
  perceivedPressureScore: 1,
};

vi.mock('./live-conversation-evaluation-controller', () => ({
  readLiveConversationEvaluationSnapshot: () => ({ events: [], report }),
}));

import {
  buildDurableEvaluationPayload,
  initializeLiveConversationDurableEvaluationController,
} from './live-conversation-durable-evaluation-controller';
import { liveConversationStore } from './live-conversation-store';

const profile = {
  presence_preset: 'natural' as const,
  talkativeness: 50,
  conversation_stance: 'discuss' as const,
  conversation_pace: 'balanced' as const,
  interruption_preference: 'balanced' as const,
  assistant_backchannel_mode: 'natural' as const,
  initiative_mode: 'gentle' as const,
  idle_threshold_ms: 15_000,
  long_pause_behavior: 'wait' as const,
  response_length: 'conversational' as const,
  response_onset_style: 'adaptive' as const,
  emotional_attunement: 'subtle' as const,
  topic_continuity: 'natural' as const,
  max_idle_prompts: 1,
  duplex_mode: 'automatic' as const,
  pronunciation_save_policy: 'ask' as const,
  profile_version: 2,
};

function activeCall() {
  return {
    callId: 'call-one',
    startedAt: '2026-07-11T12:00:00+00:00',
    eosTerminationCounts: { natural_eos: 6, forced_eos: 1, token_limit: 0, sequence_limit: 0, model_stopped: 0 },
    releaseLatencies: {
      stt_finalize_ms: [400, 500],
      final_to_first_token_ms: [800, 900],
      first_token_to_first_audio_ms: [300, 350],
      final_to_first_audio_ms: [1_100, 1_250],
      stt_request_to_first_audio_ms: [1_500, 1_750],
      stt_request_to_first_playback_ms: [1_700, 1_950],
      interruption_to_silence_ms: [120, 150],
    },
    releaseQuality: {
      false_interruption: [false, false],
      missed_interruption: [false, true],
      backchannel_false_positive: [false],
      playback_echo_submission: [false, false],
    },
    duckToCancelMs: [100, 140],
    rejectedCandidateRestoreMs: [90, 110],
  };
}

describe('durable Live Conversation evaluation controller', () => {
  beforeEach(() => {
    window.localStorage.clear();
    liveConversationStore.reset();
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:maya' });
    liveConversationStore.dispatch({ type: 'identity', identity: { characterId: 'maya', displayName: 'Maya', profileVersion: 4 } });
    liveConversationStore.dispatch({ type: 'profile', profile });
    liveConversationStore.dispatch({
      type: 'duplex',
      duplex: {
        configuredMode: 'automatic',
        resolvedMode: 'echo_aware',
        reason: 'calibration_confident',
        confidence: 0.91,
        calibration: {
          version: 'live-voice-calibration-v1',
          deviceKey: 'device-pair-hash',
          createdAt: 1_000,
          expiresAt: Date.now() + 60_000,
          noiseFloorRms: 0.002,
          playbackRms: 0.04,
          echoGain: 0.2,
          delayMs: 42,
          similarity: 0.9,
          userSpeechSeparation: 2.4,
          confidence: 0.91,
          resolvedMode: 'echo_aware',
          reason: 'calibration_confident',
        },
      },
    });
    document.documentElement.dataset.commitSha = 'a'.repeat(40);
    document.documentElement.dataset.appVersion = '1.2.3';
    window.localStorage.setItem('omnix.liveCall.releaseScenario', 'speakers-quiet,immediate-hard-stop');
  });

  afterEach(() => {
    delete document.documentElement.dataset.commitSha;
    delete document.documentElement.dataset.appVersion;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('builds aggregate-only evidence from the authoritative store', () => {
    const payload = buildDurableEvaluationPayload(activeCall(), '2026-07-11T12:10:00+00:00');

    expect(payload).toMatchObject({
      call_id: 'call-one',
      session_id: 'chat:maya',
      character_id: 'maya',
      presence_preset: 'natural',
      resolved_duplex_mode: 'echo_aware',
      listening_score: 5,
      pressure_score: 1,
      eos_termination_counts: { natural_eos: 6, forced_eos: 1 },
      scenario_labels: ['speakers-quiet', 'immediate-hard-stop'],
      latency_summary: {
        stt_finalize_p95_ms: 500,
        final_to_first_token_p95_ms: 900,
        first_token_to_first_audio_p95_ms: 350,
        final_to_first_audio_p95_ms: 1_250,
        stt_request_to_first_audio_p95_ms: 1_750,
        stt_request_to_first_playback_p95_ms: 1_950,
        interruption_to_silence_p95_ms: 150,
        cancellation_p95_ms: 140,
        rejected_candidate_restore_p95_ms: 110,
      },
      quality_metrics: {
        false_barge_in_rate: 0,
        missed_barge_in_rate: 0.5,
        playback_echo_submission_rate: 0,
      },
    });
    expect(payload.browser_version?.length ?? 0).toBeGreaterThan(0);
    expect(payload.os_version?.length ?? 0).toBeGreaterThan(0);
    const serialized = JSON.stringify(payload).toLocaleLowerCase();
    expect(serialized).not.toMatch(/transcript|prompt|memory|message_content|utterance_text/);
  });

  it('normalizes transient runtime metadata before posting evidence', () => {
    liveConversationStore.dispatch({
      type: 'identity',
      identity: { profileVersion: 0 },
    });
    liveConversationStore.dispatch({
      type: 'profile',
      profile: {
        ...profile,
        presence_preset: 'legacy' as unknown as typeof profile.presence_preset,
        conversation_stance: 'legacy' as unknown as typeof profile.conversation_stance,
        duplex_mode: 'legacy' as unknown as typeof profile.duplex_mode,
      },
    });
    liveConversationStore.dispatch({
      type: 'duplex',
      duplex: { resolvedMode: 'legacy' as unknown as 'half_duplex' },
    });
    document.documentElement.dataset.commitSha = 'dev';

    const payload = buildDurableEvaluationPayload(activeCall(), '2026-07-11T12:10:00+00:00');

    expect(payload.profile_version).toBeNull();
    expect(payload.presence_preset).toBe('natural');
    expect(payload.conversation_stance).toBe('automatic');
    expect(payload.configured_duplex_mode).toBe('automatic');
    expect(payload.resolved_duplex_mode).toBe('half_duplex');
    expect(payload.exact_commit_sha).toBe('unknown0');
  });

  it('posts one record, evaluates the durable aggregate, and counts release observations', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/release-gate')) {
        return Response.json({
          status: 'insufficient', generated_at: 'now', records_scanned: 1, traces: 1,
          scenarios: [], missing_scenarios: [], character_modes: ['character'], metrics: [],
          failures: [], insufficient: ['more evidence required'],
        });
      }
      return Response.json({ ...JSON.parse(String(init?.body)), evaluation_id: 'evaluation-one' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const dispose = initializeLiveConversationDurableEvaluationController();

    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-start'));
    window.dispatchEvent(new CustomEvent('omnix:live-voice-release-observation', {
      detail: { kind: 'latency', metricName: 'stt_finalize_ms', valueMs: 430, scenario: 'speakers-quiet' },
    }));
    window.dispatchEvent(new CustomEvent('omnix:live-voice-release-observation', {
      detail: { kind: 'latency', metricName: 'stt_request_to_first_playback_ms', valueMs: 1_780, scenario: 'speakers-quiet' },
    }));
    window.dispatchEvent(new CustomEvent('omnix:live-voice-release-observation', {
      detail: { kind: 'quality', qualityName: 'playback_echo_submission', occurred: false, scenario: 'speakers-quiet' },
    }));
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: { provider_timing: { termination_reason: 'natural_eos' } },
    }));
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: { stage: 'barge_in_confirmed', duck_to_cancel_ms: 125 },
    }));
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-stop'));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [, init] = fetchMock.mock.calls.find(([input]) => !String(input).includes('/release-gate')) ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body.eos_termination_counts.natural_eos).toBe(1);
    expect(body.latency_summary.stt_finalize_p95_ms).toBe(430);
    expect(body.latency_summary.stt_request_to_first_playback_p95_ms).toBe(1_780);
    expect(body.latency_summary.cancellation_p95_ms).toBe(125);
    expect(body.quality_metrics.playback_echo_submission_rate).toBe(0);
    expect(body.release_gate_status).toBe('insufficient');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tts/live-call/evaluations/release-gate?limit=1000&persist_status=true',
      { method: 'GET' },
    );
    dispose();
  });
});
