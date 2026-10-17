import { waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { initializeLiveChatWorkspace, sessionIdFromChatRequest } from './live-chat-workspace';
import { pipelineFetch } from '../../../api/fetchPipeline';
import { createTestQueryClient } from '../../../test/renderWithProviders';

let disposeWorkspace: (() => void) | null = null;

afterEach(() => {
  disposeWorkspace?.();
  disposeWorkspace = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('live chat workspace controller', () => {
  it('recognizes the selected chat session from session API requests', () => {
    expect(sessionIdFromChatRequest('/api/chat/sessions/chat%3Aone')).toBe('chat:one');
    expect(sessionIdFromChatRequest('/api/chat/sessions/chat%3Aone/live-call/runtime')).toBe('chat:one');
    expect(sessionIdFromChatRequest('/api/chat/sessions')).toBeNull();
  });

  it('announces the session live requests switch to', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ok: true })));
    const changed = vi.fn();
    window.addEventListener('omnix:live-chat-session-changed', changed);
    disposeWorkspace = initializeLiveChatWorkspace(createTestQueryClient());

    await pipelineFetch('/api/chat/sessions/chat%3Aone/interaction');
    await pipelineFetch('/api/chat/sessions/chat%3Aone/live-call/runtime');

    expect(changed).toHaveBeenCalledOnce();
    expect((changed.mock.calls[0][0] as CustomEvent<{ sessionId: string }>).detail.sessionId).toBe('chat:one');
    window.removeEventListener('omnix:live-chat-session-changed', changed);
  });

  it('reconciles persisted chat history after terminal live response diagnostics', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ok: true })));
    const queryClient = createTestQueryClient();
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue();
    disposeWorkspace = initializeLiveChatWorkspace(queryClient);

    await pipelineFetch('/api/chat/sessions/chat%3Aone/interaction');
    window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
      detail: { event: 'turn_finished', details: { turn_kind: 'response' } },
    }));

    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ['feature', 'chatbot', 'session', 'chat:one'],
        exact: true,
      });
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ['feature', 'chatbot', 'sessions'],
        exact: true,
      });
    });

    invalidateQueries.mockClear();
    window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
      detail: { event: 'turn_finished', details: { turn_kind: 'greeting' } },
    }));
    expect(invalidateQueries).not.toHaveBeenCalled();
  });
});
