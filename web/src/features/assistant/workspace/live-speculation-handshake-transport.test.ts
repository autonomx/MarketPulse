import { describe, expect, it, vi } from 'vitest';

import {
  directGatewayEnabled,
  liveChatDirectGatewayEnabled,
  resolveDirectLiveChatUrl,
  resolveDirectSpeculationUrl,
  withLiveVoiceCallAffinity,
} from './live-speculation-direct-gateway-transport';
import { bridgeLiveSpeculationHandshakeRequest } from './live-speculation-handshake-transport';

function startBody(segmentId = 'segment-test', sourceSequence = 4): string {
  return JSON.stringify({
    content: 'Tell me a story',
    segment_id: segmentId,
    source_sequence: sourceSequence,
  });
}

function liveChatBody(turnId = 'voice-turn:test'): string {
  return JSON.stringify({
    content: 'Hello there',
    live_voice_turn_id: turnId,
  });
}

function generationIdFrom(text: string): string {
  const match = text.match(/"generation_id":"([^"]+)"/);
  if (!match) throw new Error(`No generation id in ${text}`);
  return match[1];
}

describe('live speculation direct gateway transport', () => {
  const viteLocation = {
    hostname: 'localhost',
    port: '5173',
    origin: 'http://localhost:5173',
  } as Pick<Location, 'hostname' | 'port' | 'origin'>;

  it('routes only private speculation requests directly to the local gateway', () => {
    expect(directGatewayEnabled(viteLocation, {})).toBe(true);
    expect(resolveDirectSpeculationUrl(
      '/api/live/speculation/sessions/session-test/start-stream?x=1',
      viteLocation,
      {},
    )).toBe('http://127.0.0.1:8000/api/live/speculation/sessions/session-test/start-stream?x=1');
    expect(resolveDirectSpeculationUrl(
      '/api/live/speculation/tts-prefetch',
      viteLocation,
      { VITE_LIVE_SPECULATION_GATEWAY_ORIGIN: 'http://127.0.0.1:8123' },
    )).toBe('http://127.0.0.1:8123/api/live/speculation/tts-prefetch');
    expect(resolveDirectSpeculationUrl(
      '/api/chat/sessions/session-test/messages/stream',
      viteLocation,
      {},
    )).toBeNull();
  });

  it('routes accepted live voice chat directly without changing manual chat', () => {
    const path = '/api/chat/sessions/session-test/messages/stream';
    expect(liveChatDirectGatewayEnabled(viteLocation, {})).toBe(true);
    expect(resolveDirectLiveChatUrl(
      path,
      { method: 'POST', body: liveChatBody() },
      viteLocation,
      {},
    )).toBe('http://127.0.0.1:8000/api/chat/sessions/session-test/messages/stream');
    expect(resolveDirectLiveChatUrl(
      path,
      { method: 'POST', body: JSON.stringify({ content: 'manual chat' }) },
      viteLocation,
      {},
    )).toBeNull();
  });

  it('attaches stable call affinity without changing manual Chat', () => {
    const init = { method: 'POST', body: liveChatBody(), headers: { 'Content-Type': 'application/json' } };
    const pinned = withLiveVoiceCallAffinity('/api/chat/sessions/a/messages/stream', init);
    expect(new Headers(pinned?.headers).get('X-Omnix-Call-Id')).toBe('a');
    expect(new Headers(pinned?.headers).has('X-Omnix-Gateway-Affinity')).toBe(false);
    expect(new Headers(pinned?.headers).get('Content-Type')).toBe('application/json');
    expect(pinned?.body).toBe(init.body);
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    const manual = { method: 'POST', body: JSON.stringify({ content: 'manual' }) };
    expect(withLiveVoiceCallAffinity('/api/chat/sessions/a/messages/stream', manual)).toBe(manual);
  });

  it('honors accepted-live direct origin and opt-out independently of speculation', () => {
    const path = '/api/chat/sessions/session-test/messages/stream?mode=voice';
    expect(resolveDirectLiveChatUrl(
      path,
      { method: 'POST', body: liveChatBody() },
      viteLocation,
      { VITE_LIVE_CHAT_GATEWAY_ORIGIN: 'http://localhost:8124' },
    )).toBe('http://localhost:8124/api/chat/sessions/session-test/messages/stream?mode=voice');
    expect(resolveDirectLiveChatUrl(
      path,
      { method: 'POST', body: liveChatBody() },
      viteLocation,
      { VITE_LIVE_CHAT_DIRECT_GATEWAY: 'false' },
    )).toBeNull();
  });

  it('fails closed outside local Vite and honors the explicit opt-out', () => {
    expect(directGatewayEnabled({
      hostname: 'omnix.example',
      port: '443',
      origin: 'https://omnix.example',
    }, {})).toBe(false);
    expect(directGatewayEnabled(viteLocation, {
      VITE_LIVE_SPECULATION_DIRECT_GATEWAY: 'false',
    })).toBe(false);
    expect(liveChatDirectGatewayEnabled({
      hostname: 'omnix.example',
      port: '443',
      origin: 'https://omnix.example',
    }, {})).toBe(false);
  });
});

