 
import { activeFetchMiddlewares, registerFetchMiddleware, type FetchNext } from '../../../api/fetchPipeline';
import { stopAssistantPcmStream } from './assistant-pcm-stream-websocket-player';
import {
  createLiveCallDiagnosticsReporter,
  createLiveCallTraceId,
  type LiveCallDiagnosticsReporter,
} from './live-call-diagnostics-client';
import {
  createLiveSpeechSynthesisOptions,
  selectLiveResponseCue,
} from './live-speech-synthesis-options';
import type {
  SpeechPerformancePlan,
  SpeechSynthesisOptions,
} from './live-speech-performance-contract';
import {
  StableClauseAccumulator,
  mergeStreamText,
  type StableClause,
} from './live-voice-clause-stabilizer';
import {
  advanceDeliveryLedger,
  appendDeliveryPhrase,
  createLiveVoiceDeliveryLedger,
  instrumentDeliveryReporter,
  removeDeliveryLedgerRow,
  renderDeliveryLedger,
  type LiveVoiceDeliveryLedger,
} from './live-voice-delivery-ledger';
import {
  readLiveVoiceHumanizationFlags,
  type LiveVoiceHumanizationFlags,
} from './live-voice-humanization-flags';
import { createOnsetTimingPlan, naturalPauseAfterClause } from './live-voice-natural-timing';
import {
  createLiveVoicePcmSession,
  type LiveOutputOwnership,
  type LiveVoicePcmSession,
} from './live-voice-pcm-session';
import { openStream, statusError } from '../../../api/transport';
import { chatStreamEventSchema, parseJson } from '../../../api/schemas/streams';
import { liveCallPresentationStore } from './live-call-presentation-store';
import { ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent } from '../../../events/bus';

const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages\/stream$/;
const LIVE_CALL_RUNTIME_PATH = /^\/api\/chat\/sessions\/([^/]+)\/live-call\/runtime$/;
const LIVE_CALL_GREETING_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/live-call\/greeting\/stream$/;
const VOICE_SETTINGS_KEY = 'omnix.chatbot.assistantSettings';
const REQUESTED_PLAYBACK_SAMPLE_RATE = 24_000;
const START_BUFFER_MS = 400;
const PAUSE_FOLLOWING_SPEECH_BUFFER_MS = 120;
const AUDIO_COMPLETION_TIMEOUT_MS = 60_000;
const SPEAKABLE_TEXT_PATTERN = /[\p{L}\p{N}]/u;

type ChatStreamEvent = {
  type?: string;
  text?: string;
  message?: {
    metadata?: Record<string, unknown>;
  };
};

export type LiveTurnKind = 'greeting' | 'response';

type ActiveLiveTurn = {
  generation: number;
  kind: LiveTurnKind;
  sessionId: string;
  flags: LiveVoiceHumanizationFlags;
  traceId: string;
  startedAtMs: number;
  reporter: LiveCallDiagnosticsReporter;
  sessionPromise: Promise<LiveVoicePcmSession>;
  audioTasks: Promise<void>[];
  outputOwnerships: LiveOutputOwnership[];
  abortController: AbortController;
  userTurnId: string;
  speechSegmentId: string;
  assistantTurnId: string | null;
  phraseCount: number;
  textChunkCount: number;
  delivery: LiveVoiceDeliveryLedger;
  previousClause: string | null;
  previousPerformancePlan: SpeechPerformancePlan | null;
};

type LiveTurnIds = {
  userTurnId: string;
  speechSegmentId: string;
};

type SharedLiveAudioSession = {
  sessionId: string;
  voiceId: string | null;
  traceId: string;
  reporter: LiveCallDiagnosticsReporter;
  sessionPromise: Promise<LiveVoicePcmSession>;
  session: LiveVoicePcmSession | null;
  nextPhraseIndex: number;
  nextOutputOrder: number;
};

type LiveVoiceRequestPayload = Record<string, unknown> & {
  live_voice_turn_id?: unknown;
};

type GreetingStartup = {
  token: number;
  connected: boolean;
  sessionId: string | null;
  userSpoke: boolean;
  started: boolean;
  requestAbortController: AbortController | null;
};

