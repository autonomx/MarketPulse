export {
  ASSISTANT_WORKSPACE_SESSION_MODES,
  assertProjectSessionLink,
  assertWorkspaceProjectLink,
  assertWorkspaceSessionLink,
  createChatSessionRef,
  createProjectRef,
  createWorkspaceRef,
  isChatSessionMode,
} from './domain';
export type {
  WorkspaceChatSession,
  ChatSessionMode,
  ChatSessionRef,
  Project,
  ProjectRef,
  Workspace,
  WorkspaceRef,
} from './domain';

export {
  appendConversationTurn,
  CONVERSATION_TURN_ROLES,
  createConversationTurn,
  getLatestConversationTurn,
  isConversationTurnRole,
} from './conversation';
export type {
  ConversationState,
  ConversationTurn,
  ConversationTurnMetadata,
  ConversationTurnRole,
  MessageContent,
  TokenUsage,
} from './conversation';

export { ASSISTANT_WORKSPACE_EVENT_TYPES, isAssistantWorkspaceEventType } from './events';
export type {
  AssistantWorkspaceEvent,
  AssistantWorkspaceEventBase,
  AssistantWorkspaceEventPayload,
  AssistantWorkspaceEventPayloadByType,
  AssistantWorkspaceEventType,
} from './events';

export {
  createInMemoryAssistantWorkspaceEventStore,
  createStoredAssistantWorkspaceEventStore,
  parseAssistantWorkspaceEvents,
  serializeAssistantWorkspaceEvents,
} from './event-store';
export type {
  AssistantWorkspaceEventStorage,
  AssistantWorkspaceEventStore,
  AssistantWorkspaceEventStoreFilter,
} from './event-store';

export {
  DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG,
  createAssistantWorkspaceRuntimeConfig,
} from './runtime-config';
export type {
  AssistantWorkspaceRuntimeConfig,
  AssistantWorkspaceRuntimeEnv,
  AssistantWorkspaceRuntimeFeatureFlags,
} from './runtime-config';

export {
  appendProjectionEvent,
  createConversationProjection,
  rebuildConversationProjection,
} from './projections';
export type { ConversationProjection } from './projections';

export { assembleContext, createContextSource, getEnabledInstructions } from './context';
export type {
  AssistantIdentityContext,
  ContextAssembly,
  ContextSource,
  ContextSourceType,
  KnowledgeChunkContext,
  MemoryContext,
  ToolContext,
  WorkspaceInstructionContext,
} from './context';

export { createProviderCapabilitySet, createStaticModelProvider, providerSupportsRequest } from './provider';
export type { ModelProvider, ModelProviderCapabilities, ModelRequest, ModelResponse } from './provider';

export {
  createAnthropicProvider,
  createOpenAiCompatibleProvider,
  flattenMessageContent,
  fromAnthropicMessagesResponse,
  fromOpenAiChatResponse,
  toAnthropicMessagesRequest,
  toOpenAiChatRequest,
} from './provider-adapters';
export type {
  AnthropicMessage,
  AnthropicMessagesRequest,
  AnthropicMessagesResponse,
  AnthropicProviderOptions,
  OpenAiChatMessage,
  OpenAiChatRequest,
  OpenAiChatResponse,
  OpenAiCompatibleProviderOptions,
  OpenAiToolDefinition,
  ProviderHttpRequest,
  ProviderTransport,
} from './provider-adapters';

export {
  allocateContextBudget,
  createContextBudgetManager,
  createDefaultContextBudget,
  estimateContextSourceTokens,
} from './budget';
export type { BudgetedContextAssembly, ContextBudget, ContextBudgetManager } from './budget';

export {
  createAssistantResponseAudit,
  explainContextSource,
  summarizeAssistantResponseAudit,
} from './audit';
export type { AssistantResponseAudit, ResponseAuditSummary } from './audit';

export {
  DEFAULT_ASSISTANT_IDENTITY_NAMES,
  createAssistantIdentity,
  isDefaultAssistantIdentityName,
  updateAssistantIdentityPrompt,
} from './identity';
export type { AssistantIdentity, DefaultAssistantIdentityName } from './identity';

export {
  createWorkspaceProjectTree,
  getProjectConversationIds,
  summarizeWorkspaceProjectTree,
} from './workspace-system';
export type { ProjectWorkspaceSummary, WorkspaceProjectTree } from './workspace-system';

