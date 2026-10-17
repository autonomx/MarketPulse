import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react';
import type { UseFormGetValues, UseFormSetValue } from 'react-hook-form';
import { omnixApiClient } from '../../../api/client';
import { liveCallPresentationStore, liveVoiceTranscriptStore, type AssistantWorkspaceRuntimeConfig } from '../workspace';
import { characterClient, type CharacterLiveCallRuntime, type LiveCallSpeechStyle } from './characterClient';
import { CALL_TIMER_TICK_MS, LIVE_VOICE_AUTO_SEND_DELAY_MS, createPersonalityPrompt, dedicatedLiveVoiceControllerInstalled, liveVoiceSubmissionKey, type AssistantSettings, type ChatMessage, type ChatbotFormValues, type UtilityPanel } from './chatbotWorkspaceModel';
import { useBrowserVoiceInput } from './useBrowserVoiceInput';
import type { useResponseAudio } from './useResponseAudio';
import type { VoiceTurnDiagnostics } from './useVoiceTurnDiagnostics';
import { useStreamedVoiceTurns } from './useStreamedVoiceTurns';
import { ASSISTANT_LIVE_VOICE_STOP_EVENT, emitOmnixEvent } from '../../../events/bus';
import { startTicker } from '../../../shared/timers';

type LiveVoiceCallOptions = Pick<ReturnType<typeof useResponseAudio>, 'playAssistantResponseAudio' | 'stopAssistantResponseAudio' | 'currentLiveCallSpeechStyle'>
  & Pick<VoiceTurnDiagnostics, 'voiceTurnPerformanceRef' | 'markVoiceTurnPerformance' | 'recordVoiceTurnDiagnostic'>
  & {
    runtimeConfig: AssistantWorkspaceRuntimeConfig;
    assistantSettings: AssistantSettings;
    selectedSessionId: string | null;
    setSelectedSessionId: (sessionId: string | null) => void;
    selectedProviderId: string;
    selectedModelId: string;
    setValue: UseFormSetValue<ChatbotFormValues>;
    getValues: UseFormGetValues<ChatbotFormValues>;
    setAudioStatus: Dispatch<SetStateAction<string | null>>;
    setActiveUtilityPanel: (panel: UtilityPanel) => void;
    liveVoiceActiveRef: RefObject<boolean>;
    liveCallRuntimeRef: RefObject<CharacterLiveCallRuntime | null>;
    setLiveCallRuntime: (runtime: CharacterLiveCallRuntime | null) => void;
    latestAssistantMessage: ChatMessage | undefined;
    recentMessages: ChatMessage[];
    /** Sends a typed message as a queued reply job (outside a call). */
    sendChatMessage: (values: ChatbotFormValues) => void;
  };

/**
 * Chat's live voice call: starting and ending it, sending each spoken turn
 * as a streamed reply, speaking replies, and holding the session projection
 * back until the reply's audio has played.
 */
