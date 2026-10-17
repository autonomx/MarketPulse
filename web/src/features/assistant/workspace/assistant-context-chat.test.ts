import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../api/errors';
import { sendChatWithAssistantContext, type AssistantChatRoute } from './assistant-context-chat';
import { assistantContextStore } from './assistant-context-store';

afterEach(() => {
  assistantContextStore.resetForTests();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function recordingSender(responses: Partial<Record<AssistantChatRoute, () => Promise<string>>> = {}) {
  const calls: Array<{ route: AssistantChatRoute; body: Record<string, unknown> }> = [];
  const send = (route: AssistantChatRoute, body: Record<string, unknown>) => {
    calls.push({ route, body });
    return responses[route]?.() ?? Promise.resolve(route);
  };
  return { calls, send };
}

describe('sendChatWithAssistantContext', () => {
  it('sends a message to the chat route when no context tool is on', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })));
    const { calls, send } = recordingSender();

    await expect(sendChatWithAssistantContext('s1', { content: 'hello' }, send)).resolves.toBe('chat');

    expect(calls).toEqual([{ route: 'chat', body: { content: 'hello' } }]);
  });

  it('sends agent mode and deep research through the context route with their fields', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })));
    assistantContextStore.update({ agentMode: true, researchMode: 'deep', deepResearchMaxPages: 7 });
    const { calls, send } = recordingSender();

    await expect(sendChatWithAssistantContext('s1', { content: 'fix the layout', dry_run: true }, send)).resolves.toBe('context');

    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({
      route: 'context',
      body: expect.objectContaining({
        content: 'fix the layout',
        agent_mode: true,
        dry_run: false,
        web_research_mode: 'deep',
        deep_research_max_pages: 7,
        desktop_history_timestamps: [],
        desktop_capture_mode: 'single',
      }),
    });
  });

  it('falls back to the chat route with the original message when the gateway has no context route', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })));
    assistantContextStore.update({ researchMode: 'quick' });
    const { calls, send } = recordingSender({ context: () => Promise.reject(new ApiError(404, 'Not Found')) });

    await expect(sendChatWithAssistantContext('s1', { content: 'hello' }, send)).resolves.toBe('chat');

    expect(calls.map((call) => call.route)).toEqual(['context', 'chat']);
    expect(calls[1].body).toEqual({ content: 'hello' });
  });

  it('keeps other context-route failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 200 })));
    assistantContextStore.update({ researchMode: 'quick' });
    const { calls, send } = recordingSender({ context: () => Promise.reject(new ApiError(503, 'Unavailable')) });

    await expect(sendChatWithAssistantContext('s1', { content: 'hello' }, send)).rejects.toMatchObject({ status: 503 });
    expect(calls.map((call) => call.route)).toEqual(['context']);
  });

  it('saves the research mode after the message request settles, not before', async () => {
    const order: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      order.push(new URL(String(input), window.location.origin).pathname);
      return new Response(null, { status: 200 });
    }));
    assistantContextStore.update({ researchMode: 'quick' });
    let finish: (value: string) => void = () => undefined;
    const pending = new Promise<string>((resolve) => {
      finish = resolve;
    });
    const send = (route: AssistantChatRoute) => {
      order.push(`send:${route}`);
      return pending;
    };

    const result = sendChatWithAssistantContext('s1', { content: 'hello' }, send);
    await Promise.resolve();
    expect(order).toEqual(['send:context']);

    finish('done');
    await expect(result).resolves.toBe('done');
    await vi.waitFor(() => expect(order).toContain('/api/chat/sessions/s1/research-mode'));
  });
});
