import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react';
import type { UseFormSetValue } from 'react-hook-form';
import { omnixApiClient, type ChatSession as ApiChatSession } from '../../../api/client';
import { openStream, statusError } from '../../../api/transport';
import { liveChatSubmissionGateway } from '../workspace';
import { LIVE_SESSION_PROJECTION_FALLBACK_DELAY_MS, createPersonalityPrompt, mergeTranscript, parseChatStreamEvent, shouldFlushStreamedSpeechBuffer, unifiedLiveVoiceAudioInstalled, type AssistantSettings, type ChatMessage, type ChatbotFormValues } from './chatbotWorkspaceModel';
import type { useResponseAudio } from './useResponseAudio';
import type { VoiceTurnDiagnostics } from './useVoiceTurnDiagnostics';
import { sendChatWithAssistantContext } from '../workspace';
import { LIVE_CALL_DIAGNOSTIC_EVENT } from '../../../events/bus';

type StreamedVoiceTurnsOptions = Pick<ReturnType<typeof useResponseAudio>, 'playAssistantResponseAudio'>
  & Pick<VoiceTurnDiagnostics, 'voiceTurnPerformanceRef' | 'markVoiceTurnPerformance' | 'recordVoiceTurnDiagnostic'>
  & {
    assistantSettings: AssistantSettings;
    selectedSessionId: string | null;
    setSelectedSessionId: (sessionId: string | null) => void;
    selectedProviderId: string;
    selectedModelId: string;
    setValue: UseFormSetValue<ChatbotFormValues>;
    setAudioStatus: Dispatch<SetStateAction<string | null>>;
    setLiveTranscript: Dispatch<SetStateAction<string>>;
    setLiveInterimTranscript: Dispatch<SetStateAction<string>>;
    liveVoiceActiveRef: RefObject<boolean>;
    liveVoiceActive: boolean;
    autoSpeakResponses: boolean;
    latestAssistantMessage: ChatMessage | undefined;
  };

/**
 * Each spoken turn of a call, sent as a streamed reply: the reply is spoken
 * as it arrives, and the updated session is shown once its audio has
 * played, so React work never competes with arriving audio.
 */
