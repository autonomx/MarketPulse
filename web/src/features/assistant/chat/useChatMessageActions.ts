import { useState } from 'react';
import { copyTextToClipboard, type AssistantMessageFeedback, type ChatMessage } from './chatbotWorkspaceModel';
import { toggleAssistantPcmStream } from '../workspace';
import { useStableMessageActions } from './ChatMessageItem';
import type { useResponseAudio } from './useResponseAudio';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent } from '../../../events/bus';

type ChatMessageActionsOptions = Pick<ReturnType<typeof useResponseAudio>, 'playAssistantResponseAudio' | 'stopAssistantResponseAudio' | 'currentLiveCallVoiceId'> & {
  setAudioStatus: (status: string | null) => void;
  /** The message whose reply audio is streaming, if any. */
  streamingMessageId: string | null;
  continueFrom: (message: ChatMessage) => void;
  openTools: () => void;
};

/** What a reply's buttons do: feedback, copy, play, stream, and the more-actions menu. */
export function useChatMessageActions({
  setAudioStatus,
  playAssistantResponseAudio,
  stopAssistantResponseAudio,
  currentLiveCallVoiceId,
  streamingMessageId,
  continueFrom,
  openTools,
}: ChatMessageActionsOptions) {
  const [assistantMessageFeedback, setAssistantMessageFeedback] = useState<Record<string, AssistantMessageFeedback>>({});
  const [openMessageActionMenuId, setOpenMessageActionMenuId] = useState<string | null>(null);
  const messageActions = useStableMessageActions({
    toggleFeedback: toggleAssistantMessageFeedback,
    copy: (message) => void copyAssistantResponse(message),
    play: (text) => void playAssistantResponseAudio(text),
    stream: streamAssistantResponseAudio,
    toggleMenu: (messageId) => setOpenMessageActionMenuId((current) => current === messageId ? null : messageId),
    closeMenu: () => setOpenMessageActionMenuId(null),
    continueFrom,
    openTools,
  });


  function toggleAssistantMessageFeedback(messageId: string, feedback: AssistantMessageFeedback): void {
    setAssistantMessageFeedback((current) => {
      const next = { ...current };
      if (next[messageId] === feedback) delete next[messageId];
      else next[messageId] = feedback;
      return next;
    });
    setAudioStatus(feedback === 'liked' ? 'Response marked as helpful.' : 'Response marked for review.');
  }

  async function copyAssistantResponse(message: ChatMessage): Promise<void> {
    const copied = await copyTextToClipboard(message.content);
    setAudioStatus(copied ? 'Assistant response copied.' : 'Copy failed. Select the message text and copy it manually.');
    setOpenMessageActionMenuId(null);
  }

  // Low-latency playback of one reply over the TTS WebSocket; pressing it again stops it.
  function streamAssistantResponseAudio(message: ChatMessage): void {
    if (streamingMessageId !== message.id) {
      emitOmnixEvent(ASSISTANT_VOICE_INTERRUPT_EVENT, { source: 'manual-stream-button', intent: 'audio-preempt', confidence: 1 });
      stopAssistantResponseAudio(undefined, { cancelPending: false });
    }
    void toggleAssistantPcmStream(message.id, message.content, currentLiveCallVoiceId() || null);
  }

  return { messageActions, assistantMessageFeedback, openMessageActionMenuId };
}
