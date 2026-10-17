/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const session = {
    sampleRate: 48_000,
    enqueuePhrase: vi.fn(async () => undefined),
    enqueueOutputPhrase: vi.fn(async (
      _text: string,
      _phraseIndex: number,
      _ownership: { outputId: string; generationEpoch: number; outputOrder: number },
      _options?: { performancePlan?: Record<string, unknown> },
    ) => undefined),
    enqueueSilence: vi.fn(async (
      _durationMs: number,
      _reason: string,
      _minimumFollowingSpeechMs?: number,
    ) => undefined),
    enqueueCue: vi.fn(async (_cueId: string, _variantId: string, _gain?: number) => undefined),
    cancelSegment: vi.fn(),
    cancelOutputItem: vi.fn(async () => undefined),
    cancelAllAfter: vi.fn(),
    waitForOutputItem: vi.fn(async () => undefined),
    setStartPolicy: vi.fn((_policy: Record<string, number>) => undefined),
    finish: vi.fn(async () => undefined),
    stop: vi.fn(async (_reason?: string) => undefined),
    isClosed: vi.fn(() => false),
  };
  const recordSpy = vi.fn();
  const reporter = {
    traceId: 'live-call:s1:test-trace',
    record: recordSpy,
    flush: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  return {
    session,
    reporter,
    recordSpy,
    createSession: vi.fn(async () => session),
    createReporter: vi.fn(() => reporter),
    createTraceId: vi.fn(() => 'live-call:s1:test-trace'),
    stopButtonStream: vi.fn(),
  };
});

vi.mock('./assistant-pcm-stream-websocket-player', () => ({
  stopAssistantPcmStream: mocks.stopButtonStream,
}));

vi.mock('./live-call-diagnostics-client', () => ({
  createLiveCallDiagnosticsReporter: mocks.createReporter,
  createLiveCallTraceId: mocks.createTraceId,
}));

vi.mock('./live-voice-pcm-session', () => ({
  createLiveVoicePcmSession: mocks.createSession,
}));

import {
  initializeLiveVoiceUnifiedAudioController,
  isLiveVoiceUnifiedAudioInstalled,
  shouldUseUnifiedLiveVoiceAudio,
} from './live-voice-unified-audio-controller';
import { pipelineFetch } from '../../../api/fetchPipeline';
import { liveCallPresentationStore, liveCallVoiceMode } from './live-call-presentation-store';

let cleanup: (() => void) | null = null;
let streamEvents: Array<Record<string, unknown>> | null = null;
let greetingEvents: Array<Record<string, unknown>> | null = null;
let fetchMock: ReturnType<typeof vi.fn>;

function renderLiveVoice(active = true, autoSpeak = true): void {
  liveCallPresentationStore.resetForTests();
  liveCallPresentationStore.update({
    autoSpeak,
    captureOwned: true,
    captureStatus: active ? 'connected' : 'idle',
    captureActive: active,
  });
  document.body.innerHTML = `
    <section class="assistant-live-card" data-live-voice-status="${active ? 'connected' : 'idle'}">
      <button type="button">${active ? 'End Call' : 'Start Call'}</button>
      <div class="assistant-voice-orb" data-voice-mode="${active ? 'listening' : 'idle'}"></div>
      <label class="assistant-voice-toggle"><input type="checkbox" ${autoSpeak ? 'checked' : ''}> Auto-speak</label>
    </section>
    <div class="assistant-inline-status"></div>`;
}

function chatStreamResponse(events: Array<Record<string, unknown>> = [
  { type: 'user_message', message: { id: 'u1', metadata: { assistant_turn_id: 'assistant-turn:t1' } } },
  { type: 'text_chunk', text: 'Hello there. This first phrase is ready for speech.' },
  { type: 'text_chunk', text: 'The second phrase should enter the same continuous queue.' },
  { type: 'session', session: { id: 's1', messages: [{ id: 'a1', role: 'assistant' }] } },
  { type: 'done' },
]): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function greetingStreamResponse(): Response {
  return chatStreamResponse(greetingEvents ?? [
    { type: 'text_chunk', text: 'Hey there! How is your day going?' },
    { type: 'complete', content: 'Hey there! How is your day going?', metadata: { transient: true } },
    { type: 'done' },
  ]);
}