export function useStreamedVoiceTurns({
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
}: StreamedVoiceTurnsOptions) {
  const queryClient = useQueryClient();
  const [spokenMessageIds, setSpokenMessageIds] = useState<Record<string, true>>({});
  const streamedSpeechQueueRef = useRef<Promise<void>>(Promise.resolve());
  const liveVoiceSubmissionInFlightRef = useRef(false);
  const pendingLiveSessionProjectionRef = useRef<ApiChatSession | null>(null);
  const pendingLiveComposerResetRef = useRef(false);
  const pendingLiveProjectionCommitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (pendingLiveProjectionCommitTimerRef.current !== null) {
      window.clearTimeout(pendingLiveProjectionCommitTimerRef.current);
      pendingLiveProjectionCommitTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const handleDiagnostic = (event: Event) => {
      const detail = (event as CustomEvent<{ event?: unknown }>).detail;
      if (detail?.event === 'turn_finished') commitPendingLiveSessionProjection();
    };
    window.addEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnostic);
    return () => window.removeEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnostic);
    // Re-subscribes when what a projection commit reads changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSpeakResponses, queryClient]);

  useEffect(() => {
    if (unifiedLiveVoiceAudioInstalled()) return;
    if (!autoSpeakResponses || !liveVoiceActive || !latestAssistantMessage || spokenMessageIds[latestAssistantMessage.id]) return;
    setSpokenMessageIds((current) => ({ ...current, [latestAssistantMessage.id]: true }));
    void playAssistantResponseAudio(latestAssistantMessage.content);
    // Speaks each new reply once, keyed by its id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSpeakResponses, latestAssistantMessage?.id, liveVoiceActive, spokenMessageIds]);

  useEffect(() => liveChatSubmissionGateway.register(async (input) => {
    if (input.sessionId !== selectedSessionId) throw new Error('live_chat_session_mismatch');
    await sendStreamingVoiceTranscript(input.text);
    // Re-registers when what a streamed turn reads changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [selectedSessionId, selectedProviderId, selectedModelId, assistantSettings, autoSpeakResponses]);

  function commitPendingLiveSessionProjection(): void {
    if (pendingLiveProjectionCommitTimerRef.current !== null) {
      window.clearTimeout(pendingLiveProjectionCommitTimerRef.current);
      pendingLiveProjectionCommitTimerRef.current = null;
    }
    const session = pendingLiveSessionProjectionRef.current;
    if (session) {
      pendingLiveSessionProjectionRef.current = null;
      if (autoSpeakResponses) markAssistantMessagesSpoken(session);
      queryClient.setQueryData(['feature', 'chatbot', 'session', session.id], session);
    }
    if (pendingLiveComposerResetRef.current) {
      pendingLiveComposerResetRef.current = false;
      setLiveTranscript('');
      setLiveInterimTranscript('');
      setValue('content', '', {
        shouldDirty: false,
        shouldTouch: false,
        shouldValidate: false,
      });
    }
  }

  function schedulePendingLiveSessionProjection(): void {
    if (!liveVoiceActiveRef.current || pendingLiveSessionProjectionRef.current === null) return;
    if (pendingLiveProjectionCommitTimerRef.current !== null) return;
    pendingLiveProjectionCommitTimerRef.current = window.setTimeout(() => {
      pendingLiveProjectionCommitTimerRef.current = null;
      if (liveVoiceActiveRef.current) commitPendingLiveSessionProjection();
    }, LIVE_SESSION_PROJECTION_FALLBACK_DELAY_MS);
  }

  async function sendStreamingVoiceTranscript(content: string): Promise<void> {
    liveVoiceSubmissionInFlightRef.current = true;
    markVoiceTurnPerformance('chatSubmitStartedAt');
    recordVoiceTurnDiagnostic('chat_submit_started', {
      input_chars: content.length,
      provider_configured: Boolean(selectedProviderId),
      model_configured: Boolean(selectedModelId),
    });
    setAudioStatus('Sending voice text.');
    const providerId = selectedProviderId || undefined;
    const modelId = selectedModelId || undefined;
    const personalityPrompt = createPersonalityPrompt(assistantSettings);
    let sessionId = selectedSessionId;
    if (!sessionId) {
      const created = await omnixApiClient.createChatSession({
        title: content.slice(0, 48) || 'New chat',
        provider_id: providerId,
        model_id: modelId,
        system_prompt: personalityPrompt || undefined,
      });
      sessionId = created.id;
      setSelectedSessionId(sessionId);
    }

    let responseText = '';
    let speechBuffer = '';
    try {
      const streamSessionId = encodeURIComponent(sessionId);
      const response = await sendChatWithAssistantContext(sessionId, {
        content,
        provider_id: providerId,
        model_id: modelId,
        coding_approval_policy: assistantSettings.codingApprovalPolicy,
        live_voice_turn_id: voiceTurnPerformanceRef.current?.turnId,
      }, (route, body) => openStream(
        route === 'context'
          ? `/api/assistant/context/chat/sessions/${streamSessionId}/messages/stream`
          : `/api/chat/sessions/${streamSessionId}/messages/stream`,
        { body },
      )).catch(statusError('Chat stream'));
      if (!response.body) throw new Error(`Chat stream failed with status ${response.status}.`);
      markVoiceTurnPerformance('chatResponseReceivedAt');
      recordVoiceTurnDiagnostic('chat_response_opened', { status: response.status });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const events = pending.split(/\n\n/);
        pending = events.pop() ?? '';
        for (const eventText of events) {
          const event = parseChatStreamEvent(eventText);
          if (!event) continue;
          if (event.type === 'error') throw new Error(typeof event.message === 'string' ? event.message : 'Chat stream failed.');
          if (event.type === 'text_chunk' && typeof event.text === 'string') {
            const firstChunk = voiceTurnPerformanceRef.current?.llmFirstChunkReceivedAt === undefined;
            markVoiceTurnPerformance('llmFirstChunkReceivedAt');
            if (firstChunk) {
              recordVoiceTurnDiagnostic('llm_first_text_chunk_received', {
                text_chunk_chars: event.text.length,
              });
            }
            responseText = mergeTranscript(responseText, event.text);
            speechBuffer = mergeTranscript(speechBuffer, event.text);
            // Updating workspace state for each live token re-renders the full
            // conversation projection (often hundreds of messages) while PCM
            // frames are arriving. The call controller already owns the live
            // status surface, so keep this text-mode update off the hot path.
            if (!liveVoiceActiveRef.current) setAudioStatus('Assistant response streaming.');
            if (autoSpeakResponses && shouldFlushStreamedSpeechBuffer(speechBuffer)) {
              queueStreamedAssistantAudio(speechBuffer);
              speechBuffer = '';
            }
          }
          if (event.type === 'session' && event.session) {
            if (liveVoiceActiveRef.current) {
              // A full live session can contain hundreds of messages. Project it
              // after playback so React work cannot block arriving PCM frames.
              pendingLiveSessionProjectionRef.current = event.session;
            } else {
              if (autoSpeakResponses) markAssistantMessagesSpoken(event.session);
              queryClient.setQueryData(['feature', 'chatbot', 'session', event.session.id], event.session);
            }
          }
        }
      }
      if (liveVoiceActiveRef.current) {
        pendingLiveComposerResetRef.current = true;
        // The audio controller normally commits this projection at
        // turn_finished. Keep the chat screen correct even when that
        // controller is unavailable or audio completion is interrupted.
        schedulePendingLiveSessionProjection();
      } else {
        setLiveTranscript('');
        setLiveInterimTranscript('');
        setValue('content', '', { shouldDirty: false, shouldTouch: false, shouldValidate: false });
      }
      if (autoSpeakResponses && speechBuffer.trim()) queueStreamedAssistantAudio(speechBuffer);
      markVoiceTurnPerformance('llmCompletedAt');
      recordVoiceTurnDiagnostic('llm_stream_completed', {
        response_chars: responseText.length,
      });
      if (!liveVoiceActiveRef.current) {
        await queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot'] });
      }
      if (!liveVoiceActiveRef.current) {
        setAudioStatus(responseText ? 'Response ready.' : 'Voice text sent.');
      }
    } catch (error) {
      recordVoiceTurnDiagnostic('chat_stream_failed', {
        error_name: error instanceof Error ? error.name : 'unknown',
        error_code: error instanceof Error && error.message.trim()
          ? error.message.trim().slice(0, 240)
          : 'live_chat_stream_failed',
      });
      setAudioStatus(error instanceof Error ? error.message : 'Voice text stream failed.');
    } finally {
      liveVoiceSubmissionInFlightRef.current = false;
    }
  }

  function queueStreamedAssistantAudio(text: string): void {
    if (unifiedLiveVoiceAudioInstalled()) return;
    streamedSpeechQueueRef.current = streamedSpeechQueueRef.current
      .catch(() => undefined)
      .then(() => playAssistantResponseAudio(text));
  }

  function markAssistantMessagesSpoken(session: ApiChatSession): void {
    const assistantMessageIds = session.messages
      ?.filter((message) => message.role === 'assistant')
      .map((message) => message.id)
      .filter(Boolean) ?? [];
    if (!assistantMessageIds.length) return;
    setSpokenMessageIds((current) => {
      const next = { ...current };
      for (const messageId of assistantMessageIds) next[messageId] = true;
      return next;
    });
  }

  return { liveVoiceSubmissionInFlightRef, commitPendingLiveSessionProjection, sendStreamingVoiceTranscript };
}
