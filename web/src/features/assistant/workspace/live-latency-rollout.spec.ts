import { afterEach, describe, expect, it, vi } from 'vitest';
import { resolveAuthoritySelection } from './live-stt-authority-controller';
import {
  resolveLiveVoiceSttSelection,
  shouldCommitProviderEndpoint,
} from './live-voice-controller';
import {
  LIVE_SPECULATION_HANDSHAKE_GRACE_MS,
  speculationHandshakeWaitBudgetMs,
  transcriptIsSpeculationSafe,
  transcriptsCanReuseSpeculation,
} from './live-speculation-controller';
import { parseLiveVoiceCriticalDirtyFiles } from './live-runtime-provenance';
import { AdaptiveTtsBufferPolicy } from './live-tts-adaptive-buffer-controller';
import { assessAcousticBargeIn } from './live-voice-barge-in-detector';
import { StableClauseAccumulator } from './live-voice-clause-stabilizer';
import {
  clearPlaybackEchoSuppression,
  markPlaybackEchoSuppressed,
} from './live-voice-echo-suppression';
import {
  classifyOverlap,
  shouldConfirmInterruption,
} from './live-voice-overlap-classifier';
import { compareRecentWaveforms } from './live-voice-waveform-reference';

const locationLike = { protocol: 'http:', hostname: 'localhost' } as Pick<Location, 'protocol' | 'hostname'>;

afterEach(() => clearPlaybackEchoSuppression());

function deterministicNoise(length: number, seed: number): Float32Array {
  let state = seed >>> 0;
  return Float32Array.from({ length }, () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
    return (state / 0xffff_ffff * 2 - 1) * 0.35;
  });
}

