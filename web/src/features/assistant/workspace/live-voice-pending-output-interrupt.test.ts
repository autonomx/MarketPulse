import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveConversationStore } from './live-conversation-store';
import {
  initializeLiveVoicePendingOutputInterrupt,
  shouldInterruptPendingAssistantOutput,
} from './live-voice-pending-output-interrupt';

let cleanup: (() => void) | null = null;

function setConversation(
  assistantTurn: 'idle' | 'planning' | 'generating' | 'queued' | 'speaking' | 'interrupted',
  delivery: 'generated' | 'visual_started' | 'audio_started' | 'completed' | 'interrupted',
): void {
  liveConversationStore.dispatch({ type: 'conversation', event: { type: 'connection', value: 'connected' } });
  liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: assistantTurn } });
  liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: delivery } });
}

beforeEach(() => {
  liveConversationStore.dispatch({ type: 'reset_all' });
  cleanup = initializeLiveVoicePendingOutputInterrupt();
});

afterEach(() => {
  cleanup?.();
  cleanup = null;
  liveConversationStore.dispatch({ type: 'reset_all' });
  vi.restoreAllMocks();
});

describe('live voice pending output interruption', () => {
  it('interrupts serialized pending assistant output when confirmed user speech arrives before audio starts', () => {
    setConversation('speaking', 'generated');
    const interrupt = vi.fn();
    const perf = vi.fn();
    window.addEventListener('omnix:assistant-voice-interrupt', interrupt, { once: true });
    window.addEventListener('omnix:assistant-voice-perf', perf, { once: true });

    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-user-speech', {
      detail: { assistantSpeaking: true, assistantOwnsFloor: true },
    }));

    expect(interrupt).toHaveBeenCalledTimes(1);
    expect((interrupt.mock.calls[0]?.[0] as CustomEvent).detail).toMatchObject({
      source: 'pending-output-user-speech',
      reason: 'user_speech_before_audio_started',
    });
    expect((perf.mock.calls[0]?.[0] as CustomEvent).detail).toMatchObject({
      stage: 'pending_output_cancelled_on_user_speech',
      assistantTurn: 'speaking',
      delivery: 'generated',
    });
  });

  it('does not bypass normal barge-in classification once assistant audio has started', () => {
    setConversation('speaking', 'audio_started');
    const interrupt = vi.fn();
    window.addEventListener('omnix:assistant-voice-interrupt', interrupt);

    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-user-speech', {
      detail: { assistantSpeaking: true, assistantOwnsFloor: true },
    }));

    expect(interrupt).not.toHaveBeenCalled();
    window.removeEventListener('omnix:assistant-voice-interrupt', interrupt);
  });

  it('recognizes generating and queued output as pending but ignores idle output', () => {
    const base = liveConversationStore.getState().conversation;
    expect(shouldInterruptPendingAssistantOutput({ ...base, connection: 'connected', assistantTurn: 'generating', delivery: 'generated' })).toBe(true);
    expect(shouldInterruptPendingAssistantOutput({ ...base, connection: 'connected', assistantTurn: 'queued', delivery: 'visual_started' })).toBe(true);
    expect(shouldInterruptPendingAssistantOutput({ ...base, connection: 'connected', assistantTurn: 'idle', delivery: 'completed' })).toBe(false);
  });
});
