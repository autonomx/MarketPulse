import { VirtualList } from '../../../design/VirtualList';
import { ChatIdentityModeControl } from './ChatIdentityModeControl';
import { ResearchProgressCard } from './ResearchProgressCard';
import { ChatMessageItem } from './ChatMessageItem';
import { chatbotSubmitErrorMessage, codingApprovalOptions, voiceProfileId, voiceProfileLabel } from './chatbotWorkspaceModel';
import { ChatHeader } from './ChatFrame';
import { ChatLiveVoiceCard } from './ChatLiveVoiceCard';
import { ChatComposer } from './ChatComposer';
import { AssistantWorkspaceView } from './AssistantWorkspaceView';
import type { ChatWorkspaceModel } from './useChatWorkspace';

/** The Chats view: the header, the transcript, research progress and the composer. */
export function ChatConversation({ ws }: { ws: ChatWorkspaceModel }) {
  const {
    activeMessageCount, activeSessionError, activeSessionLoading, applySuggestedPrompt, assistantMessageFeedback,
    assistantSettings, chatImageError, chatJobInProgress, chatModels, chatProviders, displayedMessages,
    displayedSessionId, enabledToolCount, errors, handleComposerPaste, handleMessagesScroll, handleSubmit,
    isChatFullscreen, latestAssistantMessage, liveVoiceActive, messageActions, messagesContainerRef, messagesEndRef,
    openMessageActionMenuId, pastedChatImages, pastedChatTextFile, pcmStream, playAssistantResponseAudio,
    quickSearchProgress, refreshActivityPanel, register, runtimeConfig, selectedPersonalityLabel, selectedSessionId,
    sendMutation, sessionsLoading, setActiveUtilityPanel, setChatImageError, setIsChatFullscreen,
    setPastedChatImages, setPastedChatTextFile, setSelectedSessionId, showAssistantView, submitComposerMessage,
    toggleLiveCallFromControls, ttsOutputLabel, updateAssistantSettings, voiceProfiles,
  } = ws;
  return (
    <>
      <ChatHeader fullscreen={isChatFullscreen} onToggleFullscreen={() => setIsChatFullscreen((current) => !current)}>
        <ChatIdentityModeControl
          sessionId={selectedSessionId}
          systemVoiceId={assistantSettings.voiceId}
          defaultVoiceLabel={runtimeConfig.ttsVoice ? `Default (${runtimeConfig.ttsVoice})` : 'Default voice'}
          voiceOptions={voiceProfiles.map((asset) => ({
            assetId: asset.id,
            value: voiceProfileId(asset),
            label: voiceProfileLabel(asset),
          }))}
          onSystemVoiceChange={(voiceId) => updateAssistantSettings({ ...assistantSettings, voiceId })}
          onSessionResolved={(sessionId) => setSelectedSessionId(sessionId)}
          onOpenSystemSettings={() => showAssistantView('settings')}
          onOpenCharacterSettings={() => showAssistantView('characters')}
        />
      </ChatHeader>
      <div className="assistant-chat-messages" role="log" aria-live="polite" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
        {displayedMessages.length ? <VirtualList items={displayedMessages} getKey={(message) => message.id} scrollRef={messagesContainerRef} estimateSize={180} renderItem={(message) => (
          <ChatMessageItem
            key={message.id}
            message={message}
            sessionId={displayedSessionId}
            feedback={assistantMessageFeedback[message.id]}
            menuOpen={openMessageActionMenuId === message.id}
            streaming={pcmStream.messageId === message.id}
            actions={messageActions}
          />
        )} /> : activeSessionLoading || sessionsLoading ? <div className="platform-empty" role="status">Loading chat messages...</div> : activeSessionError ? <div className="platform-empty" role="status">Chat messages failed to load.</div> : <div className="platform-empty" role="status">No chat messages yet.</div>}
        {quickSearchProgress ? <div className="assistant-quick-search-progress" role="status" aria-live="polite"><span className="assistant-quick-search-icon" aria-hidden="true">◎</span><span>Searching {quickSearchProgress}</span></div> : null}
        {sendMutation.isPending || chatJobInProgress ? <div className="assistant-thinking-indicator" role="status" aria-live="polite"><span className="assistant-thinking-orb" aria-hidden="true" /><span className="assistant-thinking-label">Thinking<span className="assistant-thinking-dots" aria-hidden="true"><i /><i /><i /></span></span></div> : null}
        <div ref={messagesEndRef} aria-hidden="true" />
      </div>
      <ResearchProgressCard />
      <ChatComposer
        onSubmit={handleSubmit(submitComposerMessage)}
        register={register}
        errors={errors}
        chatProviders={chatProviders}
        chatModels={chatModels}
        onApplyPrompt={applySuggestedPrompt}
        ttsOutputLabel={ttsOutputLabel}
        canPlayLatestReply={Boolean(latestAssistantMessage)}
        onPlayLatestReply={() => void playAssistantResponseAudio(latestAssistantMessage?.content ?? '')}
        personalityLabel={selectedPersonalityLabel}
        permissionsLabel={codingApprovalOptions.find((option) => option.value === assistantSettings.codingApprovalPolicy)?.label}
        toolsLabel={runtimeConfig.features.toolExecution ? `${enabledToolCount} Active` : 'Off'}
        contextLabel={activeMessageCount > 0 ? 'Project Brief' : 'Ready'}
        onOpenSettings={() => showAssistantView('settings')}
        onOpenTools={() => { showAssistantView('tools'); setActiveUtilityPanel('tools'); }}
        onRefreshContext={refreshActivityPanel}
        pastedChatImages={pastedChatImages}
        pastedChatTextFile={pastedChatTextFile}
        chatImageError={chatImageError}
        onRemoveImage={(index) => {
          setPastedChatImages((current) => current.filter((_, candidateIndex) => candidateIndex !== index));
          setChatImageError(null);
        }}
        onRemoveTextFile={() => {
          setPastedChatTextFile(null);
          setChatImageError(null);
        }}
        onPaste={handleComposerPaste}
        liveVoiceActive={liveVoiceActive}
        onToggleCall={toggleLiveCallFromControls}
        sendPending={sendMutation.isPending}
        chatJobInProgress={chatJobInProgress}
      />
    </>
  );
}

