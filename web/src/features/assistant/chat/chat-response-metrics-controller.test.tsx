/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import {
  ChatResponseMetricsRow,
  formatLmStudioStopReason,
  initializeChatResponseMetricsController,
  readChatResponseMetrics,
  resetChatResponseMetricsForTests,
} from './chat-response-metrics-controller';
import { pipelineFetch } from '../../../api/fetchPipeline';

afterEach(() => {
  resetChatResponseMetricsForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  cleanup();
});

describe('chat response metrics', () => {
  it('normalizes LM Studio persisted metrics', () => {
    expect(readChatResponseMetrics({
      usage: { completion_tokens: 37 },
      provider_metrics: {
        provider: 'lmstudio',
        tokens_per_second: 127.26,
        generation_time_seconds: 0.38,
        time_to_first_token_seconds: 0.11,
        stop_reason: 'eosFound',
      },
    })).toEqual({
      tokensPerSecond: 127.26,
      outputTokens: 37,
      generationTimeSeconds: 0.38,
      timeToFirstTokenSeconds: 0.11,
      stopReason: 'eosFound',
    });
  });

  it('renders compact metrics for an assistant reply', () => {
    render(<ChatResponseMetricsRow metadata={{
      usage: { completion_tokens: 37 },
      provider_metrics: {
        provider: 'lmstudio',
        tokens_per_second: 127.26,
        generation_time_seconds: 0.38,
        time_to_first_token_seconds: 0.11,
        stop_reason: 'eosFound',
      },
    }} />);

    const row = screen.getByLabelText('LM Studio response metrics');
    expect(row).toHaveTextContent('127.26 tok/sec');
    expect(row).toHaveTextContent('37 tokens');
    expect(row).toHaveTextContent('0.38s');
    expect(row).toHaveTextContent('Stop reason: EOS Token Found');
    expect(row.dataset.timeToFirstTokenSeconds).toBe('0.11');
  });

  it('renders nothing for replies without LM Studio metrics', () => {
    const { container } = render(<ChatResponseMetricsRow metadata={{ provider_metrics: { provider: 'openrouter', tokens_per_second: 5 } }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('formats LM Studio stop reason identifiers for display', () => {
    expect(formatLmStudioStopReason('maxPredictedTokensReached')).toBe('Max Tokens Reached');
    expect(formatLmStudioStopReason('stopStringFound')).toBe('Stop String Found');
  });

  it('does not clone live streams and persists the effective transport version', async () => {
    const cloneSpy = vi.spyOn(Response.prototype, 'clone');
    const observed: Array<Record<string, unknown>> = [];
    const listener = (event: Event): void => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail;
      if (detail?.stage === 'chat_sse_transport_response_observed') observed.push(detail);
    };
    window.addEventListener('omnix:assistant-voice-perf', listener);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
      if (rawUrl.includes('/api/tts/live-call/diagnostics')) {
        return new Response(null, { status: 204 });
      }
      return new Response('data: {"type":"done"}\n\n', {
        status: 200,
        headers: {
          'content-type': 'text/event-stream',
          'x-omnix-sse-transport': 'immediate-v2',
        },
      });
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    initializeChatResponseMetricsController();

    const response = await pipelineFetch('/api/chat/sessions/session-1/messages/stream', {
      method: 'POST',
      body: JSON.stringify({ live_voice_turn_id: 'voice-turn:test' }),
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    window.removeEventListener('omnix:assistant-voice-perf', listener);
    expect(response.status).toBe(200);
    expect(cloneSpy).not.toHaveBeenCalled();
    expect(observed).toEqual([
      expect.objectContaining({
        stage: 'chat_sse_transport_response_observed',
        turnId: 'voice-turn:test',
        transportVersion: 'immediate-v2',
        contentType: 'text/event-stream',
        responseCloned: false,
      }),
    ]);
    const diagnosticsRequest = fetchMock.mock.calls.find(
      (call) => String(call[0]).includes('/api/tts/live-call/diagnostics'),
    );
    expect(diagnosticsRequest).toBeDefined();
    const diagnosticsBody = JSON.parse(String(diagnosticsRequest?.[1]?.body));
    expect(diagnosticsBody.trace_id).toBe('live-call:voice-turn:test');
    expect(diagnosticsBody.events).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: 'chat_response_metrics',
        event: 'chat_sse_transport_response_observed',
        details: expect.objectContaining({
          transport_version: 'immediate-v2',
          content_type: 'text/event-stream',
          response_cloned: false,
        }),
      }),
    ]));
  });
});
