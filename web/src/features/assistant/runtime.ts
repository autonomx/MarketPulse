import type { DisposableStore, ModuleRuntimeContext } from '../../app/moduleRuntime';

/** Chat's browser runtime (WP-9.1): live voice, companion, research and session tools, undone on exit. */
export async function activateAssistantRuntime({ queryClient }: ModuleRuntimeContext, store: DisposableStore): Promise<void> {
  // Navigating away can dispose the store while these imports load; a step
  // that completes afterwards is undone at once by store.add.
  const add = (cleanup: Parameters<DisposableStore['add']>[0]) => store.add(cleanup);

  add((await import('./chat/chat-response-metrics-controller')).initializeChatResponseMetricsController());
  add((await import('./chat/sessionTools')).installSessionTools());
  add((await import('./chat/researchProgressController')).installResearchProgressController());
  add((await import('../voice')).installVoiceLibraryAssetFallback());
  add((await import('../voice')).installVoiceLibraryFetchDiagnostics());
  add((await import('./chat/liveCharacterAvatarBridge')).installLiveCharacterAvatarBridge());
  add((await import('./chat/liveCharacterVisemeBridge')).installLiveCharacterVisemeBridge());
  add((await import('./chat/live2dCharacterRenderer')).installLive2DCharacterRenderer());

  add((await import('./chat/live-chat-workspace')).initializeLiveChatWorkspace(queryClient));

  add((await import('./workspace/live-conversation-store-bridge')).initializeLiveConversationStoreBridge());
  add((await import('./workspace/live-session-coordinator')).initializeLiveSessionCoordinator());
  add((await import('./workspace/live-stt-authority-controller')).initializeLiveSttAuthorityController());
  const voiceTurns = await import('./workspace/live-voice-turn-coordinator');
  add(voiceTurns.initializeLiveVoiceTranscriptReconciliation());
  add(voiceTurns.initializeLiveVoiceTurnCoordinator());
  add((await import('./workspace/live-voice-release-observer')).initializeLiveVoiceReleaseObserver());
  add((await import('./workspace/live-voice-echo-suppression')).initializePlaybackEchoSuppression());
  add((await import('./workspace/live-speculation-diagnostics-bridge')).initializeLiveSpeculationDiagnosticsBridge());
  add((await import('./workspace/live-speculation-eligibility-diagnostics')).initializeLiveSpeculationEligibilityDiagnostics());
  add((await import('./workspace/live-speculation-direct-gateway-transport')).initializeLiveSpeculationDirectGatewayTransport());
  add((await import('./workspace/live-speculation-handshake-transport')).initializeLiveSpeculationHandshakeTransport());
  add((await import('./workspace/live-speculation-early-trigger')).initializeLiveSpeculationEarlyTrigger());
  add((await import('./workspace/live-speculation-runtime')).initializeLiveSpeculationRuntime());
  add((await import('./workspace/live-call-prewarm-controller')).initializeLiveCallPrewarmController());
  if (!store.isDisposed) (await import('./workspace/live-runtime-provenance')).emitLiveRuntimeProvenance();
  add((await import('./workspace/live-voice-controller')).initializeLiveVoiceController());
  add((await import('./workspace/live-output-coordinator')).initializeLiveOutputCoordinator());
  add((await import('./workspace/live-presence-policy-controller')).initializeLivePresencePolicyController());
  add((await import('./workspace/live-voice-duplex-gate')).initializeLiveVoiceDuplexGate());
  add((await import('./workspace/live-voice-audio-duck-bridge')).initializeLiveVoiceAudioDuckBridge());
  add((await import('./workspace/live-voice-cue-asset-bridge')).initializeLiveVoiceCueAssetBridge());
  add((await import('./workspace/live-voice-cue-pack-loader')).initializeLiveVoiceCuePackLoader());
  add((await import('./workspace/live-tts-capability-controller')).initializeLiveTtsCapabilityController());
  add((await import('./workspace/live-tts-adaptive-buffer-controller')).initializeLiveTtsAdaptiveBufferController());
  add((await import('./workspace/live-voice-unified-audio-controller')).initializeLiveVoiceUnifiedAudioController());
  add((await import('./workspace/live-voice-pending-output-interrupt')).initializeLiveVoicePendingOutputInterrupt());
  add((await import('./workspace/live-conversation-initiative-controller')).initializeLiveConversationInitiativeController());
  add((await import('./workspace/live-conversation-repair-controller')).initializeLiveConversationRepairController());
  add((await import('./workspace/live-conversation-evaluation-controller')).initializeLiveConversationEvaluationController());
  add((await import('./workspace/live-conversation-durable-evaluation-controller')).initializeLiveConversationDurableEvaluationController());

  add((await import('./workspace/assistant-context-controller')).initializeAssistantContextController());

  add((await import('./workspace/live-voice-form-sync')).initializeLiveVoiceFormSync());
  add((await import('./workspace/desktop-companion-expression-enricher')).initializeDesktopCompanionExpressionEnricher());
  add((await import('./workspace/desktop-companion-delivery')).initializeDesktopCompanionDeliveryController());
  const [watch, evaluation, operationalGuard] = await Promise.all([
    import('./workspace/desktop-companion-watch-controller'),
    import('./workspace/desktop-companion-shadow-evaluation-controller'),
    import('./workspace/desktop-companion-operational-guard'),
  ]);
  add(evaluation.initializeDesktopCompanionShadowEvaluationController());
  add(operationalGuard.initializeDesktopCompanionOperationalGuard());
  add(watch.initializeDesktopCompanionWatchController());
  add((await import('./workspace/research-release-controller')).initializeResearchReleaseController());
}
