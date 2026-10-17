/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { activeFetchMiddlewares, resetFetchPipelineForTests, pipelineFetch } from '../../../api/fetchPipeline';
import { describe, expect, it, vi } from 'vitest';

import {
  LiveChatSubmissionGateway,
  liveSubmissionFetchMatches,
} from './live-chat-submission-gateway';

const input = {
  sessionId: 'chat:test',
  text: 'Hello',
  source: 'live_coordination' as const,
  interrupted: false,
  segmentId: 'segment-1',
  sourceSequence: 1,
};

function dispatchDiagnostic(event: string, details: Record<string, unknown> = {}): void {
  window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
    detail: { event, details },
  }));
}

describe('live chat submission gateway', () => {
  it('matches only the exact POST chat stream for the submitted session', () => {
    expect(liveSubmissionFetchMatches(
      input,
      '/api/chat/sessions/chat%3Atest/messages/stream',
      { method: 'POST' },
    )).toBe(true);
    expect(liveSubmissionFetchMatches(
      input,
      '/api/chat/sessions/chat%3Aother/messages/stream',
      { method: 'POST' },
    )).toBe(false);
    expect(liveSubmissionFetchMatches(
      input,
      '/api/chat/sessions/chat%3Atest/messages/stream',
      { method: 'GET' },
    )).toBe(false);
  });

  it('resolves when the registered handler completes without a chat fetch', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const handler = vi.fn(async () => undefined);
    gateway.register(handler);

    await expect(gateway.submit(input)).resolves.toBeUndefined();
    expect(handler).toHaveBeenCalledWith(input);
  });

  it('releases coordination when its exact chat response opens instead of waiting for the body', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    let bodyController!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        bodyController = controller;
      },
    });
    window.fetch = vi.fn(async () => new Response(body, { status: 200 })) as typeof window.fetch;
    let handlerCompleted = false;
    gateway.register(async () => {
      const response = await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', {
        method: 'POST',
      });
      await response.text();
      handlerCompleted = true;
    });

    try {
      await expect(gateway.submit(input)).resolves.toBeUndefined();
      expect(handlerCompleted).toBe(false);
      bodyController.close();
      await vi.waitFor(() => expect(handlerCompleted).toBe(true));
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('rejects when its exact chat fetch fails even if the workspace handler catches it', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    window.fetch = vi.fn(async () => {
      throw new DOMException('BodyStreamBuffer was aborted', 'AbortError');
    }) as typeof window.fetch;
    gateway.register(async () => {
      try {
        await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', { method: 'POST' });
      } catch {
        // The workspace converts the transport error into status UI and returns.
      }
    });

    try {
      await expect(gateway.submit(input)).rejects.toThrow('BodyStreamBuffer was aborted');
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('rejects a non-success response from its exact chat fetch', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    window.fetch = vi.fn(async () => new Response('busy', { status: 503 })) as typeof window.fetch;
    gateway.register(async () => {
      await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', { method: 'POST' });
    });

    try {
      await expect(gateway.submit(input)).rejects.toThrow('live_chat_stream_status_503');
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('ignores a handler failure after its exact chat fetch has started', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    let resolveFetch!: (response: Response) => void;
    const pendingFetch = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    window.fetch = vi.fn(() => pendingFetch) as typeof window.fetch;
    gateway.register(async () => {
      void pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', { method: 'POST' });
      throw new Error('Speculation accept failed with status 409.');
    });

    try {
      const submission = gateway.submit(input);
      await Promise.resolve();
      resolveFetch(new Response('ok', { status: 200 }));
      await expect(submission).resolves.toBeUndefined();
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('ignores a superseded turn diagnostic while the new submission is waiting for its own fetch', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    let resolveFetch!: (response: Response) => void;
    const pendingFetch = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    window.fetch = vi.fn(() => pendingFetch) as typeof window.fetch;
    gateway.register(async () => {
      await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', { method: 'POST' });
    });

    try {
      const submission = gateway.submit(input);
      dispatchDiagnostic('chat_stream_failed', {
        error_name: 'AbortError',
        error_code: 'BodyStreamBuffer was aborted',
      });
      resolveFetch(new Response('ok', { status: 200 }));
      await expect(submission).resolves.toBeUndefined();
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('scopes a fetch interceptor to the synchronous submission handoff', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    const fallback = vi.fn(async () => new Response('fallback'));
    window.fetch = fallback as typeof window.fetch;
    const intercepted = vi.fn(async (_submission, request) => (
      new Response(`intercepted:${String(request)}`)
    ));
    gateway.registerFetchInterceptor(intercepted);
    let responseText = '';
    gateway.register(async () => {
      responseText = await (await pipelineFetch(
        '/api/chat/sessions/chat%3Atest/messages/stream',
        { method: 'POST' },
      )).text();
    });

    try {
      await gateway.submit(input);
      await vi.waitFor(() => {
        expect(responseText).toBe('intercepted:/api/chat/sessions/chat%3Atest/messages/stream');
      });
      expect(intercepted).toHaveBeenCalledOnce();
      expect(fallback).not.toHaveBeenCalled();
      // The interceptor is gone once the handoff returns: later requests reach the base fetch.
      expect(activeFetchMiddlewares()).not.toContain('live-chat-submission');
      await pipelineFetch('/api/chat/sessions');
      expect(fallback).toHaveBeenCalledOnce();
    } finally {
      resetFetchPipelineForTests();
      window.fetch = originalFetch;
    }
  });
});
