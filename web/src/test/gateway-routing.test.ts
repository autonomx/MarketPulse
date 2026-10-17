// @vitest-environment node
import { createHash } from 'node:crypto';
import { createServer, request, type Server } from 'node:http';
import { once } from 'node:events';
import type { Socket } from 'node:net';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createServer as createViteServer, type ViteDevServer } from 'vite';
import { canUseApi, gatewayRouting, resolveApiOrigins } from '../../gateway-routing';

describe('gateway routing policy', () => {
  it('balances Chat while keeping speech on the worker unless shared TTS is configured', () => {
    for (const url of ['/api/chat/sessions', '/api/chat/sessions/a', '/api/chat/sessions/a/messages']) {
      expect(canUseApi({ url, method: 'POST', headers: {} })).toBe(true);
    }
    const speech = { url: '/api/tts/stream/websocket', method: 'POST', headers: {} };
    expect(canUseApi(speech)).toBe(false);
    expect(canUseApi(speech, true)).toBe(true);
  });
  it('keeps controls, live coordination, and unknown extensions on the worker', () => {
    for (const url of ['/api/trading/monitors/start', '/api/jobs/a/cancel', '/api/jobs/claim',
      '/api/chat/sessions/a/live-call/runtime', '/api/chat/sessions/a/research-mode',
      '/api/tts/live-call/websocket', '/api/settings', '/api/chat/sessions/a/new-extension']) {
      expect(canUseApi({ url, method: 'POST', headers: {} })).toBe(false);
    }
    expect(canUseApi({ url: '/api/chat/sessions/a/messages/stream', method: 'POST',
      headers: { 'x-omnix-gateway-affinity': 'worker' } })).toBe(false);
  });
  it('sends live calls to APIs only with remote TTS and recognizes websocket cookies', () => {
    const liveChat = {
      url: '/api/chat/sessions/session-a/messages/stream',
      method: 'POST',
      headers: { 'x-omnix-call-id': 'session-a' },
    };
    const liveChatWithoutAffinity = { ...liveChat, headers: {} };
    expect(canUseApi(liveChat)).toBe(false);
    expect(canUseApi(liveChat, true)).toBe(true);
    expect(canUseApi(liveChatWithoutAffinity, true)).toBe(false);
    expect(canUseApi({
      ...liveChatWithoutAffinity,
      headers: { cookie: 'omnix_call_affinity=session-a' },
    }, true)).toBe(true);
    expect(canUseApi({
      url: '/api/tts/live-call/websocket',
      method: 'GET',
      headers: { cookie: 'omnix_call_affinity=session-a' },
    }, true)).toBe(true);
    expect(canUseApi({
      url: '/api/tts/live-call/websocket', method: 'GET', headers: {},
    }, true)).toBe(false);
  });
  it('balances only read operations for jobs and market data', () => {
    for (const url of ['/api/jobs', '/api/jobs/a', '/events?after_id=10', '/api/trading/bars']) {
      expect(canUseApi({ url, method: 'GET', headers: {} })).toBe(true);
      expect(canUseApi({ url, method: 'POST', headers: {} })).toBe(false);
    }
  });
  it('honors explicit targets and isolated E2E overrides', () => {
    expect(resolveApiOrigins('http://localhost:8000', '.', { OMNIX_GATEWAY_API_ORIGINS: 'http://localhost:8001,http://localhost:8002' }))
      .toEqual(['http://localhost:8001', 'http://localhost:8002']);
    expect(resolveApiOrigins('http://localhost:8123', '.', { VITE_GATEWAY_ORIGIN: 'http://localhost:8123' })).toEqual([]);
    expect(resolveApiOrigins('http://localhost:8123', '.', { OMNIX_E2E22_GATEWAY_URL: 'http://localhost:8123' })).toEqual([]);
    expect(resolveApiOrigins('http://localhost:8000', '.', { OMNIX_GATEWAY_API_REPLICAS: '2' })).toEqual(['http://localhost:8001', 'http://localhost:8002']);
  });
  it('rejects invalid or credential-bearing deployment targets', () => {
    for (const value of ['http://user:secret@host:8001', 'http://host/path', 'http://localhost:8000', 'http://host:8001,http://host:8001']) {
      expect(() => resolveApiOrigins('http://localhost:8000', '.', { OMNIX_GATEWAY_API_ORIGINS: value })).toThrow();
    }
    for (const value of ['-1', '1.5', '9', 'abc']) {
      expect(() => resolveApiOrigins('http://localhost:8000', '.', { OMNIX_GATEWAY_API_REPLICAS: value })).toThrow();
    }
  });
});