function requestPath(input: RequestInfo | URL): string {
  const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  return new URL(raw, window.location.origin).pathname;
}

beforeEach(() => {
  renderLiveVoice();
  streamEvents = null;
  greetingEvents = null;
  window.localStorage.clear();
  window.localStorage.setItem('omnix.chatbot.assistantSettings', JSON.stringify({ voiceId: 'Jinx' }));
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path.endsWith('/live-call/runtime')) {
      return new Response(JSON.stringify({ session_id: 's1', greeting: '' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (path.endsWith('/live-call/greeting/stream')) return greetingStreamResponse();
    return chatStreamResponse(streamEvents ?? undefined);
  });
  vi.stubGlobal('fetch', fetchMock);
  mocks.session.enqueueOutputPhrase.mockReset().mockResolvedValue(undefined);
  mocks.session.enqueueSilence.mockReset().mockResolvedValue(undefined);
  mocks.session.enqueueCue.mockReset().mockResolvedValue(undefined);
  mocks.session.cancelOutputItem.mockReset().mockResolvedValue(undefined);
  mocks.session.waitForOutputItem.mockReset().mockResolvedValue(undefined);
  mocks.session.setStartPolicy.mockReset();
  mocks.session.finish.mockReset().mockResolvedValue(undefined);
  mocks.session.stop.mockReset().mockResolvedValue(undefined);
  mocks.session.isClosed.mockReset().mockReturnValue(false);
  mocks.recordSpy.mockReset();
  mocks.reporter.record = mocks.recordSpy;
  mocks.reporter.flush.mockReset().mockResolvedValue(undefined);
  mocks.reporter.close.mockReset().mockResolvedValue(undefined);
  mocks.createSession.mockReset().mockResolvedValue(mocks.session);
  mocks.createReporter.mockReset().mockReturnValue(mocks.reporter);
  mocks.createTraceId.mockReset().mockReturnValue('live-call:s1:test-trace');
  mocks.stopButtonStream.mockReset();
  cleanup = initializeLiveVoiceUnifiedAudioController();
});

afterEach(async () => {
  const hadSharedSession = mocks.createSession.mock.calls.length > 0;
  cleanup?.();
  cleanup = null;
  if (hadSharedSession) {
    await waitFor(() => expect(mocks.session.stop).toHaveBeenCalled());
  }
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice unified audio controller', () => {
  it('reports whether it owns assistant playback, so Chat does not play it too', () => {
    expect(isLiveVoiceUnifiedAudioInstalled()).toBe(true);
    cleanup?.();
    cleanup = null;
    expect(isLiveVoiceUnifiedAudioInstalled()).toBe(false);
  });

  it('records installation and uses one persistent PCM session for every phrase', async () => {
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'controller_installed',
      expect.objectContaining({ fetch_wrapped: true }),
      'controller',
    );
    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: 'hello' }),
    });
    const applicationEvents = await response.text();

    expect(applicationEvents).not.toContain('text_chunk');
    expect(applicationEvents).toContain('user_message');
    expect(applicationEvents).toContain('session');
    expect(applicationEvents).toContain('done');
    const forwardedInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const forwardedBody = JSON.parse(String(forwardedInit.body)) as Record<string, unknown>;
    expect(forwardedBody.user_turn_id).toMatch(/^voice-user-turn:/);
    expect(forwardedBody.speech_segment_id).toMatch(/^voice-segment:/);
    expect(forwardedInit.signal).toBeInstanceOf(AbortSignal);

    await waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    expect(mocks.createSession).toHaveBeenCalledWith(
      'live-call:s1:test-trace',
      'Jinx',
      mocks.reporter,
      expect.objectContaining({ sessionScoped: true }),
    );
    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(3));
    expect(mocks.session.setStartPolicy).toHaveBeenCalledTimes(1);
    expect(mocks.session.setStartPolicy).toHaveBeenCalledWith(expect.objectContaining({
      minimumBufferedSpeechMs: 400,
    }));
    expect(mocks.session.enqueueSilence).toHaveBeenCalledWith(
      expect.any(Number),
      expect.any(String),
      120,
    );
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenNthCalledWith(
      1,
      'Hello there.',
      0,
      expect.objectContaining({ generationEpoch: expect.any(Number), outputOrder: 0 }),
      {},
    );
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenNthCalledWith(
      2,
      'This first phrase is ready for speech.',
      1,
      expect.objectContaining({ generationEpoch: expect.any(Number), outputOrder: 1 }),
      {},
    );
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenNthCalledWith(
      3,
      'The second phrase should enter the same continuous queue.',
      2,
      expect.objectContaining({ generationEpoch: expect.any(Number), outputOrder: 2 }),
      {},
    );
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'assistant_turn_linked',
      expect.objectContaining({ assistant_turn_id: 'assistant-turn:t1' }),
      'controller',
    );
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'perceived_onset_planned',
      expect.objectContaining({ sample_rate: 48_000, source: 'fallback' }),
      'controller',
    );
    await waitFor(() => expect(mocks.session.waitForOutputItem).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(mocks.reporter.close).toHaveBeenCalledWith(
      'turn_finished',
      expect.objectContaining({ phrases: 3, text_chunks: 2, assistant_turn_id: 'assistant-turn:t1', turn_kind: 'response' }),
    ));
    expect(liveCallVoiceMode(liveCallPresentationStore.getState())).toBe('listening');
  });

  it('uses one performance plan for onset, pause policy, cue decision, and TTS dispatch', async () => {
    window.localStorage.setItem('omnix.liveConversation.effectiveProfile', JSON.stringify({
      presence_preset: 'listener',
      talkativeness: 50,
      conversation_stance: 'listen',
      conversation_pace: 'reflective',
      interruption_preference: 'balanced',
      assistant_backchannel_mode: 'natural',
      initiative_mode: 'gentle',
      idle_threshold_ms: 15_000,
      long_pause_behavior: 'wait',
      response_length: 'conversational',
      response_onset_style: 'reflective',
      emotional_attunement: 'expressive',
      topic_continuity: 'natural',
      max_idle_prompts: 1,
      duplex_mode: 'echo_aware',
      pronunciation_save_policy: 'ask',
      profile_version: 1,
    }));
    streamEvents = [
      { type: 'user_message', message: { id: 'u1', metadata: { assistant_turn_id: 'assistant-turn:plan' } } },
      { type: 'text_chunk', text: 'I think the safer option is to wait.' },
      { type: 'done' },
    ];

    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', { method: 'POST' });
    await response.text();

    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(1));
    const options = mocks.session.enqueueOutputPhrase.mock.calls[0]?.[3];
    expect(options?.performancePlan).toMatchObject({
      speech_act: 'reflection',
      pace: 'slightly_slow',
      clause_pause: 'long',
      onset_policy: { desired_perceived_onset_ms: 650 },
    });
    expect(mocks.session.setStartPolicy).toHaveBeenCalledWith(expect.objectContaining({
      minimumBufferedSpeechMs: 400,
    }));
    expect(mocks.session.enqueueCue).toHaveBeenCalledWith('hmm', expect.stringMatching(/^hmm-v\d$/), 0.62);
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'phrase_queued',
      expect.objectContaining({
        phrase_index: 0,
        performance_speech_act: 'reflection',
        performance_pause: 'long',
        response_cue: 'hmm',
      }),
      'controller',
    );
  });

  it('generates one transient greeting only after runtime and microphone connection are ready', async () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-start'));
    await pipelineFetch('/api/chat/sessions/s1/live-call/runtime');
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input).endsWith('/live-call/greeting/stream'))).toBe(false);

    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-connected'));

    await waitFor(() => expect(
      fetchMock.mock.calls.some(([input]) => requestPath(input).endsWith('/live-call/greeting/stream')),
    ).toBe(true));
    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(2));
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenNthCalledWith(
      1,
      'Hey there!',
      0,
      expect.objectContaining({ generationEpoch: expect.any(Number), outputOrder: 0 }),
      {},
    );
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenNthCalledWith(
      2,
      'How is your day going?',
      1,
      expect.objectContaining({ generationEpoch: expect.any(Number), outputOrder: 1 }),
      {},
    );
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'turn_intercepted',
      expect.objectContaining({ turn_kind: 'greeting', request_path: '/api/chat/sessions/s1/live-call/greeting/stream' }),
      'controller',
    );
    expect(fetchMock.mock.calls.filter(([input]) => requestPath(input).endsWith('/live-call/greeting/stream'))).toHaveLength(1);
  });

  it('does not generate a greeting when the user speaks before startup is ready', async () => {
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-start'));
    await pipelineFetch('/api/chat/sessions/s1/live-call/runtime');
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-user-speech'));
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-connected'));
    await Promise.resolve();

    expect(fetchMock.mock.calls.some(([input]) => requestPath(input).endsWith('/live-call/greeting/stream'))).toBe(false);
    expect(mocks.session.enqueueOutputPhrase).not.toHaveBeenCalled();
  });

  it('aborts greeting generation and playback when the user begins speaking', async () => {
    let resolveOutput: () => void = () => undefined;
    mocks.session.waitForOutputItem.mockImplementationOnce(() => new Promise<undefined>((resolve) => {
      resolveOutput = () => resolve(undefined);
    }));
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-start'));
    await pipelineFetch('/api/chat/sessions/s1/live-call/runtime');
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-call-connected'));

    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(2));
    const greetingCall = fetchMock.mock.calls.find(([input]) => requestPath(input).endsWith('/live-call/greeting/stream'));
    const signal = greetingCall?.[1]?.signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    expect(liveCallPresentationStore.getState().outputKind).toBe('greeting');

    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-user-speech'));

    await waitFor(() => expect(signal.aborted).toBe(true));
    await waitFor(() => expect(mocks.session.cancelOutputItem).toHaveBeenCalledWith(
      expect.stringContaining('conversation-'),
      expect.any(Number),
      'user-spoke-during-greeting',
    ));
    expect(liveCallPresentationStore.getState().outputKind).toBeNull();
    expect(liveCallVoiceMode(liveCallPresentationStore.getState())).toBe('listening');
    resolveOutput();
  });

  it('uses the authoritative live-call voice instead of the chat voice setting', async () => {
    const card = document.querySelector<HTMLElement>('.assistant-live-card');
    if (card) card.dataset.liveVoiceId = 'Maya';

    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', { method: 'POST' });
    await response.text();

    await waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    expect(mocks.createSession).toHaveBeenCalledWith(
      'live-call:s1:test-trace',
      'Maya',
      mocks.reporter,
      expect.objectContaining({ sessionScoped: true }),
    );
  });

  it('publishes authoritative playback state until unified response audio drains', async () => {
    const states: boolean[] = [];
    window.addEventListener('omnix:assistant-audio-playback-state', ((event: CustomEvent<{ speaking: boolean }>) => {
      states.push(event.detail.speaking);
    }) as EventListener);

    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', { method: 'POST' });
    await response.text();

    await waitFor(() => expect(states).toContain(true));
    await waitFor(() => expect(mocks.session.waitForOutputItem).toHaveBeenCalled());
    await waitFor(() => expect(states.at(-1)).toBe(false));
    expect(states).toEqual([true, false]);
  });

  it('reuses the speech turn id as the end-to-end diagnostics trace', async () => {
    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: 'hello', live_voice_turn_id: 'voice-turn:12345' }),
    });
    await response.text();

    expect(mocks.createReporter).toHaveBeenCalledWith('live-call:voice-turn:12345');
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'turn_intercepted',
      expect.objectContaining({ voice_turn_id: 'voice-turn:12345', turn_kind: 'response' }),
      'controller',
    );
    expect(mocks.createTraceId).toHaveBeenCalledWith('s1:audio-session');
  });

  it('skips non-speech-only trailing chunks without stopping the turn', async () => {
    streamEvents = [
      { type: 'user_message', message: { id: 'u1', metadata: { assistant_turn_id: 'assistant-turn:t2' } } },
      { type: 'text_chunk', text: 'Here is a complete spoken answer for the live call.' },
      { type: 'text_chunk', text: '☀️' },
      { type: 'text_chunk', text: '✨' },
      { type: 'session', session: { id: 's1', messages: [{ id: 'a1', role: 'assistant' }] } },
      { type: 'done' },
    ];

    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', { method: 'POST' });
    await response.text();

    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(1));
    expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledWith(
      'Here is a complete spoken answer for the live call.',
      0,
      expect.objectContaining({ outputOrder: 0 }),
      {},
    );
    expect(mocks.recordSpy).toHaveBeenCalledWith(
      'phrase_skipped',
      expect.objectContaining({ reason: 'non-speech-only', text: '☀️ ✨' }),
      'controller',
    );
    await waitFor(() => expect(mocks.session.waitForOutputItem).toHaveBeenCalledTimes(1));
    expect(mocks.session.stop).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.reporter.close).toHaveBeenCalledWith(
      'turn_finished',
      expect.objectContaining({ phrases: 1, text_chunks: 3, turn_kind: 'response' }),
    ));
  });

  it('uses live streaming endpoints as activation signals and respects Auto-speak', () => {
    renderLiveVoice(false, true);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'POST' })).toBe(true);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/live-call/greeting/stream', { method: 'POST' })).toBe(true);

    renderLiveVoice(true, false);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'POST' })).toBe(false);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/live-call/greeting/stream', { method: 'POST' })).toBe(false);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages', { method: 'POST' })).toBe(false);
    expect(shouldUseUnifiedLiveVoiceAudio('/api/chat/sessions/s1/messages/stream', { method: 'GET' })).toBe(false);
  });

  it('aborts the active request and cancels owned output on interruption', async () => {
    let resolveOutput: () => void = () => undefined;
    mocks.session.waitForOutputItem.mockImplementationOnce(() => new Promise<undefined>((resolve) => {
      resolveOutput = () => resolve(undefined);
    }));
    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', { method: 'POST' });
    await response.text();
    await waitFor(() => expect(mocks.session.waitForOutputItem).toHaveBeenCalled());
    const forwardedInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const signal = forwardedInit.signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-interrupt'));

    await waitFor(() => expect(signal.aborted).toBe(true));
    await waitFor(() => expect(mocks.session.cancelOutputItem).toHaveBeenCalledWith(
      expect.stringContaining('conversation-'),
      expect.any(Number),
      'voice-interrupt',
    ));
    expect(mocks.session.stop).not.toHaveBeenCalled();
    expect(mocks.stopButtonStream).toHaveBeenCalled();
    await waitFor(() => expect(mocks.reporter.close).toHaveBeenCalledWith(
      'turn_stopped',
      expect.objectContaining({ reason: 'voice-interrupt', assistant_turn_id: 'assistant-turn:t1', turn_kind: 'response' }),
    ));
    expect(liveCallVoiceMode(liveCallPresentationStore.getState())).toBe('listening');
    resolveOutput();
  });
});