describe('live speculation handshake transport', () => {
  it('opens locally before the inline gateway response and reuses the client generation id', async () => {
    let resolveInline: ((response: Response) => void) | undefined;
    let forwardedBody = '';
    const inlineResponse = new Promise<Response>((resolve) => {
      resolveInline = resolve;
    });
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input.toString();
      if (url.endsWith('/start-stream')) {
        forwardedBody = String(init?.body ?? '');
        return inlineResponse;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    const bridged = await bridgeLiveSpeculationHandshakeRequest(
      fetchImpl,
      '/api/live/speculation/sessions/session-test/stream',
      { method: 'POST', body: startBody() },
    );

    expect(bridged).not.toBeNull();
    const reader = bridged!.body!.getReader();
    const decoder = new TextDecoder();
    const first = await reader.read();
    const firstText = decoder.decode(first.value);
    const clientGenerationId = generationIdFrom(firstText);
    expect(clientGenerationId).toMatch(/^spec-client-/);
    expect(firstText).toContain('"optimistic_transport":true');

    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/live/speculation/sessions/session-test/start-stream',
      expect.objectContaining({ method: 'POST', priority: 'high' }),
    );
    expect(JSON.parse(forwardedBody)).toMatchObject({
      generation_id: clientGenerationId,
      segment_id: 'segment-test',
      source_sequence: 4,
    });

    resolveInline?.(new Response(
      `data: {"type":"speculation_started","generation_id":"${clientGenerationId}"}\n\n`
      + 'data: {"type":"text_chunk","text":"Hello"}\n\n'
      + `data: {"type":"done","generation_id":"${clientGenerationId}"}\n\n`,
      {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'X-Omnix-Speculation-Generation-Id': clientGenerationId,
          'X-Omnix-Speculation-Transport': 'inline-v2-client-id',
        },
      },
    ));

    let remainder = '';
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      remainder += decoder.decode(next.value, { stream: true });
    }
    remainder += decoder.decode();
    expect(remainder).toContain('"type":"text_chunk"');
    expect(remainder).toContain('"text":"Hello"');
  });

  it('falls back to the two-request handshake on an older gateway', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url.endsWith('/start-stream')) {
        return new Response('not found', { status: 404 });
      }
      if (url.endsWith('/start')) {
        return new Response(JSON.stringify({
          ok: true,
          generation_id: 'spec-test',
          segment_id: 'segment-test',
          source_sequence: 4,
          provider_id: 'fake-provider',
          model_id: 'fake-model',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/spec-test/stream')) {
        return new Response(
          'data: {"type":"text_chunk","text":"Hello"}\n\n'
          + 'data: {"type":"done","generation_id":"spec-test"}\n\n',
          {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    const bridged = await bridgeLiveSpeculationHandshakeRequest(
      fetchImpl,
      '/api/live/speculation/sessions/session-test/stream',
      { method: 'POST', body: startBody() },
    );

    expect(bridged).not.toBeNull();
    const text = await bridged!.text();
    expect(text).toContain('"generation_id":"spec-client-');
    expect(text).toContain('"generation_id":"spec-test"');
    expect(text).toContain('"type":"text_chunk"');
    expect(text).toContain('"text":"Hello"');
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/live/speculation/sessions/session-test/start',
      expect.objectContaining({ method: 'POST', priority: 'high' }),
    );
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/live/speculation/sessions/session-test/spec-test/stream',
      expect.objectContaining({ method: 'POST', priority: 'high' }),
    );
  });

  it('cancels eager fallback generation when the source request is aborted', async () => {
    const sourceAbort = new AbortController();
    let generationController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const neverEndingStream = new ReadableStream<Uint8Array>({
      start(controller) {
        generationController = controller;
      },
    });
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url.endsWith('/start-stream')) {
        return new Response('not found', { status: 404 });
      }
      if (url.endsWith('/start')) {
        return new Response(JSON.stringify({
          ok: true,
          generation_id: 'spec-cancel',
          segment_id: 'segment-cancel',
          source_sequence: 5,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/spec-cancel/stream')) {
        return new Response(neverEndingStream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (url.endsWith('/cancel')) {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    const bridged = await bridgeLiveSpeculationHandshakeRequest(
      fetchImpl,
      '/api/live/speculation/sessions/session-test/stream',
      {
        method: 'POST',
        body: startBody('segment-cancel', 5),
        signal: sourceAbort.signal,
      },
    );

    expect(bridged).not.toBeNull();
    const reader = bridged!.body!.getReader();
    await reader.read();
    await vi.waitFor(() => {
      expect(fetchImpl).toHaveBeenCalledWith(
        '/api/live/speculation/sessions/session-test/spec-cancel/stream',
        expect.objectContaining({ method: 'POST', priority: 'high' }),
      );
    });
    sourceAbort.abort('transcript corrected');

    await vi.waitFor(() => {
      expect(fetchImpl).toHaveBeenCalledWith(
        '/api/live/speculation/sessions/session-test/spec-cancel/cancel',
        expect.objectContaining({ method: 'POST', keepalive: true }),
      );
    });
    generationController?.close();
    await reader.cancel();
  });

  it('leaves unrelated fetch requests untouched', async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const bridged = await bridgeLiveSpeculationHandshakeRequest(
      fetchImpl,
      '/api/chat/sessions/session-test/messages/stream',
      { method: 'POST', body: '{}' },
    );

    expect(bridged).toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