describe('live latency PR3-PR5 rollout policies', () => {
  it('keeps STT authority and WebSocket routes on the gateway origin and port', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true, eligible: true }))) as unknown as typeof fetch;
    const selected = await resolveAuthoritySelection('/api/stt?authority=auto&language=en',
      { protocol: 'http:', hostname: 'localhost', host: 'localhost:5173' }, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:5173/api/stt/authorityz?language=en&mode=auto', expect.any(Object));
    expect(selected.websocketUrl).toBe('ws://localhost:5173/api/stt/ws/transcribe?language=en');
    expect(selected.authorityEnabled).toBe(true);
  });

  it('does not enable authority from an error status with an eligible body', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true, eligible: true }), { status: 503 })) as unknown as typeof fetch;
    await expect(resolveAuthoritySelection('/api/stt?authority=auto', locationLike, fetchImpl)).rejects.toThrow('authority_http_503');
  });

  it('selects authoritative Kyutai only after the pre-session gate passes', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      eligible: true,
      reasons: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch;

    const selected = await resolveAuthoritySelection(
      'http://127.0.0.1:5202?language=en&authority=test&endpoint_threshold=0.82',
      locationLike,
      fetchImpl,
    );
    expect(selected.authorityEnabled).toBe(true);
    expect(selected.fallbackUsed).toBe(false);
    expect(selected.endpointThreshold).toBe(0.82);
    expect(selected.websocketUrl).toBe('ws://127.0.0.1:5202/ws/transcribe?language=en');
  });

  it('falls back before capture when production authority gates fail', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      ok: false,
      eligible: false,
      reasons: ['quality_gate_not_passed'],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch;

    const selected = await resolveAuthoritySelection(
      'http://127.0.0.1:5202?language=en&authority=auto&fallback=http%3A%2F%2F127.0.0.1%3A5201',
      locationLike,
      fetchImpl,
    );
    expect(selected.authorityEnabled).toBe(false);
    expect(selected.fallbackUsed).toBe(true);
    expect(selected.websocketUrl).toBe('ws://127.0.0.1:5201/ws/transcribe');
    expect(selected.reasons).toContain('quality_gate_not_passed');
  });

  it('uses the controller authority resolver before capture and defaults to Parakeet', async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const selected = await resolveLiveVoiceSttSelection(undefined, locationLike, fetchImpl);

    expect(selected.authorityEnabled).toBe(false);
    expect(selected.fallbackUsed).toBe(false);
    expect(selected.reasons).toEqual(['default_parakeet']);
    expect(selected.websocketUrl).toBe('ws://localhost/api/stt/ws/transcribe');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('allows provider endpoint commitment only after fused confidence, silence, and stability gates', () => {
    const ready = {
      authorityEnabled: true,
      probability: 0.86,
      endpointThreshold: 0.75,
      speechDetected: true,
      finalRequested: false,
      pausePending: true,
      pauseElapsedMs: 180,
      transcriptStableMs: 100,
      semanticProbabilityDone: 0.94,
      transcriptWords: 4,
      correctionPending: false,
    };
    expect(shouldCommitProviderEndpoint(ready)).toBe(true);
    expect(shouldCommitProviderEndpoint({ ...ready, authorityEnabled: false })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, probability: 0.7 })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, pausePending: false })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, pauseElapsedMs: 80 })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, transcriptStableMs: 30 })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, finalRequested: true })).toBe(false);
    expect(shouldCommitProviderEndpoint({ ...ready, correctionPending: true })).toBe(false);
  });

  it('does not commit semantically ambiguous statements from provider confidence alone', () => {
    const ambiguous = {
      authorityEnabled: true,
      probability: 0.9,
      endpointThreshold: 0.75,
      speechDetected: true,
      finalRequested: false,
      pausePending: true,
      pauseElapsedMs: 180,
      transcriptStableMs: 100,
      semanticProbabilityDone: 0.78,
      transcriptWords: 5,
      correctionPending: false,
    };
    expect(shouldCommitProviderEndpoint(ambiguous)).toBe(false);
    expect(shouldCommitProviderEndpoint({
      ...ambiguous,
      pauseElapsedMs: 280,
    })).toBe(false);
  });

  it('reports only normalized live-voice critical dirty paths from build provenance', () => {
    expect(parseLiveVoiceCriticalDirtyFiles(JSON.stringify([
      'src/app/composition/gateway/live_voice_speculative_tts.py',
      ' src/app/composition/gateway/live_voice_execution_lane.py ',
      'src/app/composition/gateway/live_voice_speculative_tts.py',
      42,
    ]))).toEqual([
      'src/app/composition/gateway/live_voice_speculative_tts.py',
      'src/app/composition/gateway/live_voice_execution_lane.py',
    ]);
    expect(parseLiveVoiceCriticalDirtyFiles('not-json')).toEqual([]);
    expect(parseLiveVoiceCriticalDirtyFiles(undefined)).toEqual([]);
  });

  it('reuses speculation only when normalized words are unchanged', () => {
    expect(transcriptsCanReuseSpeculation('Tell me a story', 'tell me a story!')).toBe(true);
    expect(transcriptsCanReuseSpeculation('Tell me a story', 'Tell me the story')).toBe(false);
    expect(transcriptIsSpeculationSafe('Wait, no I mean tell me a story')).toBe(false);
  });

  it('only spends a small handshake grace when the speculative response is already open', () => {
    expect(LIVE_SPECULATION_HANDSHAKE_GRACE_MS).toBeGreaterThanOrEqual(20);
    expect(LIVE_SPECULATION_HANDSHAKE_GRACE_MS).toBeLessThanOrEqual(75);
    expect(speculationHandshakeWaitBudgetMs({
      generationReady: false,
      bufferedChunks: 0,
      responseReady: true,
      completed: false,
      error: false,
    })).toBe(LIVE_SPECULATION_HANDSHAKE_GRACE_MS);
  });

  it('does not delay fallback or already-usable speculation', () => {
    const pending = {
      generationReady: false,
      bufferedChunks: 0,
      responseReady: false,
      completed: false,
      error: false,
    };
    expect(speculationHandshakeWaitBudgetMs(pending)).toBe(0);
    expect(speculationHandshakeWaitBudgetMs({
      ...pending,
      generationReady: true,
      responseReady: true,
    })).toBe(0);
    expect(speculationHandshakeWaitBudgetMs({
      ...pending,
      bufferedChunks: 1,
      responseReady: true,
    })).toBe(0);
    expect(speculationHandshakeWaitBudgetMs({
      ...pending,
      completed: true,
      responseReady: true,
    })).toBe(0);
    expect(speculationHandshakeWaitBudgetMs({
      ...pending,
      error: true,
      responseReady: true,
    })).toBe(0);
  });

  it('commits streamed LLM text to TTS after the low-latency deadline', () => {
    const clauses = new StableClauseAccumulator();
    expect(clauses.append('This response starts early', 1_000)).toEqual([]);
    expect(clauses.takeReady(1_141)).toEqual([
      { text: 'This response starts', reason: 'deadline' },
    ]);
    expect(clauses.pendingText()).toBe('early');
  });

  it('raises buffering after underruns and cautiously lowers it after stable turns', () => {
    const policy = new AdaptiveTtsBufferPolicy({ startBufferMs: 260, rebufferMs: 520 });
    const raised = policy.observeWorkletEvent('underrun');
    expect(raised.startBufferMs).toBeGreaterThan(260);
    expect(raised.rebufferMs).toBeGreaterThan(520);
    policy.observeWorkletEvent('drained');

    const beforeStable = policy.snapshot();
    policy.observeWorkletEvent('drained');
    policy.observeWorkletEvent('drained');
    const lowered = policy.observeWorkletEvent('drained');
    expect(lowered.startBufferMs).toBeLessThan(beforeStable.startBufferMs);
    expect(lowered.rebufferMs).toBeLessThan(beforeStable.rebufferMs);
  });

  it('rejects delayed self playback while preserving simultaneous user speech', () => {
    const playback = deterministicNoise(30_000, 17);
    const source = playback.slice(10_800, 12_848);
    const pureEcho = Float32Array.from(source, (sample) => sample * 0.5);
    const user = deterministicNoise(source.length, 91_337);
    const mixed = Float32Array.from(
      source,
      (sample, index) => sample * 0.5 + user[index] * 0.55,
    );

    const echoMatch = compareRecentWaveforms(playback, pureEcho, 24_000);
    const mixedMatch = compareRecentWaveforms(playback, mixed, 24_000);
    expect(echoMatch.lagMs ?? 0).toBeGreaterThan(650);
    expect(echoMatch.residualRatio ?? 1).toBeLessThan(0.05);
    expect(mixedMatch.residualRatio ?? 0).toBeGreaterThan(0.6);

    const echo = assessAcousticBargeIn({
      assistantSpeaking: true,
      microphoneRms: echoMatch.alignedMicrophoneRms ?? 0,
      playbackRms: echoMatch.alignedPlaybackRms ?? 0,
      playbackReferenceAgeMs: 900,
      speechThreshold: 0.01,
      waveformSimilarity: echoMatch.similarity,
      residualSpeechRatio: echoMatch.residualRatio,
      estimatedEchoGain: echoMatch.estimatedEchoGain,
    });
    const bargeIn = assessAcousticBargeIn({
      assistantSpeaking: true,
      microphoneRms: mixedMatch.alignedMicrophoneRms ?? 0,
      playbackRms: mixedMatch.alignedPlaybackRms ?? 0,
      playbackReferenceAgeMs: 900,
      speechThreshold: 0.01,
      waveformSimilarity: mixedMatch.similarity,
      residualSpeechRatio: mixedMatch.residualRatio,
      estimatedEchoGain: mixedMatch.estimatedEchoGain,
    });
    expect(echo.decision).toBe('likely_echo');
    expect(bargeIn.decision).toBe('independent_speech');
  });

  it('lets an acoustic echo verdict veto partial-STT interruption until real user speech clears it', () => {
    const interruption = classifyOverlap('I need to change the destination now');
    markPlaybackEchoSuppressed('echo_residual_matches_playback');
    expect(shouldConfirmInterruption(interruption, 'easy')).toBe(false);

    clearPlaybackEchoSuppression();
    expect(shouldConfirmInterruption(interruption, 'easy')).toBe(true);
  });
});