const MIDDLEWARE = 'live-voice-unified-audio';
let removeMiddleware: (() => void) | null = null;
let playbackGeneration = 0;
let activeTurn: ActiveLiveTurn | null = null;
let sharedAudioSession: SharedLiveAudioSession | null = null;
let greetingStartup: GreetingStartup | null = null;
let greetingStartupToken = 0;
let reportedSpeaking = false;

/** Whether unified live audio plays assistant speech (Chat must not play it too). */
export function isLiveVoiceUnifiedAudioInstalled(): boolean {
  return removeMiddleware !== null;
}

export function initializeLiveVoiceUnifiedAudioController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (removeMiddleware) return () => undefined;
  removeMiddleware = registerFetchMiddleware(MIDDLEWARE, interceptLiveVoiceFetch);
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, stopLiveVoiceUnifiedAudio);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, stopLiveVoiceUnifiedAudio);
  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleGreetingCallStart);
  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, handleGreetingCallConnected);
  window.addEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleGreetingUserSpeech);
  window.addEventListener('beforeunload', stopLiveVoiceUnifiedAudio);
  const installedReporter = createLiveCallDiagnosticsReporter('live-call:controller');
  installedReporter.record('controller_installed', {
    location: window.location.href,
    fetch_wrapped: activeFetchMiddlewares().includes(MIDDLEWARE),
    humanization_flags: readLiveVoiceHumanizationFlags(),
  }, 'controller');
  void installedReporter.close('controller_install_confirmed');

  return () => {
    removeMiddleware?.();
    removeMiddleware = null;
    window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, stopLiveVoiceUnifiedAudio);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, stopLiveVoiceUnifiedAudio);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleGreetingCallStart);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, handleGreetingCallConnected);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleGreetingUserSpeech);
    window.removeEventListener('beforeunload', stopLiveVoiceUnifiedAudio);
    stopLiveVoiceUnifiedAudio();
  };
}

