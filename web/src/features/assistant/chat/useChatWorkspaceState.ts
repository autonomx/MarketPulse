import { useMemo, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { createAssistantWorkspaceRuntimeConfig, startLiveVoiceCall, toggleLiveVoiceCall, useAssistantPcmStream, useLiveCallPresentation, useLiveVoiceTranscript } from '../workspace';
import { AssistantView, ChatbotFormValues, dedicatedLiveVoiceControllerInstalled, getLatestAssistantMessage, getSpeechRecognitionConstructor, personalityLabel, readAssistantToolReturn, selectedModelLabel, selectedProviderLabel, voiceLabelForId } from './chatbotWorkspaceModel';
import { useChatLayout } from './useChatLayout';
import { useChatAttachments } from './useChatAttachments';
import { useVoiceTurnDiagnostics } from './useVoiceTurnDiagnostics';
import { useResponseAudio } from './useResponseAudio';
import { useChatSessions } from './useChatSessions';
import { useChatJob } from './useChatJob';
import { useLiveCallRuntime } from './useLiveCallRuntime';
import { useSendChatMessage } from './useSendChatMessage';
import { useLiveVoiceCall } from './useLiveVoiceCall';
import { selectFreshChatSession } from './chatMessageModel';
import { useAssistantSettings } from './useAssistantSettings';
import { useChatActivity } from './useChatActivity';
import { useStickToLatestMessage } from './useStickToLatestMessage';
import { liveChatMessagesFor } from './chatMessageModel';
import { useChatCatalog } from './useChatCatalog';
import { useChatMessageActions } from './useChatMessageActions';

/** Wires Chat's hooks together: layout, sessions, sending, the live call, audio and message actions. */
export function useChatWorkspaceState() {
  const assistantToolReturn = useMemo(() => readAssistantToolReturn(), []);

  const {
    activeView, setActiveView,
    isChatFullscreen, setIsChatFullscreen,
    activeUtilityPanel, setActiveUtilityPanel,
    isAssistantSidebarMinimized, setIsAssistantSidebarMinimized,
    isSidePanelMinimized, setIsSidePanelMinimized,
  } = useChatLayout(assistantToolReturn.toolId);

  const {
    pastedChatImages, setPastedChatImages,
    pastedChatTextFile, setPastedChatTextFile,
    chatImageError, setChatImageError,
    handleComposerPaste,
  } = useChatAttachments();

  const [audioStatus, setAudioStatus] = useState<string | null>(null);

  const {
    selectedSessionId, setSelectedSessionId,
    sessionsQuery, sessionQuery,
    selectedSessionSummary,
    chatSessions, sessionsLoading, sessionsError,
    activeSessionLoading, activeSessionError,
    deleteSessionMutation,
    selectSidebarSession,
  } = useChatSessions({ setActiveView, setAudioStatus });

  const { setActiveChatJobId, chatJobError, setChatJobError, chatJobQuery, chatJobInProgress } = useChatJob(selectedSessionId);

  const liveVoiceActiveRef = useRef(false);

  const diagnostics = useVoiceTurnDiagnostics();

  const runtimeConfig = useMemo(() => createAssistantWorkspaceRuntimeConfig(), []);

  const { assistantSettings, settingsStatus, updateAssistantSettings, resetAssistantSettings } = useAssistantSettings(runtimeConfig, activeView);

  const { register, handleSubmit, reset, setValue, getValues, watch, control, formState: { errors } } = useForm<ChatbotFormValues>({
    defaultValues: { content: '', providerId: runtimeConfig.defaultProviderId ?? '', modelId: runtimeConfig.defaultModelId ?? '' },
  });

  const { sendMutation, sendChatMessage, quickSearchProgress, pendingUserMessage } = useSendChatMessage({
    runtimeConfig,
    assistantSettings,
    selectedSessionId,
    setSelectedSessionId,
    pastedChatImages,
    pastedChatTextFile,
    setPastedChatImages,
    setPastedChatTextFile,
    setChatImageError,
    setActiveChatJobId,
    setChatJobError,
    reset,
    onSent: () => {
      setLiveTranscript('');
      setLiveInterimTranscript('');
    },
    recordActivityEvent: (event, filter) => recordActivityEvent(event, filter),
    markVoiceTurnPerformance: diagnostics.markVoiceTurnPerformance,
  });

  const selectedProviderId = watch('providerId');

  const selectedModelId = watch('modelId');

  const { providerPayload, chatProviders, chatModels, interaction, selectedCharacter, voiceProfiles, voiceProfilesLoading } = useChatCatalog({
    activeView,
    sessionsPending: sessionsQuery.isPending,
    selectedSessionId,
    selectedSessionSummary,
    selectedProviderId,
  });

  const { liveCallRuntime, setLiveCallRuntime, liveCallRuntimeRef } = useLiveCallRuntime({
    selectedSessionId,
    selectedSessionSummary,
    interaction,
    liveVoiceActiveRef,
  });

  const activeSession = selectFreshChatSession(sendMutation.data?.session, sessionQuery.data);

  const activeMessageCount = activeSession?.messages?.length ?? 0;

  const providerLabel = selectedProviderLabel(providerPayload, selectedProviderId);

  const modelLabel = selectedModelLabel(providerPayload, selectedModelId);

  const recentMessages = activeSession?.messages?.slice(-4) ?? [];

  const sessionMessages = activeSession?.messages;

  const displayedMessages = useMemo(
    () => pendingUserMessage ? [...(sessionMessages ?? []), pendingUserMessage] : sessionMessages ?? [],
    [sessionMessages, pendingUserMessage],
  );

  const displayedSessionId = activeSession?.id ?? selectedSessionId ?? 'pending-session';

  const { messagesContainerRef, messagesEndRef, handleMessagesScroll } = useStickToLatestMessage(displayedSessionId, displayedMessages.length);

  const liveVoiceTranscript = useLiveVoiceTranscript();

  const livePresentation = useLiveCallPresentation();

  const pcmStream = useAssistantPcmStream();

  const liveChatMessages = useMemo(() => liveChatMessagesFor(displayedMessages, liveVoiceTranscript), [displayedMessages, liveVoiceTranscript]);

  const liveCardRef = useRef<HTMLElement | null>(null);

  const { toolExecutionRows, refreshActivityPanel, recordActivityEvent } = useChatActivity(runtimeConfig, activeSession);

  const latestAssistantMessage = getLatestAssistantMessage(activeSession?.messages ?? []);

  const enabledToolCount = runtimeConfig.features.toolExecution ? Math.max(toolExecutionRows.length, 3) : 0;

  const liveCharacterName = liveCallRuntime?.interaction_mode === 'character'
    ? liveCallRuntime.display_name
    : selectedCharacter?.display_name;

  const liveIdentityLabel = liveCharacterName
    ? `Character Mode · ${liveCharacterName}`
    : 'System Assistant';

  const configuredVoiceId = assistantSettings.voiceId || runtimeConfig.ttsVoice || '';

  const activeVoiceId = liveCallRuntime?.voice_asset_id || configuredVoiceId;

  const activeVoiceLabel = voiceLabelForId(activeVoiceId, voiceProfiles);

  const selectedPersonalityLabel = personalityLabel(assistantSettings.personalityId);

  const speechInputLabel = runtimeConfig.sttServiceUrl ? 'STT service recording' : getSpeechRecognitionConstructor() ? 'Browser speech-to-text' : 'No STT input configured';

  const ttsOutputLabel = `${runtimeConfig.ttsServiceUrl ? 'TTS service' : 'Voice Studio TTS job'}${activeVoiceLabel ? ` · ${activeVoiceLabel}` : ''}`;

  const audio = useResponseAudio({
    runtimeConfig,
    assistantSettings,
    liveCallRuntimeRef,
    voiceProfiles,
    activeVoiceId,
    activeVoiceLabel,
    activeSessionId: activeSession?.id,
    selectedProviderId,
    selectedModelId,
    selectedPersonalityLabel,
    setAudioStatus,
    ...diagnostics,
  });

  const { isAssistantSpeaking, playAssistantResponseAudio, currentLiveCallVoiceId } = audio;

  const {
    liveVoiceActive, callElapsedMs, voiceCaptureMode, liveDraftText, visibleVoiceTranscriptMessages,
    autoSpeakResponses, setAutoSpeakResponses, setLiveTranscript, setLiveInterimTranscript,
    startLiveCall, stopLiveCall, startVoiceInput, clearVoiceTranscript, sendVoiceTranscript, submitVoiceTranscriptContent,
  } = useLiveVoiceCall({
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
    ...audio,
    ...diagnostics,
  });

  const { messageActions, assistantMessageFeedback, openMessageActionMenuId } = useChatMessageActions({
    setAudioStatus,
    ...audio,
    streamingMessageId: pcmStream.messageId,
    continueFrom: (message) => applySuggestedPrompt(`Continue from: ${message.content.slice(0, 120)}`),
    openTools: () => { showAssistantView('tools'); setActiveUtilityPanel('tools'); },
  });

  function applySuggestedPrompt(prompt: string): void {
    setActiveView('chats');
    setValue('content', prompt, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
  }

  function showAssistantView(view: AssistantView): void {
    setActiveView(view);
    if (view === 'voice') setActiveUtilityPanel('voice');
    if (view === 'tools') setActiveUtilityPanel('tools');
  }

  // The call controls start or end both the capture controller's microphone
  // stream (when installed) and Chat's call session.
  function toggleLiveCallFromControls(): void {
    const card = liveCardRef.current;
    if (card && dedicatedLiveVoiceControllerInstalled()) toggleLiveVoiceCall(card);
    void (liveVoiceActive ? stopLiveCall() : startLiveCall());
  }

  function sendFromLiveChat(text: string): boolean {
    const content = text.trim();
    if (!content) return false;
    setValue('content', content, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
    void handleSubmit(submitComposerMessage)();
    return true;
  }

  function startLiveCallFromControls(): Promise<void> {
    const card = liveCardRef.current;
    if (card && dedicatedLiveVoiceControllerInstalled()) startLiveVoiceCall(card);
    return startLiveCall();
  }

  function submitComposerMessage(values: ChatbotFormValues): void {
    if (liveVoiceActiveRef.current) {
      void submitVoiceTranscriptContent(values.content, { manual: true });
      return;
    }
    sendChatMessage(values);
  }

  return {
    assistantToolReturn, activeView, setActiveView, isChatFullscreen, setIsChatFullscreen, activeUtilityPanel,
    setActiveUtilityPanel, isAssistantSidebarMinimized, setIsAssistantSidebarMinimized, isSidePanelMinimized,
    setIsSidePanelMinimized, pastedChatImages, setPastedChatImages, pastedChatTextFile, setPastedChatTextFile,
    chatImageError, setChatImageError, handleComposerPaste, audioStatus, setAudioStatus, selectedSessionId,
    setSelectedSessionId, sessionsQuery, sessionQuery, selectedSessionSummary, chatSessions, sessionsLoading,
    sessionsError, activeSessionLoading, activeSessionError, deleteSessionMutation, selectSidebarSession,
    setActiveChatJobId, chatJobError, setChatJobError, chatJobQuery, chatJobInProgress, liveVoiceActiveRef,
    diagnostics, runtimeConfig, assistantSettings, settingsStatus, updateAssistantSettings, resetAssistantSettings,
    register, handleSubmit, reset, setValue, getValues, watch, control, errors, sendMutation, sendChatMessage,
    quickSearchProgress, pendingUserMessage, selectedProviderId, selectedModelId, providerPayload, chatProviders,
    chatModels, interaction, selectedCharacter, voiceProfiles, voiceProfilesLoading, liveCallRuntime,
    setLiveCallRuntime, liveCallRuntimeRef, activeSession, activeMessageCount, providerLabel, modelLabel,
    recentMessages, sessionMessages, displayedMessages, displayedSessionId, messagesContainerRef, messagesEndRef,
    handleMessagesScroll, liveVoiceTranscript, livePresentation, pcmStream, liveChatMessages, liveCardRef,
    toolExecutionRows, refreshActivityPanel, recordActivityEvent, latestAssistantMessage, enabledToolCount,
    liveCharacterName, liveIdentityLabel, configuredVoiceId, activeVoiceId, activeVoiceLabel,
    selectedPersonalityLabel, speechInputLabel, ttsOutputLabel, audio, isAssistantSpeaking,
    playAssistantResponseAudio, currentLiveCallVoiceId, liveVoiceActive, callElapsedMs, voiceCaptureMode,
    liveDraftText, visibleVoiceTranscriptMessages, autoSpeakResponses, setAutoSpeakResponses, setLiveTranscript,
    setLiveInterimTranscript, startLiveCall, stopLiveCall, startVoiceInput, clearVoiceTranscript,
    sendVoiceTranscript, submitVoiceTranscriptContent, messageActions, assistantMessageFeedback,
    openMessageActionMenuId, applySuggestedPrompt, showAssistantView, toggleLiveCallFromControls, sendFromLiveChat,
    startLiveCallFromControls, submitComposerMessage,
  };
}
