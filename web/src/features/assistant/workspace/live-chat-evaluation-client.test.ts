import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  liveChatEvaluationClient,
  suggestPresencePolicy,
  type PresencePolicyVersion,
  type VoiceSessionEvaluationRecord,
} from './live-chat-evaluation-client';

const active: PresencePolicyVersion = {
  preset: 'natural',
  version: 1,
  values: {
    silence_tolerance_ms: 15_000,
    initiative_threshold_ms: 18_000,
    initiative_cooldown_ms: 45_000,
    listener_backchannel_frequency: 0.16,
    typical_turn_words: 70,
    interruption_sensitivity: 0.70,
    response_onset_ms: 420,
  },
  reason: 'initial_server_policy',
  evidence_evaluation_ids: [],
  active: true,
  created_at: '2026-07-11T00:00:00+00:00',
};

function evaluation(index: number): VoiceSessionEvaluationRecord {
  return {
    evaluation_id: `evaluation-${index}`,
    call_id: `call-${index}`,
    session_id: 'chat:one',
    started_at: '2026-07-11T12:00:00+00:00',
    ended_at: '2026-07-11T12:10:00+00:00',
    exact_commit_sha: 'a'.repeat(40),
    app_version: '1.0.0',
    browser_version: 'Chrome 150',
    os_version: 'Windows 11',
    character_id: 'maya',
    profile_version: 4,
    presence_preset: 'natural',
    conversation_stance: 'discuss',
    configured_duplex_mode: 'automatic',
    resolved_duplex_mode: 'echo_aware',
    calibration_version: 'live-voice-calibration-v1',
    input_device_hash: 'input-hash',
    output_device_hash: 'output-hash',
    environment_hash: 'environment-hash',
    latency_summary: { first_audio_p95_ms: 700 },
    quality_metrics: {
      silence_fill_regret_rate: 0.2,
      backchannel_collision_rate: 0.08,
      perceived_pressure_score: 3.2,
      perceived_listening_score: 3.0,
    },
    eos_termination_counts: { natural_eos: 8 },
    scenario_labels: ['speakers-quiet'],
    release_gate_status: 'insufficient',
    listening_score: 3,
    pressure_score: 3.2,
    created_at: '2026-07-11T12:10:01+00:00',
    updated_at: '2026-07-11T12:10:01+00:00',
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('live chat evaluation client', () => {
  it('requests the durable aggregate release gate with status persistence', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'insufficient' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await liveChatEvaluationClient.releaseGate({ limit: 250, persistStatus: true });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tts/live-call/evaluations/release-gate?limit=250&persist_status=true',
      { method: 'GET' },
    );
  });
});

describe('suggestPresencePolicy', () => {
  it('makes bounded conservative changes from measured pressure and collisions', () => {
    const suggested = suggestPresencePolicy(active, Array.from({ length: 5 }, (_, index) => evaluation(index)));

    expect(suggested.silence_tolerance_ms).toBeGreaterThan(active.values.silence_tolerance_ms);
    expect(suggested.initiative_threshold_ms).toBeGreaterThan(active.values.initiative_threshold_ms);
    expect(suggested.initiative_cooldown_ms).toBeGreaterThan(active.values.initiative_cooldown_ms);
    expect(suggested.listener_backchannel_frequency).toBeLessThan(active.values.listener_backchannel_frequency);
    expect(suggested.typical_turn_words).toBeLessThan(active.values.typical_turn_words);
    expect(suggested.interruption_sensitivity).toBeGreaterThanOrEqual(active.values.interruption_sensitivity);
    expect(suggested.response_onset_ms).toBeGreaterThan(active.values.response_onset_ms);
  });

  it('does not change an unrelated preset from mismatched evidence', () => {
    const quietEvidence = { ...evaluation(1), presence_preset: 'quiet' as const };
    expect(suggestPresencePolicy(active, [quietEvidence])).toEqual(active.values);
  });
});