async function interceptLiveVoiceFetch(input: RequestInfo | URL, init: RequestInit | undefined, next: FetchNext): Promise<Response> {
  const fetchImpl = next;
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();

  const runtimeMatch = method === 'GET' ? LIVE_CALL_RUNTIME_PATH.exec(url.pathname) : null;
  if (runtimeMatch) {
    const response = await fetchImpl(input, init);
    captureGreetingSession(runtimeMatch[1], response.ok);
    return response;
  }

  const responseMatch = method === 'POST' ? CHAT_STREAM_PATH.exec(url.pathname) : null;
  const greetingMatch = method === 'POST' ? LIVE_CALL_GREETING_STREAM_PATH.exec(url.pathname) : null;
  const flags = readLiveVoiceHumanizationFlags();
  if ((!responseMatch && !greetingMatch) || !isAutoSpeakEnabled() || !flags.master) {
    return fetchImpl(input, init);
  }

  const kind: LiveTurnKind = greetingMatch ? 'greeting' : 'response';
  if (kind === 'response') cancelGreetingStartup('real-response-started', true);
  await stopActiveTurn(kind === 'response' ? 'superseded-by-real-response' : 'superseded-by-greeting');
  stopAssistantPcmStream();

  const sessionId = decodeURIComponent((responseMatch ?? greetingMatch)?.[1] ?? 'unknown');
  const voiceTurnId = kind === 'response' ? extractLiveVoiceTurnId(init) : null;
  const ids = createLiveTurnIds();
  const abortController = new AbortController();
  connectAbortSignal(init?.signal, abortController);
  const preparedInit = kind === 'response'
    ? injectLiveTurnIds(init, ids, abortController.signal)
    : { ...init, signal: abortController.signal };
  const response = await fetchImpl(input, preparedInit);
  if (!response.body || !response.ok) return response;

  const generation = ++playbackGeneration;
  const traceId = kind === 'greeting'
    ? `live-call:greeting:${sessionId}:${generation}`
    : voiceTurnId ? `live-call:${voiceTurnId}` : createLiveCallTraceId(sessionId);
  const reporter = createLiveCallDiagnosticsReporter(traceId);
  const delivery = createLiveVoiceDeliveryLedger(REQUESTED_PLAYBACK_SAMPLE_RATE);
  instrumentDeliveryReporter(reporter, () => delivery, (ledger) => {
    renderDeliveryLedger(ledger);
    recordDeliveryCheckpoint(reporter, ledger);
  });
  const voiceId = selectedVoiceId();
  const startedAtMs = performance.now();
  const sharedAudio = await ensureSharedAudioSession(sessionId, voiceId);
  const sessionPromise = sharedAudio.sessionPromise;
  const turn: ActiveLiveTurn = {
    generation,
    kind,
    sessionId,
    flags,
    traceId,
    startedAtMs,
    reporter,
    sessionPromise,
    audioTasks: [],
    outputOwnerships: [],
    abortController,
    userTurnId: ids.userTurnId,
    speechSegmentId: ids.speechSegmentId,
    assistantTurnId: null,
    phraseCount: 0,
    textChunkCount: 0,
    delivery,
    previousClause: null,
    previousPerformancePlan: null,
  };
  activeTurn = turn;
  reporter.record('turn_intercepted', {
    session_id: sessionId,
    request_path: url.pathname,
    turn_kind: kind,
    voice_id: voiceId,
    auto_speak: true,
    humanization_flags: flags,
    user_turn_id: kind === 'response' ? ids.userTurnId : null,
    speech_segment_id: kind === 'response' ? ids.speechSegmentId : null,
    voice_turn_id: voiceTurnId,
  }, 'controller');

  const [applicationBranch, audioBranch] = response.body.tee();
  void consumeLiveVoiceText(audioBranch, turn).catch(async (error: unknown) => {
    if (generation !== playbackGeneration || abortController.signal.aborted) return;
    const message = error instanceof Error ? error.message : 'Live voice audio streaming failed.';
    reporter.record('turn_failed', { error: message, turn_kind: kind }, 'controller');
    setInlineStatus(message);
    setVoiceSpeaking(false);
    const session = await sessionPromise.catch(() => null);
    if (session) await cancelTurnOutputs(turn, 'turn-failed', session);
    recordDeliveryCheckpoint(reporter, delivery);
    await reporter.close('turn_failed_final', { error: message, turn_kind: kind });
    removeDeliveryLedgerRow();
    if (activeTurn?.generation === generation) activeTurn = null;
  });

  const headers = new Headers(response.headers);
  headers.delete('content-length');
  return new Response(filterLegacyAudioTextChunks(applicationBranch), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export function shouldUseUnifiedLiveVoiceAudio(input: RequestInfo | URL, init?: RequestInit): boolean {
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  if (method !== 'POST') return false;
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  return readLiveVoiceHumanizationFlags().master
    && (CHAT_STREAM_PATH.test(url.pathname) || LIVE_CALL_GREETING_STREAM_PATH.test(url.pathname))
    && isAutoSpeakEnabled();
}

function handleGreetingCallStart(): void {
  cancelGreetingStartup('new-call-started');
  greetingStartup = {
    token: ++greetingStartupToken,
    connected: false,
    sessionId: null,
    userSpoke: false,
    started: false,
    requestAbortController: null,
  };
}

function handleGreetingCallConnected(): void {
  if (!greetingStartup) return;
  greetingStartup.connected = true;
  maybeStartGeneratedGreeting();
}

function handleGreetingUserSpeech(): void {
  cancelGreetingStartup('user-spoke-before-greeting', true);
  if (activeTurn?.kind !== 'greeting') return;
  playbackGeneration += 1;
  void stopActiveTurn('user-spoke-during-greeting');
  stopAssistantPcmStream();
  setVoiceSpeaking(false);
}

function captureGreetingSession(encodedSessionId: string, responseOk: boolean): void {
  if (!responseOk || !greetingStartup || greetingStartup.userSpoke) return;
  greetingStartup.sessionId = decodeURIComponent(encodedSessionId);
  maybeStartGeneratedGreeting();
}

function maybeStartGeneratedGreeting(): void {
  const startup = greetingStartup;
  if (
    !startup
    || startup.started
    || startup.userSpoke
    || !startup.connected
    || !startup.sessionId
    || !isAutoSpeakEnabled()
    || !readLiveVoiceHumanizationFlags().master
  ) return;
  startup.started = true;
  const abortController = new AbortController();
  startup.requestAbortController = abortController;
  const path = `/api/chat/sessions/${encodeURIComponent(startup.sessionId)}/live-call/greeting/stream` as const;
  void openStream(path, { body: {}, signal: abortController.signal }).catch(statusError('Live-call greeting')).then(async (response) => {
    await response.text();
  }).catch((error: unknown) => {
    if (abortController.signal.aborted) return;
    console.warn('[Omnix Voice] generated live-call greeting failed', error);
  }).finally(() => {
    if (greetingStartup?.token === startup.token) greetingStartup.requestAbortController = null;
  });
}

function cancelGreetingStartup(reason: string, preserveUserSpoke = false): void {
  const startup = greetingStartup;
  if (!startup) return;
  if (preserveUserSpoke) startup.userSpoke = true;
  startup.requestAbortController?.abort(reason);
  startup.requestAbortController = null;
  if (!preserveUserSpoke) greetingStartup = null;
}

async function consumeLiveVoiceText(stream: ReadableStream<Uint8Array>, turn: ActiveLiveTurn): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const clauses = new StableClauseAccumulator();
  let fallbackText = '';
  let pending = '';
  let deadlineTimer: ReturnType<typeof window.setTimeout> | null = null;

  const clearDeadlineTimer = (): void => {
    if (deadlineTimer !== null) window.clearTimeout(deadlineTimer);
    deadlineTimer = null;
  };
  const commit = (ready: StableClause[]): void => {
    ready.forEach((clause) => queuePhrase(clause.text, turn, clause.reason));
  };
  const ingestText = (text: string): void => {
    if (turn.flags.stableClauses) {
      commit(clauses.append(text, performance.now()));
      scheduleDeadline();
    } else {
      fallbackText = mergeStreamText(fallbackText, text.trim());
    }
  };
  const scheduleDeadline = (): void => {
    clearDeadlineTimer();
    if (!turn.flags.stableClauses) return;
    const remaining = clauses.deadlineRemainingMs();
    if (remaining === null) return;
    deadlineTimer = window.setTimeout(() => {
      deadlineTimer = null;
      if (turn.generation !== playbackGeneration || turn.abortController.signal.aborted) return;
      commit(clauses.takeReady(performance.now()));
      scheduleDeadline();
    }, Math.max(1, Math.ceil(remaining + 1)));
  };

  try {
    while (turn.generation === playbackGeneration && !turn.abortController.signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const blocks = pending.split(/\n\n/);
      pending = blocks.pop() ?? '';
      for (const block of blocks) {
        const event = parseSseBlock(block);
        captureAssistantTurnId(turn, event);
        if (event?.type !== 'text_chunk' || typeof event.text !== 'string') continue;
        turn.textChunkCount += 1;
        turn.reporter.record('llm_text_chunk_received', {
          text_chunk_index: turn.textChunkCount - 1,
          text: event.text,
          text_length: event.text.length,
          elapsed_ms: performance.now() - turn.startedAtMs,
          turn_kind: turn.kind,
        }, 'controller');
        ingestText(event.text);
      }
    }

    if (turn.abortController.signal.aborted) {
      await reader.cancel('live-turn-aborted').catch(() => undefined);
      return;
    }
    pending += decoder.decode();
    if (pending.trim()) {
      const event = parseSseBlock(pending);
      captureAssistantTurnId(turn, event);
      if (event?.type === 'text_chunk' && typeof event.text === 'string') {
        turn.textChunkCount += 1;
        ingestText(event.text);
      }
    }
    if (turn.flags.stableClauses) commit(clauses.flush());
    else if (fallbackText.trim()) queuePhrase(fallbackText, turn, 'stream-end');
  } finally {
    clearDeadlineTimer();
  }

  turn.reporter.record('llm_stream_finished', {
    elapsed_ms: performance.now() - turn.startedAtMs,
    text_chunks: turn.textChunkCount,
    phrases: turn.phraseCount,
    assistant_turn_id: turn.assistantTurnId,
    turn_kind: turn.kind,
  }, 'controller');

  let audioIssue: string | null = null;
  const session = await turn.sessionPromise.catch((error: unknown) => {
    audioIssue = error instanceof Error ? error.message : 'Live audio could not start.';
    turn.reporter.record('turn_audio_unavailable', { error: audioIssue }, 'controller');
    return null;
  });
  if (session) {
    try {
      const generationResults = await Promise.allSettled(turn.audioTasks);
      const rejected = generationResults.find((result) => result.status === 'rejected');
      if (rejected?.status === 'rejected') throw rejected.reason;
      await withTimeout(
        Promise.all(turn.outputOwnerships.map((ownership) => session.waitForOutputItem(
          ownership.outputId,
          ownership.generationEpoch,
        ))).then(() => undefined),
        AUDIO_COMPLETION_TIMEOUT_MS,
        'Live audio completion timed out.',
      );
    } catch (error: unknown) {
      audioIssue = error instanceof Error ? error.message : 'Live audio completion failed.';
      turn.reporter.record('turn_audio_recovered', {
        error: audioIssue,
        elapsed_ms: performance.now() - turn.startedAtMs,
      }, 'controller');
      await cancelTurnOutputs(turn, 'audio-recovery', session);
    }
  }
  if (turn.generation !== playbackGeneration) return;
  advanceDeliveryLedger(turn.delivery, Number.MAX_SAFE_INTEGER);
  recordDeliveryCheckpoint(turn.reporter, turn.delivery);
  setVoiceSpeaking(false);
  const subject = turn.kind === 'greeting' ? 'Live greeting' : 'Live response';
  setInlineStatus(audioIssue ? `${subject} finished; some audio was skipped.` : `${subject} audio finished.`);
  await turn.reporter.close('turn_finished', {
    elapsed_ms: performance.now() - turn.startedAtMs,
    text_chunks: turn.textChunkCount,
    phrases: turn.phraseCount,
    audio_degraded: Boolean(audioIssue),
    audio_error: audioIssue,
    assistant_turn_id: turn.assistantTurnId,
    turn_kind: turn.kind,
  });
  removeDeliveryLedgerRow();
  if (activeTurn?.generation === turn.generation) activeTurn = null;
}

function queuePhrase(text: string, turn: ActiveLiveTurn, reason: string): void {
  const phrase = text.trim();
  if (!phrase || turn.generation !== playbackGeneration || turn.abortController.signal.aborted) return;
  if (!SPEAKABLE_TEXT_PATTERN.test(phrase)) {
    turn.reporter.record('phrase_skipped', {
      reason: 'non-speech-only',
      text: phrase,
      text_length: phrase.length,
      elapsed_ms: performance.now() - turn.startedAtMs,
    }, 'controller');
    return;
  }

  const phraseIndex = turn.phraseCount;
  const sharedAudio = sharedAudioSession;
  if (!sharedAudio || sharedAudio.sessionId !== turn.sessionId) {
    turn.reporter.record('phrase_queue_failed', {
      phrase_index: phraseIndex,
      error: 'live_audio_session_unavailable',
      continuing: false,
    }, 'controller');
    return;
  }
  const sessionPhraseIndex = sharedAudio.nextPhraseIndex++;
  const ownership: LiveOutputOwnership = {
    outputId: conversationOutputId(turn, phraseIndex),
    generationEpoch: turn.generation,
    outputOrder: sharedAudio.nextOutputOrder++,
  };
  turn.outputOwnerships.push(ownership);
  const synthesisOptions: SpeechSynthesisOptions = createLiveSpeechSynthesisOptions(phrase, {
    scopeKey: turn.sessionId,
    enablePerformancePlan: turn.flags.performancePlans,
    enableVocalContinuity: turn.flags.vocalContinuity,
  });
  const performancePlan = synthesisOptions.performancePlan;
  const precedingPause = turn.flags.naturalTiming && turn.previousClause !== null
    ? naturalPauseAfterClause(
      turn.previousClause,
      phraseIndex - 1,
      turn.previousPerformancePlan ?? undefined,
    )
    : null;
  const cue = turn.flags.responseCues
    ? selectLiveResponseCue(phrase, performancePlan, phraseIndex)
    : { allowed: false, cueId: null, variantId: null, reason: 'rollout_disabled' };

  appendDeliveryPhrase(turn.delivery, phraseIndex, phrase);
  turn.phraseCount += 1;
  turn.previousClause = phrase;
  turn.previousPerformancePlan = performancePlan ?? null;
  setVoiceSpeaking(true, turn.kind);
  setInlineStatus(turn.kind === 'greeting' ? 'Buffering generated greeting…' : 'Buffering live response audio…');
  turn.reporter.record('phrase_queued', {
    phrase_index: phraseIndex,
    reason,
    text: phrase,
    text_length: phrase.length,
    preceding_pause_ms: precedingPause?.durationMs ?? 0,
    preceding_pause_reason: precedingPause?.reason ?? null,
    performance_schema_version: performancePlan?.schema_version ?? null,
    performance_speech_act: performancePlan?.speech_act ?? null,
    performance_pace: performancePlan?.pace ?? null,
    performance_pause: performancePlan?.clause_pause ?? null,
    response_cue: cue.allowed ? cue.cueId : null,
    response_cue_reason: cue.reason,
    elapsed_ms: performance.now() - turn.startedAtMs,
    turn_kind: turn.kind,
    output_id: ownership.outputId,
    generation_epoch: ownership.generationEpoch,
    output_order: ownership.outputOrder,
  }, 'controller');

  const previousAudioTask = turn.audioTasks[turn.audioTasks.length - 1] ?? Promise.resolve();
  const audioTask = previousAudioTask.catch(() => undefined).then(async () => {
    const session = await turn.sessionPromise;
    if (phraseIndex === 0) {
      const onsetPolicy = performancePlan?.onset_policy;
      const onset = turn.flags.naturalTiming
        ? createOnsetTimingPlan(performance.now() - turn.startedAtMs, {
          desiredPerceivedOnsetMs: onsetPolicy?.desired_perceived_onset_ms
            ?? (turn.kind === 'greeting' ? 320 : 450),
          maximumAdditionalDelayMs: onsetPolicy?.maximum_additional_delay_ms ?? 350,
        })
        : createOnsetTimingPlan(performance.now() - turn.startedAtMs, {
          desiredPerceivedOnsetMs: 0,
          maximumAdditionalDelayMs: 0,
        });
      session.setStartPolicy({
        notBeforeMs: onset.extraDelayMs,
        minimumBufferedSpeechMs: START_BUFFER_MS,
      });
      turn.reporter.record('perceived_onset_planned', {
        desired_perceived_onset_ms: onset.desiredPerceivedOnsetMs,
        elapsed_ms: onset.elapsedMs,
        extra_delay_ms: onset.extraDelayMs,
        sample_rate: session.sampleRate,
        source: !turn.flags.naturalTiming
          ? 'rollout_disabled'
          : performancePlan ? 'performance_plan' : 'fallback',
      }, 'controller');
    }
    if (precedingPause) {
      await session.enqueueSilence(
        precedingPause.durationMs,
        precedingPause.reason,
        PAUSE_FOLLOWING_SPEECH_BUFFER_MS,
      );
    }
    if (cue.allowed && cue.cueId && cue.variantId) {
      await session.enqueueCue(cue.cueId, cue.variantId, 0.62);
    }
    await session.enqueueOutputPhrase(phrase, sessionPhraseIndex, ownership, synthesisOptions);
  });
  turn.audioTasks.push(audioTask);
  void audioTask.catch((error: unknown) => {
    if (turn.abortController.signal.aborted) return;
    turn.reporter.record('phrase_queue_failed', {
      phrase_index: phraseIndex,
      error: error instanceof Error ? error.message : String(error),
      continuing: true,
    }, 'controller');
  });
}

function recordDeliveryCheckpoint(
  reporter: LiveCallDiagnosticsReporter,
  ledger: LiveVoiceDeliveryLedger,
): void {
  if (!ledger.assistantTurnId) return;
  reporter.record('delivery_checkpoint', {
    assistant_turn_id: ledger.assistantTurnId,
    generated_phrase_count: ledger.phrases.length,
    audio_delivered_phrase_count: ledger.audioDeliveredPhraseCount,
    audio_active_phrase_index: ledger.activePhraseIndex,
    audio_played_samples: ledger.audioPlayedSamples,
    semantic_speech_samples: ledger.semanticSpeechSamples,
    playback_sample_rate: ledger.playbackSampleRate,
    visual_delivered_text_end: ledger.visualDeliveredTextEnd,
    context_delivered_text_end: ledger.contextDeliveredTextEnd,
    delivery_policy: 'reveal_as_spoken',
  }, 'controller');
}

function stopLiveVoiceUnifiedAudio(event?: Event): void {
  const reason = event?.type === ASSISTANT_VOICE_INTERRUPT_EVENT ? 'voice-interrupt' : 'live-call-stop';
  cancelGreetingStartup(reason);
  playbackGeneration += 1;
  void stopActiveTurn(reason).finally(() => {
    if (reason !== 'voice-interrupt') void stopSharedAudioSession(reason);
  });
  stopAssistantPcmStream();
  setVoiceSpeaking(false);
}

async function stopActiveTurn(reason: string): Promise<void> {
  const turn = activeTurn;
  activeTurn = null;
  if (!turn) return;
  turn.reporter.record('turn_stop_requested', {
    reason,
    elapsed_ms: performance.now() - turn.startedAtMs,
    text_chunks: turn.textChunkCount,
    phrases: turn.phraseCount,
    assistant_turn_id: turn.assistantTurnId,
    turn_kind: turn.kind,
  }, 'controller');
  recordDeliveryCheckpoint(turn.reporter, turn.delivery);
  renderDeliveryLedger(turn.delivery, true);
  turn.abortController.abort(reason);
  const session = await turn.sessionPromise.catch(() => null);
  if (session) await cancelTurnOutputs(turn, reason, session);
  await turn.reporter.close('turn_stopped', {
    reason,
    assistant_turn_id: turn.assistantTurnId,
    visual_delivered_text_end: turn.delivery.visualDeliveredTextEnd,
    context_delivered_text_end: turn.delivery.contextDeliveredTextEnd,
    turn_kind: turn.kind,
  });
}

async function ensureSharedAudioSession(
  sessionId: string,
  voiceId: string | null,
): Promise<SharedLiveAudioSession> {
  const current = sharedAudioSession;
  if (current && current.sessionId === sessionId && current.voiceId === voiceId) {
    const session = current.session ?? await current.sessionPromise;
    if (!session.isClosed()) return current;
  }
  if (current) await stopSharedAudioSession('live_audio_session_replaced');
  const traceId = createLiveCallTraceId(`${sessionId}:audio-session`);
  const reporter = createLiveCallDiagnosticsReporter(traceId);
  reporter.record('live_audio_session_created', {
    session_id: sessionId,
    voice_id: voiceId,
    websocket_scope: 'live_session',
  }, 'controller');
  const sessionPromise = createLiveVoicePcmSession(traceId, voiceId, reporter, {
    sessionScoped: true,
    affinityKey: sessionId,
  });
  const state: SharedLiveAudioSession = {
    sessionId,
    voiceId,
    traceId,
    reporter,
    sessionPromise,
    session: null,
    nextPhraseIndex: 0,
    nextOutputOrder: 0,
  };
  sharedAudioSession = state;
  void sessionPromise.then((session) => {
    state.session = session;
  }).catch(async (error: unknown) => {
    if (sharedAudioSession === state) sharedAudioSession = null;
    await reporter.close('live_audio_session_failed', {
      error: error instanceof Error ? error.message : String(error),
    });
  });
  return state;
}

async function stopSharedAudioSession(reason: string): Promise<void> {
  const state = sharedAudioSession;
  sharedAudioSession = null;
  if (!state) return;
  const session = state.session ?? await state.sessionPromise.catch(() => null);
  if (session && !session.isClosed()) await session.stop(reason);
  await state.reporter.close('live_audio_session_closed', {
    reason,
    session_id: state.sessionId,
    phrase_count: state.nextPhraseIndex,
  });
}

async function cancelTurnOutputs(
  turn: ActiveLiveTurn,
  reason: string,
  session: LiveVoicePcmSession,
): Promise<void> {
  await Promise.all(turn.outputOwnerships.map((ownership) => session.cancelOutputItem(
    ownership.outputId,
    ownership.generationEpoch,
    reason,
  )));
}

function conversationOutputId(turn: ActiveLiveTurn, phraseIndex: number): string {
  const session = turn.sessionId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-40);
  return `conversation-${session}-g${turn.generation}-p${phraseIndex}`.slice(0, 120);
}

