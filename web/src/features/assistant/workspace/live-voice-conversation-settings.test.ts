/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  readLiveConversationSettings,
  updateLiveConversationSettings,
} from './live-voice-conversation-settings';

describe('live conversation settings', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.body.innerHTML = '';
  });

  it('defaults to balanced timing with acknowledgements disabled', () => {
    expect(readLiveConversationSettings()).toEqual({
      conversationPace: 'balanced',
      interruptionPreference: 'balanced',
      backchannelMode: 'off',
    });
  });

  it('preserves existing assistant settings while updating conversation controls', () => {
    window.localStorage.setItem('omnix.chatbot.assistantSettings', JSON.stringify({
      voiceId: 'Jinx',
      liveVoiceSensitivity: 55,
    }));

    updateLiveConversationSettings({
      conversationPace: 'reflective',
      interruptionPreference: 'finish_more',
      backchannelMode: 'minimal',
    });

    const stored = JSON.parse(window.localStorage.getItem('omnix.chatbot.assistantSettings') || '{}');
    expect(stored.voiceId).toBe('Jinx');
    expect(stored.liveVoiceSensitivity).toBe(55);
    expect(readLiveConversationSettings()).toEqual({
      conversationPace: 'reflective',
      interruptionPreference: 'finish_more',
      backchannelMode: 'minimal',
    });
  });
});