export {
  createMemoryRecord,
  filterMemoriesByScope,
  pinMemory,
  requiresMemoryConfirmation,
} from './memories';
export type { WorkspaceMemory, MemoryScope, MemorySource } from './memories';

export { createMemoryViewRows } from './memory-view';
export type { MemoryViewAction, MemoryViewFilter, MemoryViewRow } from './memory-view';

export { getReadyLibraryItems, getScopedLibraryItems, getSegmentsForItems } from './library-items';
export type { LibraryItem, LibraryItemStatus, LibrarySegment } from './library-items';

export {
  getEnabledInstructionRecords,
  getScopedInstructionRecords,
  sortInstructionRecords,
} from './instructions';
export type { InstructionRecord, InstructionScope } from './instructions';

export { CONTEXT_PANEL_TABS, createContextPanelSummary, isContextPanelTab } from './context-view';
export type { ContextPanelSummary, ContextPanelTab } from './context-view';

export {
  DEFAULT_ASSISTANT_APP_REGIONS,
  createAssistantAppLayout,
  getVisibleRegions,
} from './app-layout';
export type { AssistantAppLayout, AssistantWorkspaceRegion } from './app-layout';

export {
  createTimelineItemsFromEvents,
  createTimelineNote,
  filterTimelineItemsByKind,
  sortTimelineItems,
} from './timeline-items';
export type { TimelineItem, TimelineItemKind } from './timeline-items';

export {
  DEFAULT_COMPOSER_CONTROLS,
  createComposerState,
  toggleComposerControl,
} from './composer';
export type { ComposerControl, ComposerState } from './composer';

export { canInterruptLivePanel, createLivePanelState, setLivePanelMode } from './live-panel';
export type { LivePanelMode, LivePanelState } from './live-panel';

export { LIVE_SESSION_MODES, canStartInput, canStartOutput, isLiveSessionMode } from './session-mode';
export type { LiveSessionMode } from './session-mode';

export {
  canStartAudioCapture,
  createAudioCaptureState,
  selectAudioCaptureDevice,
} from './audio-capture';
export type { AudioCaptureDevice, AudioCaptureState, CapturePermission } from './audio-capture';

export {
  refreshBrowserAudioCaptureDevices,
  requestBrowserAudioCapturePermission,
  startBrowserAudioCapture,
  stopBrowserAudioCapture,
  stopBrowserAudioStream,
  toAudioCaptureDevices,
} from './audio-capture-browser';
export type {
  BrowserAudioCaptureSession,
  BrowserAudioConstraints,
  BrowserAudioDeviceInfo,
  BrowserAudioMediaDevices,
  BrowserAudioStream,
  BrowserAudioTrack,
} from './audio-capture-browser';

export {
  createTextSegment,
  getCompleteText,
  replaceDraftTextSegment,
} from './text-segments';
export type { TextSegment, TextSegmentKind } from './text-segments';

export {
  createProcessStep,
  getCompletedProcessStages,
  isProcessComplete,
} from './process-steps';
export type { ProcessStage, ProcessStep } from './process-steps';

export {
  createPlaybackQueue,
  enqueuePlaybackItem,
  setActivePlaybackItem,
} from './playback';
export type { PlaybackItem, PlaybackQueue } from './playback';

export {
  canInvokeCapability,
  canUseCapability,
  createCapabilityDefinition,
  createCapabilityEvents,
  createCapabilityInvocation,
  executeCapabilityInvocation,
  getEnabledCapabilities,
} from './capabilities';
export type {
  CapabilityDefinition,
  CapabilityEvent,
  CapabilityExecutionRecord,
  CapabilityExecutor,
  CapabilityInvocation,
  CapabilityInvocationResult,
  CapabilityRunStatus,
  CapabilityScope,
} from './capabilities';

export {
  createToolExecutionRows,
  getPendingToolExecutionRows,
  summarizeToolValue,
} from './tool-execution-view';
export type {
  ToolExecutionAction,
  ToolExecutionRow,
  ToolExecutionStatus,
} from './tool-execution-view';
export { ToolExecutionPanel } from './LedgerToolExecutionPanel';
export type { ToolExecutionPanelProps } from './LedgerToolExecutionPanel';

export {
  DEFAULT_ASSISTANT_WORKSPACE_PREFERENCES,
  mergeAssistantWorkspacePreferences,
  shouldAnimateWorkspace,
  shouldShowLiveCaptions,
} from './preferences';
export type { AssistantWorkspacePreferences, WorkspaceAppearance, WorkspaceDensity } from './preferences';