function createLiveTurnIds(): LiveTurnIds {
  const suffix = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return {
    userTurnId: `voice-user-turn:${suffix}`,
    speechSegmentId: `voice-segment:${suffix}`,
  };
}

function extractLiveVoiceTurnId(init: RequestInit | undefined): string | null {
  if (typeof init?.body !== 'string') return null;
  try {
    const payload = JSON.parse(init.body) as LiveVoiceRequestPayload;
    const candidate = payload.live_voice_turn_id;
    return typeof candidate === 'string' && /^voice-turn:[A-Za-z0-9_.:-]+$/.test(candidate)
      ? candidate
      : null;
  } catch {
    return null;
  }
}

function injectLiveTurnIds(init: RequestInit | undefined, ids: LiveTurnIds, signal: AbortSignal): RequestInit {
  if (typeof init?.body !== 'string') return { ...init, signal };
  try {
    const payload = JSON.parse(init.body) as Record<string, unknown>;
    return {
      ...init,
      signal,
      body: JSON.stringify({
        ...payload,
        user_turn_id: ids.userTurnId,
        speech_segment_id: ids.speechSegmentId,
      }),
    };
  } catch {
    return { ...init, signal };
  }
}

function connectAbortSignal(source: AbortSignal | null | undefined, target: AbortController): void {
  if (!source) return;
  if (source.aborted) {
    target.abort(source.reason);
    return;
  }
  source.addEventListener('abort', () => target.abort(source.reason), { once: true });
}

