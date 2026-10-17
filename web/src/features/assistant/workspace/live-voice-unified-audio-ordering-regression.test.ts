/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const cueControl: { release: (() => void) | null } = { release: null };
  const session = {
    sampleRate: 24_000,
    enqueuePhrase: vi.fn(async () => undefined),
    enqueueOutputPhrase: vi.fn(async () => undefined),
    enqueueSilence: vi.fn(async () => undefined),
    enqueueCue: vi.fn(() => new Promise<void>((resolve) => {
      cueControl.release = resolve;
    })),
    cancelSegment: vi.fn(),
    cancelOutputItem: vi.fn(async () => undefined),
    cancelAllAfter: vi.fn(),
    waitForOutputItem: vi.fn(async () => undefined),
    setStartPolicy: vi.fn(),
    finish: vi.fn(async () => undefined),
    stop: vi.fn(async () => undefined),
    isClosed: vi.fn(() => false),
  };
  const recordSpy = vi.fn();
  const reporter = {
    traceId: 'live-call:s1:ordering-regression',
    record: recordSpy,
    flush: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  return {
    cueControl,
    session,
    reporter,
    recordSpy,
    createSession: vi.fn(async () => session),
    createReporter: vi.fn(() => reporter),
    createTraceId: vi.fn(() => 'live-call:s1:ordering-regression'),
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

vi.mock('./live-voice-humanization-flags', () => ({
  readLiveVoiceHumanizationFlags: () => ({
    master: true,
    stableClauses: true,
    naturalTiming: true,
    responseCues: true,
    performancePlans: false,
    vocalContinuity: false,
    listenerCues: false,
    proceduralCueFallback: false,
  }),
}));

vi.mock('./live-speech-synthesis-options', () => ({
  createLiveSpeechSynthesisOptions: () => ({}),
  selectLiveResponseCue: (_phrase: string, _plan: unknown, phraseIndex: number) => (
    phraseIndex === 0
      ? { allowed: true, cueId: 'inhale', variantId: 'inhale-v1', reason: 'test' }
      : { allowed: false, cueId: null, variantId: null, reason: 'opening_only' }
  ),
}));

vi.mock('./live-voice-natural-timing', () => ({
  createOnsetTimingPlan: () => ({
    desiredPerceivedOnsetMs: 0,
    elapsedMs: 0,
    extraDelayMs: 0,
  }),
  naturalPauseAfterClause: () => ({ durationMs: 400, reason: 'reflection' }),
}));

import { initializeLiveVoiceUnifiedAudioController } from './live-voice-unified-audio-controller';
import { pipelineFetch } from '../../../api/fetchPipeline';
import { liveCallPresentationStore } from './live-call-presentation-store';

let cleanup: (() => void) | null = null;
let fetchMock: ReturnType<typeof vi.fn>;

function renderLiveVoice(): void {
  liveCallPresentationStore.resetForTests();
  liveCallPresentationStore.update({ autoSpeak: true, captureOwned: true, captureStatus: 'connected', captureActive: true });
  document.body.innerHTML = `
    <section class="assistant-live-card" data-live-voice-status="connected">
      <button type="button">End Call</button>
      <div class="assistant-voice-orb" data-voice-mode="listening"></div>
      <label class="assistant-voice-toggle"><input type="checkbox" checked> Auto-speak</label>
    </section>
    <div class="assistant-inline-status"></div>`;
}

function chatStreamResponse(): Response {
  const events = [
    { type: 'user_message', message: { id: 'u1', metadata: { assistant_turn_id: 'assistant-turn:ordering' } } },
    { type: 'text_chunk', text: 'First phrase is ready for speech.' },
    { type: 'text_chunk', text: 'Second phrase must stay behind the first.' },
    { type: 'session', session: { id: 's1', messages: [{ id: 'a1', role: 'assistant' }] } },
    { type: 'done' },
  ];
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

beforeEach(() => {
  renderLiveVoice();
  window.localStorage.clear();
  window.localStorage.setItem('omnix.chatbot.assistantSettings', JSON.stringify({ voiceId: 'Sofia' }));
  mocks.cueControl.release = null;
  mocks.session.enqueueOutputPhrase.mockReset().mockResolvedValue(undefined);
  mocks.session.enqueueSilence.mockReset().mockResolvedValue(undefined);
  mocks.session.enqueueCue.mockReset().mockImplementation(() => new Promise<void>((resolve) => {
    mocks.cueControl.release = resolve;
  }));
  mocks.session.cancelOutputItem.mockReset().mockResolvedValue(undefined);
  mocks.session.waitForOutputItem.mockReset().mockResolvedValue(undefined);
  mocks.session.setStartPolicy.mockReset();
  mocks.session.stop.mockReset().mockResolvedValue(undefined);
  mocks.session.isClosed.mockReset().mockReturnValue(false);
  mocks.recordSpy.mockReset();
  mocks.reporter.record = mocks.recordSpy;
  mocks.reporter.close.mockReset().mockResolvedValue(undefined);
  mocks.createSession.mockReset().mockResolvedValue(mocks.session);
  mocks.createReporter.mockReset().mockReturnValue(mocks.reporter);
  mocks.createTraceId.mockReset().mockReturnValue('live-call:s1:ordering-regression');
  fetchMock = vi.fn(async () => chatStreamResponse());
  vi.stubGlobal('fetch', fetchMock);
  cleanup = initializeLiveVoiceUnifiedAudioController();
});

afterEach(async () => {
  mocks.cueControl.release?.();
  cleanup?.();
  cleanup = null;
  await Promise.resolve();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice phrase ordering regression', () => {
  it('does not enqueue a later phrase pause while the first phrase cue is still pending', async () => {
    const response = await pipelineFetch('/api/chat/sessions/s1/messages/stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: 'hello', live_voice_turn_id: 'voice-turn:ordering' }),
    });
    await response.text();

    await waitFor(() => expect(mocks.session.enqueueCue).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mocks.recordSpy).toHaveBeenCalledWith(
      'phrase_queued',
      expect.objectContaining({ phrase_index: 1 }),
      'controller',
    ));

    expect(mocks.session.enqueueSilence).not.toHaveBeenCalled();
    expect(mocks.session.enqueueOutputPhrase).not.toHaveBeenCalled();

    mocks.cueControl.release?.();

    await waitFor(() => expect(mocks.session.enqueueOutputPhrase).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mocks.session.enqueueSilence).toHaveBeenCalledTimes(1));

    const cueOrder = mocks.session.enqueueCue.mock.invocationCallOrder[0];
    const firstSpeechOrder = mocks.session.enqueueOutputPhrase.mock.invocationCallOrder[0];
    const pauseOrder = mocks.session.enqueueSilence.mock.invocationCallOrder[0];
    const secondSpeechOrder = mocks.session.enqueueOutputPhrase.mock.invocationCallOrder[1];
    expect(cueOrder).toBeLessThan(firstSpeechOrder);
    expect(firstSpeechOrder).toBeLessThan(pauseOrder);
    expect(pauseOrder).toBeLessThan(secondSpeechOrder);
  });
});