export function useLiveVoiceCall({
  runtimeConfig,
  assistantSettings,
  selectedSessionId,
  setSelectedSessionId,
  selectedProviderId,
  selectedModelId,
  setValue,
  getValues,
  setAudioStatus,
  setActiveUtilityPanel,
  liveVoiceActiveRef,
  liveCallRuntimeRef,
  setLiveCallRuntime,
  latestAssistantMessage,
  recentMessages,
  sendChatMessage,
  playAssistantResponseAudio,
  stopAssistantResponseAudio,
  currentLiveCallSpeechStyle,
  voiceTurnPerformanceRef,
  markVoiceTurnPerformance,
  recordVoiceTurnDiagnostic,
}: LiveVoiceCallOptions) {
  const queryClient = useQueryClient();
  const [callStartedAt, setCallStartedAt] = useState<number | null>(null);
  const [callElapsedMs, setCallElapsedMs] = useState(0);
  const [clearedVoiceTranscriptMessageIds, setClearedVoiceTranscriptMessageIds] = useState<Record<string, true>>({});
  const [autoSpeakResponses, setAutoSpeakResponses] = useState(true);
  const liveVoiceAutoSendTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSubmittedVoiceTextRef = useRef('');
  const {
    voiceCaptureMode, setVoiceCaptureMode,
    liveTranscript, setLiveTranscript,
    liveInterimTranscript, setLiveInterimTranscript,
    startVoiceInput,
    stopVoiceInput,
  } = useBrowserVoiceInput({
    runtimeConfig,
    setValue,
    setAudioStatus,
    setActiveUtilityPanel,
    scheduleLiveVoiceAutoSend,
    clearLiveVoiceAutoSendTimer,
  });

  const liveVoiceActive = callStartedAt !== null;
  const { liveVoiceSubmissionInFlightRef, commitPendingLiveSessionProjection, sendStreamingVoiceTranscript } = useStreamedVoiceTurns({
    assistantSettings,
    selectedSessionId,
    setSelectedSessionId,
    selectedProviderId,
    selectedModelId,
    setValue,
    setAudioStatus,
    setLiveTranscript,
    setLiveInterimTranscript,
    liveVoiceActiveRef,
    liveVoiceActive,
    autoSpeakResponses,
    latestAssistantMessage,
    playAssistantResponseAudio,
    voiceTurnPerformanceRef,
    markVoiceTurnPerformance,
    recordVoiceTurnDiagnostic,
  });
  const liveDraftText = [liveTranscript, liveInterimTranscript].filter(Boolean).join(' ').trim();
  const visibleVoiceTranscriptMessages = recentMessages.filter((message) => !clearedVoiceTranscriptMessageIds[message.id]);

  useEffect(() => {
    liveCallPresentationStore.update({ autoSpeak: autoSpeakResponses });
    return () => liveCallPresentationStore.update({ autoSpeak: false });
  }, [autoSpeakResponses]);

  useEffect(() => {
    if (callStartedAt === null) {
      setCallElapsedMs(0);
      return undefined;
    }
    const updateElapsed = () => setCallElapsedMs(Date.now() - callStartedAt);
    updateElapsed();
    return startTicker(updateElapsed, CALL_TIMER_TICK_MS);
  }, [callStartedAt]);

  useEffect(() => {
    return () => {
      liveVoiceActiveRef.current = false;
      clearLiveVoiceAutoSendTimer();
      dispatchLiveVoiceStop();
      stopVoiceInput();
      stopAssistantResponseAudio();
    };
    // On unmount the call ends: the cleanup reads refs and stops audio and the microphone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startLiveCall(): Promise<void> {
    if (callStartedAt !== null) return;
    setActiveUtilityPanel('voice');
    liveVoiceActiveRef.current = true;
    setCallStartedAt(Date.now());
    setCallElapsedMs(0);
    setAudioStatus('Live voice call started.');
    try {
      let sessionId = selectedSessionId;
      let createdSystemSession = false;
      if (!sessionId) {
        const personalityPrompt = createPersonalityPrompt(assistantSettings);
        const created = await omnixApiClient.createChatSession({
          title: 'Live voice call',
          provider_id: selectedProviderId || undefined,
          model_id: selectedModelId || undefined,
          system_prompt: personalityPrompt || undefined,
        });
        sessionId = created.id;
        createdSystemSession = true;
        setSelectedSessionId(sessionId);
      }
      let runtime: CharacterLiveCallRuntime;
      try {
        runtime = await characterClient.liveCallRuntime(sessionId);
      } catch (runtimeError) {
        if (!createdSystemSession) throw runtimeError;
        runtime = neutralSystemLiveCallRuntime(sessionId, assistantSettings.voiceId || runtimeConfig.ttsVoice || null, currentLiveCallSpeechStyle());
        console.info('[Omnix Voice Perf] live-call runtime endpoint unavailable for new system session; using neutral fallback', {
          sessionId,
          reason: runtimeError instanceof Error ? runtimeError.message : 'runtime unavailable',
        });
      }
      liveCallRuntimeRef.current = runtime;
      setLiveCallRuntime(runtime);
      console.info('[Omnix Voice Perf] live-call runtime preloaded', {
        sessionId,
        interactionMode: runtime.interaction_mode,
        characterId: runtime.character_id,
        profileVersion: runtime.character_profile_version,
        identityHash: runtime.effective_identity_hash,
        voiceAssetId: runtime.voice_asset_id,
        memoryRecordCount: runtime.preload.memory_record_count,
        preloadMs: runtime.preload.preload_ms,
      });
      setAudioStatus(`${runtime.display_name} call ready · preload ${Math.round(runtime.preload.preload_ms)}ms`);
      if (runtime.greeting.trim()) await playAssistantResponseAudio(runtime.greeting);
      if (dedicatedLiveVoiceControllerInstalled()) {
        setVoiceCaptureMode('listening');
        setAudioStatus(`${runtime.display_name} call ready · streaming microphone active`);
      } else {
        await startVoiceInput();
      }
    } catch (error) {
      liveVoiceActiveRef.current = false;
      liveCallRuntimeRef.current = null;
      setLiveCallRuntime(null);
      setCallStartedAt(null);
      setCallElapsedMs(0);
      setAudioStatus(error instanceof Error ? error.message : 'Live-call preload failed.');
    }
  }

  function stopLiveCall(): void {
    if (callStartedAt === null) return;
    liveVoiceActiveRef.current = false;
    dispatchLiveVoiceStop();
    stopVoiceInput();
    stopAssistantResponseAudio();
    commitPendingLiveSessionProjection();
    setCallStartedAt(null);
    setCallElapsedMs(0);
    liveCallRuntimeRef.current = null;
    setLiveCallRuntime(null);
    setAudioStatus('Live voice call ended.');
    // Each streamed response already installs its authoritative session in the
    // query cache. Reconcile list/interaction projections once the latency-
    // sensitive call is over instead of competing with browser PCM delivery.
    void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot'] });
  }

  function clearVoiceTranscript(): void {
    clearLiveVoiceAutoSendTimer();
    lastSubmittedVoiceTextRef.current = '';
    setLiveTranscript('');
    setLiveInterimTranscript('');
    setClearedVoiceTranscriptMessageIds((current) => {
      const next = { ...current };
      recentMessages.forEach((message) => {
        next[message.id] = true;
      });
      return next;
    });
    setValue('content', '', { shouldDirty: true, shouldTouch: true, shouldValidate: true });
    liveVoiceTranscriptStore.clear();
    setAudioStatus('Voice transcript cleared.');
  }

  function sendVoiceTranscript(): void {
    const content = (liveDraftText || getValues('content') || '').trim();
    void submitVoiceTranscriptContent(content, { manual: true });
  }

  async function submitVoiceTranscriptContent(content: string, { manual = false }: { manual?: boolean } = {}): Promise<void> {
    const trimmed = content.trim();
    if (manual) clearLiveVoiceAutoSendTimer();
    if (!trimmed) {
      setAudioStatus('Speak or type a message before sending voice text.');
      return;
    }
    const submissionKey = liveVoiceSubmissionKey(trimmed);
    if (liveVoiceSubmissionInFlightRef.current || submissionKey === lastSubmittedVoiceTextRef.current) return;
    lastSubmittedVoiceTextRef.current = submissionKey;
    if (liveVoiceActiveRef.current) {
      setLiveTranscript('');
      setLiveInterimTranscript('');
      setValue('content', '', { shouldDirty: false, shouldTouch: false, shouldValidate: false });
      await sendStreamingVoiceTranscript(trimmed);
      return;
    }
    sendChatMessage({ content: trimmed, providerId: selectedProviderId, modelId: selectedModelId });
  }

  function scheduleLiveVoiceAutoSend(content: string): void {
    if (!liveVoiceActiveRef.current) return;
    clearLiveVoiceAutoSendTimer();
    liveVoiceAutoSendTimerRef.current = window.setTimeout(() => {
      liveVoiceAutoSendTimerRef.current = null;
      void submitVoiceTranscriptContent(content);
    }, LIVE_VOICE_AUTO_SEND_DELAY_MS);
  }

  function clearLiveVoiceAutoSendTimer(): void {
    if (liveVoiceAutoSendTimerRef.current === null) return;
    clearTimeout(liveVoiceAutoSendTimerRef.current);
    liveVoiceAutoSendTimerRef.current = null;
  }

  function dispatchLiveVoiceStop(): void {
    emitOmnixEvent(ASSISTANT_LIVE_VOICE_STOP_EVENT);
  }

  return {
    liveVoiceActive,
    callElapsedMs,
    voiceCaptureMode,
    liveDraftText,
    visibleVoiceTranscriptMessages,
    autoSpeakResponses, setAutoSpeakResponses,
    setLiveTranscript, setLiveInterimTranscript,
    startLiveCall,
    stopLiveCall,
    startVoiceInput,
    clearVoiceTranscript,
    sendVoiceTranscript,
    submitVoiceTranscriptContent,
  };
}

/** The runtime of a new system session whose runtime endpoint is unavailable: no character, memory or avatar. */
function neutralSystemLiveCallRuntime(sessionId: string, voiceAssetId: string | null, speechStyle: LiveCallSpeechStyle): CharacterLiveCallRuntime {
  return {
    session_id: sessionId,
    interaction_mode: 'system',
    display_name: 'System Assistant',
    character_id: null,
    character_profile_version: null,
    effective_identity_hash: null,
    voice_asset_id: voiceAssetId,
    voice_speaker_id: null,
    avatar_pack: null,
    greeting: '',
    speech_style: speechStyle,
    read_memory: false,
    write_memory: false,
    shared_memory_access: 'none',
    memory_snapshot_id: null,
    preload: {
      profile_loaded: false,
      voice_resolved: Boolean(voiceAssetId),
      voice_error: null,
      avatar_pack_loaded: false,
      memory_snapshot_loaded: false,
      memory_record_count: 0,
      preload_ms: 0,
      resolved_at: new Date().toISOString(),
    },
  };
}