function captureAssistantTurnId(turn: ActiveLiveTurn, event: ChatStreamEvent | null): void {
  if (turn.kind !== 'response' || event?.type !== 'user_message') return;
  const candidate = event.message?.metadata?.assistant_turn_id;
  if (typeof candidate !== 'string' || !candidate.trim()) return;
  turn.assistantTurnId = candidate.trim();
  turn.delivery.assistantTurnId = turn.assistantTurnId;
  turn.reporter.record('assistant_turn_linked', {
    assistant_turn_id: turn.assistantTurnId,
    user_turn_id: turn.userTurnId,
  }, 'controller');
  recordDeliveryCheckpoint(turn.reporter, turn.delivery);
}

function isAutoSpeakEnabled(): boolean {
  return liveCallPresentationStore.getState().autoSpeak;
}

function setVoiceSpeaking(speaking: boolean, kind?: LiveTurnKind): void {
  liveCallPresentationStore.update({ speaking, outputKind: speaking && kind ? kind : null });
  if (reportedSpeaking !== speaking) {
    reportedSpeaking = speaking;
    emitOmnixEvent(ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, { speaking, source: 'unified-live-voice', kind: kind ?? null });
  }
}

// Chat shows it in its status line.
function setInlineStatus(message: string): void {
  liveCallPresentationStore.update({ streamStatus: message });
}

