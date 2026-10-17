import { liveConversationStore } from './live-conversation-store';
import type { LiveConversationState } from './live-conversation-state';
import { ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

let liveVoicePendingOutputInterruptInstalled = false;


type UserSpeechDetail = {
  assistantSpeaking?: boolean;
  assistantOwnsFloor?: boolean;
};

export function shouldInterruptPendingAssistantOutput(conversation: LiveConversationState): boolean {
  return conversation.connection === 'connected'
    && conversation.delivery !== 'audio_started'
    && (
      conversation.assistantTurn === 'planning'
      || conversation.assistantTurn === 'generating'
      || conversation.assistantTurn === 'queued'
      || conversation.assistantTurn === 'speaking'
    );
}

export function initializeLiveVoicePendingOutputInterrupt(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveVoicePendingOutputInterruptInstalled) return () => undefined;
  liveVoicePendingOutputInterruptInstalled = true;

  const handleUserSpeech = (event: Event): void => {
    const conversation = liveConversationStore.getState().conversation;
    if (!shouldInterruptPendingAssistantOutput(conversation)) return;
    const detail = (event as CustomEvent<UserSpeechDetail>).detail ?? {};
    emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, {
        stage: 'pending_output_cancelled_on_user_speech',
        timestamp: new Date().toISOString(),
        assistantTurn: conversation.assistantTurn,
        delivery: conversation.delivery,
        floorOwner: conversation.floorOwner,
        assistantSpeaking: Boolean(detail.assistantSpeaking),
        assistantOwnsFloor: Boolean(detail.assistantOwnsFloor),
      });
    emitOmnixEvent(ASSISTANT_VOICE_INTERRUPT_EVENT, {
        source: 'pending-output-user-speech',
        intent: 'interrupt',
        confidence: 1,
        reason: 'user_speech_before_audio_started',
        timestamp: new Date().toISOString(),
      });
  };

  window.addEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
  return () => {
    window.removeEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
    liveVoicePendingOutputInterruptInstalled = false;
  };
}