/** The non-chat views: voice, tools, characters, memory and settings. */
export function ChatOtherViews({ ws }: { ws: ChatWorkspaceModel }) {
  const {
    activeView, assistantSettings, assistantToolReturn, chatProviders, enabledToolCount, modelLabel, providerLabel,
    resetAssistantSettings, runtimeConfig, selectedSessionId, setActiveUtilityPanel, setSelectedSessionId,
    settingsStatus, speechInputLabel, startLiveCallFromControls, toolExecutionRows, ttsOutputLabel,
    updateAssistantSettings, voiceProfiles, voiceProfilesLoading,
  } = ws;
  if (activeView === 'chats' || activeView === 'live') return null;
  return (
    <>
      <AssistantWorkspaceView
        activeView={activeView}
        assistantSettings={assistantSettings}
        chatProviders={chatProviders}
        enabledToolCount={enabledToolCount}
        initialToolConnectionMessage={assistantToolReturn.message}
        initialToolId={assistantToolReturn.toolId}
        modelLabel={modelLabel}
        providerLabel={providerLabel}
        runtimeConfig={runtimeConfig}
        settingsStatus={settingsStatus}
        speechInputLabel={speechInputLabel}
        toolExecutionRows={toolExecutionRows.length}
        ttsOutputLabel={ttsOutputLabel}
        voiceProfiles={voiceProfiles}
        voiceProfilesLoading={voiceProfilesLoading}
        onResetAssistantSettings={resetAssistantSettings}
        onSessionResolved={setSelectedSessionId}
        onStartLiveCall={startLiveCallFromControls}
        onUpdateAssistantSettings={updateAssistantSettings}
        onShowTools={() => setActiveUtilityPanel('tools')}
        selectedSessionId={selectedSessionId}
      />
    </>
  );
}

/** Chat's status line: validation, send, job, audio and stream status. */
export function ChatInlineStatus({ ws }: { ws: ChatWorkspaceModel }) {
  const { activeView, audioStatus, chatJobError, chatJobQuery, errors, livePresentation, pcmStream, sendMutation, settingsStatus } = ws;
  return (
    <>
      <div className="assistant-inline-status" aria-live="polite">
        {errors.content ? <span role="alert">Enter a message or paste an image before sending.</span> : null}
        {sendMutation.isPending ? <span role="status">Submitting the message to the response queue...</span> : null}
        {sendMutation.isError ? <span role="alert">{chatbotSubmitErrorMessage(sendMutation.error)}</span> : null}
        {chatJobQuery.isError ? <span role="alert">The response job could not be tracked. Refresh to check its status.</span> : null}
        {chatJobError ? <span role="alert">{chatJobError}</span> : null}
        {audioStatus ? <span role="status">{audioStatus}</span> : null}
        {pcmStream.status ? <span role="status">{pcmStream.status}</span> : null}
        {livePresentation.streamStatus ? <span role="status" data-omnix-live-voice-stream-status="true">{livePresentation.streamStatus}</span> : null}
        {settingsStatus && activeView === 'settings' ? <span role="status">{settingsStatus}</span> : null}
        {sendMutation.data ? <span role="status">{chatJobQuery.data?.status === 'completed' ? 'Response ready' : chatJobQuery.data?.status === 'failed' ? 'Response failed' : chatJobQuery.data?.status === 'canceled' ? 'Response canceled' : 'Response job accepted'}: {sendMutation.data.job.id}</span> : null}
      </div>
    </>
  );
}

/** The Live Voice card, fed from the workspace. */
export function ChatLiveVoicePanel({ ws }: { ws: ChatWorkspaceModel }) {
  const {
    autoSpeakResponses, callElapsedMs, chatJobInProgress, clearVoiceTranscript, control, currentLiveCallVoiceId,
    isAssistantSpeaking, liveCallRuntime, liveCardRef, liveDraftText, liveIdentityLabel, liveVoiceActive,
    sendMutation, sendVoiceTranscript, setAutoSpeakResponses, speechInputLabel, startVoiceInput,
    toggleLiveCallFromControls, ttsOutputLabel, visibleVoiceTranscriptMessages, voiceCaptureMode,
  } = ws;
  return (
    <>
      <ChatLiveVoiceCard
        cardRef={liveCardRef}
        voiceId={currentLiveCallVoiceId()}
        liveCallRuntime={liveCallRuntime}
        identityLabel={liveIdentityLabel}
        liveVoiceActive={liveVoiceActive}
        voiceCaptureMode={voiceCaptureMode}
        isAssistantSpeaking={isAssistantSpeaking}
        callElapsedMs={callElapsedMs}
        thinking={sendMutation.isPending || chatJobInProgress}
        control={control}
        liveDraftText={liveDraftText}
        sendPending={sendMutation.isPending}
        transcriptMessages={visibleVoiceTranscriptMessages}
        autoSpeakResponses={autoSpeakResponses}
        speechInputLabel={speechInputLabel}
        ttsOutputLabel={ttsOutputLabel}
        onToggleCall={toggleLiveCallFromControls}
        onClear={clearVoiceTranscript}
        onSendVoiceText={sendVoiceTranscript}
        onAutoSpeakChange={setAutoSpeakResponses}
        onTestInput={() => void startVoiceInput()}
      />
    </>
  );
}