function filterLegacyAudioTextChunks(stream: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let pending = '';

  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          pending += decoder.decode();
          if (pending.trim() && parseSseBlock(pending)?.type !== 'text_chunk') controller.enqueue(encoder.encode(pending));
          controller.close();
          return;
        }
        pending += decoder.decode(value, { stream: true });
        const blocks = pending.split(/\n\n/);
        pending = blocks.pop() ?? '';
        const forwarded = blocks
          .filter((block) => parseSseBlock(block)?.type !== 'text_chunk')
          .map((block) => `${block}\n\n`)
          .join('');
        if (forwarded) {
          controller.enqueue(encoder.encode(forwarded));
          return;
        }
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n');
  if (!data) return null;
  return parseJson(chatStreamEventSchema, data) as ChatStreamEvent | null;
}

function selectedVoiceId(): string | null {
  const liveCallVoice = document.querySelector<HTMLElement>('.assistant-live-card')?.dataset.liveVoiceId?.trim();
  if (liveCallVoice) return liveCallVoice;
  const mounted = document.querySelector<HTMLSelectElement>('select[aria-label="Cloned voice"]')?.value.trim();
  if (mounted) return mounted;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(VOICE_SETTINGS_KEY) || '{}') as { voiceId?: unknown };
    return typeof parsed.voiceId === 'string' && parsed.voiceId.trim() ? parsed.voiceId.trim() : null;
  } catch {
    return null;
  }
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}