export {
  createWorkspaceAccessibilityProfile,
  getResponsivePanelCount,
  isWorkspaceAccessible,
  shouldUseAccessibleMotion,
} from './accessibility';
export type { WorkspaceAccessibilityProfile, WorkspaceBreakpoint } from './accessibility';

export { getWorkspaceQualityStatus, summarizeWorkspaceQuality } from './quality';
export type { WorkspaceQualitySignal, WorkspaceQualitySummary } from './quality';

export { AssistantWorkspaceDashboard, AssistantWorkspaceDashboardPanel } from './AssistantWorkspaceDashboard';
export { AssistantWorkspaceActivityPanel } from './AssistantWorkspaceActivityPanel';
export type { AssistantWorkspaceActivityPanelProps } from './AssistantWorkspaceActivityPanel';
export {
  createAssistantWorkspaceDashboard,
} from './workspace-dashboard';
export type {
  AssistantWorkspaceDashboardInput,
  AssistantWorkspaceDashboardMetric,
  AssistantWorkspaceDashboardStatus,
  AssistantWorkspaceDashboardView,
} from './workspace-dashboard';

export {
  createFetchSpeechServiceTransport,
  createSttServiceClient,
  createTtsServiceClient,
} from './speech-services';
export type {
  SpeechAudioInput,
  SpeechServiceClientOptions,
  SpeechServiceTransport,
  SpeechServiceTransportRequest,
  SttServiceClient,
  SttTranscriptionRequest,
  SttTranscriptionResponse,
  TtsServiceClient,
  TtsSynthesisRequest,
  TtsSynthesisResponse,
} from './speech-services';

export { flattenModelResponseText, runLiveAssistantTurn } from './live-orchestrator';
export type { LiveAssistantTurnInput, LiveAssistantTurnResult } from './live-orchestrator';

export { useLiveAssistantSession } from './useLiveAssistantSession';
export type {
  LiveAssistantSessionApi,
  LiveAssistantSessionController,
  LiveAssistantSessionState,
  LiveAssistantSessionStatus,
} from './useLiveAssistantSession';

export { LiveAssistantSessionPanel } from './LiveAssistantSessionPanel';
export type { LiveAssistantSessionPanelProps } from './LiveAssistantSessionPanel';

export { createBrowserLiveAssistantController } from './browser-live-controller';
export type { BrowserLiveAssistantControllerOptions, CapturedAudioReader } from './browser-live-controller';

export { createMediaRecorderAudioReader } from './media-recorder-reader';
export type {
  BrowserMediaRecorder,
  BrowserMediaRecorderFactory,
  BrowserRecorderBlobEvent,
  BrowserRecorderErrorEvent,
  MediaRecorderAudioReaderOptions,
} from './media-recorder-reader';

export { useAvatarPresence } from './live-avatar-presence';
export type { AvatarPresence, AvatarPresenceCue } from './live-avatar-presence';
export { LIVE_TASK_PRESETS, liveCallPresentationStore, liveCallVoiceMode, liveCaptureLabels, useLiveCallPresentation } from './live-call-presentation-store';
export { isLiveVoiceControllerInstalled, startLiveVoiceCall, toggleLiveVoiceCall } from './live-voice-controller';
export { isLiveVoiceUnifiedAudioInstalled } from './live-voice-unified-audio-controller';
export { stopAssistantPcmStream, toggleAssistantPcmStream, useAssistantPcmStream } from './assistant-pcm-stream-websocket-player';
export { createLiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
export type { LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
export { createChatbotActivityEvents, createChatbotFailureEvent } from './chatbot-activity';
export { assistantContextStore } from './assistant-context-store';
export { liveChatSubmissionGateway } from './live-chat-submission-gateway';
export { liveVoiceTranscriptStore, useLiveVoiceTranscript } from './live-voice-transcript-store';
export { AssistantContextControls, DesktopShareButton, DesktopShareStatusRow } from './assistant-context-controls';
export { DesktopCompanionControls } from './desktop-companion-controls';
export { DesktopCompanionTextSurface } from './desktop-companion-text-surface';
export type { ChatbotActivitySession } from './chatbot-activity';
export type { LiveVoiceTranscriptState } from './live-voice-transcript-store';
export { sendChatWithAssistantContext } from './assistant-context-chat';
export type { AssistantChatRoute } from './assistant-context-chat';
export { adoptActiveSession, applySessionResearchMode } from './assistant-context-store';
