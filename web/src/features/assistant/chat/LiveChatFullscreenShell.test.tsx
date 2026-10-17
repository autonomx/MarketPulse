 
/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveConversationStore } from '../workspace/live-conversation-store';
import { LiveChatFullscreenShell, type LiveChatMessage } from './LiveChatFullscreenShell';
import type { CharacterLiveCallRuntime } from './characterClient';
import { publishCharacterAvatarRuntime } from './liveCharacterAvatarBridge';
import { fixture } from '../../../test/fixture';

const mayaRuntime: CharacterLiveCallRuntime = fixture({
  session_id: 'chat:maya',
  interaction_mode: 'character',
  display_name: 'Maya',
  character_id: 'maya',
  avatar_pack: {
    character_id: 'maya',
    version: 1,
    render_mode: 'audio_envelope',
    renderer: 'sprite',
    rig_asset_id: null,
    base_asset_id: 'image:maya-base',
    mouth_frames: { closed: 'image:maya-closed' },
    blink_frames: {},
    expression_frames: {},
    outfit_frames: {},
    background_asset_ids: {},
  },
});
import {
  enterLiveChatFullscreen,
  exitLiveChatFullscreen,
  initializeLiveChatFullscreenController,
  resetLiveChatFullscreenStateForTests,
} from './live-chat-fullscreen-controller';

const sourceCallClick = vi.fn();
const sourceSubmit = vi.fn((text: string) => Boolean(text.trim()));
const messages: LiveChatMessage[] = [
  { id: 'a1', role: 'assistant', text: 'Welcome to our corner of the stars.', timestamp: null },
  { id: 'u1', role: 'user', text: 'Can you tell me a story?', timestamp: null },
];

function renderShell() {
  return render(<LiveChatFullscreenShell messages={messages} onSendMessage={sourceSubmit} onToggleCall={sourceCallClick} />);
}

describe('LiveChatFullscreenShell', () => {
  let dispose: () => void;

  beforeEach(() => {
    sourceCallClick.mockClear();
    sourceSubmit.mockClear();
    Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: undefined });
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    resetLiveChatFullscreenStateForTests();
    dispose = initializeLiveChatFullscreenController();
    liveConversationStore.reset();
    liveConversationStore.dispatch({ type: 'identity', identity: { characterId: 'maya', displayName: 'Maya' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'connection', value: 'connected' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'audio_started' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'assistant' } });
    liveConversationStore.dispatch({ type: 'duplex', duplex: { resolvedMode: 'echo_aware', reason: 'calibration_confident' } });
  });

  afterEach(async () => {
    publishCharacterAvatarRuntime(null);
    await exitLiveChatFullscreen();
    dispose();
    cleanup();
    liveConversationStore.reset();
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('renders a character-first stage and the conversation it is given', () => {
    enterLiveChatFullscreen('header');
    renderShell();
    act(() => publishCharacterAvatarRuntime(mayaRuntime));

    const dialog = screen.getByRole('dialog', { name: 'Immersive Live Chat with Maya' });
    const fullscreen = within(dialog);
    expect(dialog).toBeInTheDocument();
    expect(fullscreen.getByAltText('Maya live avatar')).toBeInTheDocument();
    expect(fullscreen.getByText('Welcome to our corner of the stars.')).toBeInTheDocument();
    expect(fullscreen.getByText('Can you tell me a story?')).toBeInTheDocument();
    expect(fullscreen.getByText('Maya is speaking')).toBeInTheDocument();
    expect(fullscreen.getByText(/Microphone listening · Echo-aware/)).toBeInTheDocument();
  });

  it('hands call and composer actions to Chat', () => {
    enterLiveChatFullscreen('call-card');
    renderShell();

    fireEvent.click(screen.getByRole('button', { name: 'End voice chat' }));
    expect(sourceCallClick).toHaveBeenCalledTimes(1);

    const composer = screen.getByPlaceholderText('Write a message…');
    fireEvent.change(composer, { target: { value: 'A fullscreen message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send fullscreen Live Chat message' }));
    expect(sourceSubmit).toHaveBeenCalledWith('A fullscreen message');
    expect(composer).toHaveValue('');
  });

  it('exits the overlay without ending the call', async () => {
    enterLiveChatFullscreen('header');
    renderShell();

    fireEvent.click(screen.getByRole('button', { name: 'Exit fullscreen Live Chat' }));
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(sourceCallClick).not.toHaveBeenCalled();
  });
});