describe('real Vite HTTP, SSE, and WebSocket proxy', () => {
  const backends: Server[] = [];
  const sockets = new Set<Socket>();
  const hits: { label: string; path: string; body: string }[] = [];
  let vite: ViteDevServer;
  let base: string;
  let release: (() => void) | undefined;
  let canceled: Promise<unknown>;

  beforeAll(async () => {
    const targets: string[] = [];
    for (const label of ['worker', 'api-1', 'api-2']) {
      const server = createServer((req, res) => {
        let body = '';
        req.on('data', (chunk) => { body += chunk; });
        req.on('end', () => {
          hits.push({ label, path: req.url!, body });
          if (req.url!.includes('/messages/stream')) {
            res.writeHead(200, { 'Content-Type': 'text/event-stream' });
            res.write('data: first\n\n');
            release = () => res.end('data: done\n\n');
            canceled = once(res, 'close');
          } else if (req.url === '/api/chat/sessions/fail/messages') {
            res.writeHead(503).end('provider unavailable');
          } else res.end(JSON.stringify({ label, path: req.url, body }));
        });
      });
      server.on('connection', (socket) => {
        sockets.add(socket);
        socket.on('close', () => sockets.delete(socket));
      });
      server.on('upgrade', (req, socket) => {
        hits.push({ label, path: req.url!, body: '' });
        const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
        socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
        socket.on('data', (chunk) => socket.write(chunk));
      });
      server.listen(0, '127.0.0.1');
      await once(server, 'listening');
      targets.push(`http://127.0.0.1:${(server.address() as { port: number }).port}`);
      backends.push(server);
    }
    const routing = gatewayRouting(
      targets[0], targets.slice(1),
      { OMNIX_TTS_URL: 'http://127.0.0.1:5101' },
    );
    vite = await createViteServer({ configFile: false, plugins: [routing.plugin],
      server: { host: '127.0.0.1', port: 0, hmr: false, proxy: routing.proxy },
      optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'silent' });
    await vite.listen();
    base = `http://127.0.0.1:${(vite.httpServer!.address() as { port: number }).port}`;
  }, 20000);

  afterAll(async () => {
    await vite?.close();
    for (const socket of sockets) socket.destroy();
    await Promise.all(backends.map((server) => new Promise<void>((done) => server.close(() => done()))));
  });

  it('routes concurrent bodies independently and preserves URLs', async () => {
    const responses = await Promise.all(Array.from({ length: 8 }, (_, i) => fetch(base + '/api/chat/sessions?check=1', {
      method: 'POST', body: String(i), headers: { 'Content-Type': 'application/json' },
    })));
    const values = await Promise.all(responses.map((res) => res.json()));
    expect(values.filter((value) => value.label === 'api-1')).toHaveLength(4);
    expect(values.filter((value) => value.label === 'api-2')).toHaveLength(4);
    expect(values.map((value) => value.body).sort()).toEqual(['0', '1', '2', '3', '4', '5', '6', '7']);
    expect(values.every((value) => value.path === '/api/chat/sessions?check=1')).toBe(true);
    expect(responses.every((res) => res.headers.get('x-omnix-gateway-route')?.startsWith('api-'))).toBe(true);
  });
  it('routes live calls by stable affinity, keeps controls on the worker, and blocks private prefixes', async () => {
    const control = await fetch(base + '/api/trading/monitors/start', { method: 'POST' });
    expect((await control.json()).label).toBe('worker');
    const live = await fetch(base + '/api/chat/sessions/a/messages/stream', {
      method: 'POST', body: JSON.stringify({ live_voice_turn_id: 'turn-a' }),
      headers: { 'X-Omnix-Call-Id': 'affinity-a' },
    });
    const liveLabel = live.headers.get('x-omnix-gateway-route');
    await live.body?.cancel();
    expect(liveLabel).toMatch(/^api-[12]$/u);

    const unpinnedLive = await fetch(base + '/api/chat/sessions/b/messages/stream', {
      method: 'POST', body: JSON.stringify({ live_voice_turn_id: 'turn-b' }),
    });
    expect(unpinnedLive.headers.get('x-omnix-gateway-route')).toBe('worker');
    await unpinnedLive.body?.cancel();
    expect((await fetch(base + '/__omnix_gateway_replica_0/api/settings')).status).toBe(404);
    expect((await fetch(base + '/ready')).headers.get('x-omnix-gateway-route')).toBe('worker');

    const beforeSocket = hits.length;
    const req = request(base + '/api/tts/live-call/websocket', { headers: {
      Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
      'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
      Cookie: 'omnix_call_affinity=affinity-a',
    } });
    const upgraded = once(req, 'upgrade');
    req.end();
    const [response, socket] = await upgraded;
    expect(response.headers['x-omnix-gateway-route']).toBe(liveLabel);
    expect(hits[beforeSocket].label).toBe(liveLabel);
    socket.destroy();
  });
  it('delivers SSE before completion and closes the upstream on cancellation', async () => {
    const response = await fetch(base + '/api/chat/sessions/a/messages/stream', { method: 'POST', body: '{}' });
    const reader = response.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe('data: first\n\n');
    release!();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe('data: done\n\n');
    expect((await reader.read()).done).toBe(true);
    const next = await fetch(base + '/api/chat/sessions/b/messages/stream', { method: 'POST', body: '{}' });
    const second = next.body!.getReader();
    await second.read();
    await second.cancel();
    await canceled;
  });
  it('does not replay a failed write on another replica', async () => {
    const before = hits.length;
    const response = await fetch(base + '/api/chat/sessions/fail/messages', { method: 'POST', body: 'unique' });
    expect(response.status).toBe(503);
    expect(hits.slice(before)).toHaveLength(1);
  });
  it('keeps each upgraded connection on its selected target and preserves bytes', async () => {
    const before = hits.length;
    for (let i = 0; i < 2; i++) {
      const req = request(base + '/api/tts/stream/websocket?test=1', { headers: {
        Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
        'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
      } });
      const upgraded = once(req, 'upgrade');
      req.end();
      const [response, socket] = await upgraded;
      expect(response.headers['x-omnix-gateway-route']).toMatch(/^api-[12]$/u);
      const data = once(socket, 'data');
      const frame = Buffer.from([0x82, 0x82, 1, 2, 3, 4, 0x43, 0x45]);
      socket.write(frame);
      expect((await data)[0]).toEqual(frame);
      socket.destroy();
    }
    const selected = hits.slice(before);
    expect(new Set(selected.map((hit) => hit.label)).size).toBe(2);
    expect(selected.every((hit) => hit.path === '/api/tts/stream/websocket?test=1')).toBe(true);
  });
  it('rejects a private prefix during WebSocket upgrade', async () => {
    const before = hits.length;
    const req = request(base + '/__omnix_gateway_replica_0/api/tts/live-call/websocket', { headers: {
      Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
      'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
    } });
    const response = once(req, 'response');
    req.end();
    expect((await response)[0].statusCode).toBe(404);
    expect(hits).toHaveLength(before);
  });
});
