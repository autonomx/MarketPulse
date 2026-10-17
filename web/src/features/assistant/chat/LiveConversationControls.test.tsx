import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveConversationControls } from './LiveConversationControls';
import type { LiveConversationProfile } from './liveConversationProfileClient';

const profile: LiveConversationProfile = {
  presence_preset: 'natural',
  talkativeness: 50,
  conversation_stance: 'automatic',
  conversation_pace: 'balanced',
  interruption_preference: 'balanced',
  assistant_backchannel_mode: 'off',
  initiative_mode: 'gentle',
  idle_threshold_ms: 15000,
  long_pause_behavior: 'wait',
  response_length: 'conversational',
  response_onset_style: 'adaptive',
  emotional_attunement: 'subtle',
  topic_continuity: 'natural',
  max_idle_prompts: 1,
  duplex_mode: 'automatic',
  pronunciation_save_policy: 'ask',
  profile_version: 1,
};

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('omnix.liveConversation.serverProfileMigrated.v1', 'done');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LiveConversationControls', () => {
  it('renders the effective server profile and advanced turn-taking controls', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      user_defaults: profile,
      session_override: null,
      effective: profile,
      source: 'user_defaults',
    })));

    render(<LiveConversationControls sessionId="chat:one" />);

    expect(await screen.findByLabelText('Presence')).toHaveValue('natural');
    expect(screen.getByLabelText('Conversation stance')).toHaveValue('automatic');
    expect(screen.getByLabelText('Response length')).toHaveValue('conversational');
    fireEvent.click(screen.getByRole('button', { name: 'Show advanced controls' }));
    expect(screen.getByLabelText('Conversation pace')).toHaveValue('balanced');
    expect(screen.getByLabelText('Assistant listener backchannels')).toHaveValue('off');
    expect(screen.getByLabelText('Response onset')).toHaveValue('adaptive');
    expect(screen.getByLabelText('Emotional attunement')).toHaveValue('subtle');
    expect(screen.getByLabelText('Topic continuity')).toHaveValue('natural');
    expect(screen.getByLabelText('Pronunciation saving')).toHaveValue('ask');
    expect(screen.getByLabelText('First idle prompt seconds')).toHaveAttribute('min', '1');
  });

  it('shows profile controls without waiting for a stalled legacy migration', async () => {
    window.localStorage.removeItem('omnix.liveConversation.serverProfileMigrated.v1');
    window.localStorage.setItem('omnix.liveConversation.settings', JSON.stringify({ conversationPace: 'balanced' }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') return new Promise<Response>(() => undefined);
      return Promise.resolve(Response.json({
        user_defaults: profile,
        session_override: null,
        effective: profile,
        source: 'user_defaults',
      }));
    }));

    render(<LiveConversationControls sessionId="chat:one" />);

    expect(await screen.findByLabelText('Presence')).toHaveValue('natural');
    expect(screen.getByRole('button', { name: 'Show advanced controls' })).toBeInTheDocument();
  });

  it('persists a session override and mirrors runtime-compatible fields', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      requests.push({ url, init });
      if (init?.method === 'PATCH') {
        const updated = { ...profile, presence_preset: 'engaged' as const, profile_version: 2 };
        return Response.json({ user_defaults: profile, session_override: updated, effective: updated, source: 'session_override' });
      }
      return Response.json({ user_defaults: profile, session_override: null, effective: profile, source: 'user_defaults' });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<LiveConversationControls sessionId="chat:one" />);
    await screen.findByLabelText('Presence');
    fireEvent.change(screen.getByLabelText('Presence'), { target: { value: 'engaged' } });

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'PATCH')).toBe(true));
    const patchRequest = requests.find((request) => request.init?.method === 'PATCH');
    expect(patchRequest?.url).toContain('/api/chat/sessions/chat%3Aone/live-conversation/profile');
    expect(JSON.parse(String(patchRequest?.init?.body))).toEqual({ presence_preset: 'engaged' });
    expect(await screen.findByRole('status')).toHaveTextContent('Session presence profile saved.');
    expect(JSON.parse(window.localStorage.getItem('omnix.liveConversation.settings') || '{}')).toEqual({
      conversationPace: 'balanced',
      interruptionPreference: 'balanced',
      backchannelMode: 'off',
    });
  });

  it('applies the full-duplex profile as one coherent session patch', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      requests.push({ url, init });
      if (init?.method === 'PATCH') {
        const patch = JSON.parse(String(init.body));
        const updated = { ...profile, ...patch, profile_version: 2 };
        return Response.json({
          user_defaults: profile,
          session_override: updated,
          effective: updated,
          source: 'session_override',
        });
      }
      return Response.json({
        user_defaults: profile,
        session_override: null,
        effective: profile,
        source: 'user_defaults',
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<LiveConversationControls sessionId="chat:one" />);
    fireEvent.click(await screen.findByRole('button', { name: /Full duplex/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Full duplex/ })).toHaveAttribute('aria-pressed', 'true');
    });
    const patchRequest = requests.find((request) => request.init?.method === 'PATCH');
    const patch = JSON.parse(String(patchRequest?.init?.body));
    expect(patch).toMatchObject({
      conversation_pace: 'quick',
      interruption_preference: 'easy',
      assistant_backchannel_mode: 'natural',
      duplex_mode: 'echo_aware',
      response_onset_style: 'immediate',
    });
    expect(Object.keys(patch)).toHaveLength(16);
  });
});
