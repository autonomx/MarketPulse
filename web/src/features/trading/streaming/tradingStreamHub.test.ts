import { describe, expect, it, vi } from 'vitest';
import { TradingStreamHub } from './tradingStreamHub';

class FakeSocket {
  listeners = new Map<string, Array<(event: Event) => void>>();
  closeCalls = 0;

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = typeof listener === 'function' ? listener : (event: Event) => listener.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  close() { this.closeCalls += 1; }

  emit(type: string, event: Event) {
    (this.listeners.get(type) ?? []).forEach((listener) => listener(event));
  }
}

describe('Trading stream hub', () => {
  it('uses one socket for identical chart streams and fans out messages', () => {
    const sockets: FakeSocket[] = [];
    const hub = new TradingStreamHub(() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    });
    const received: string[] = [];
    const first = hub.subscribe('chart-1', 'btc', '1m', () => received.push('one'));
    const second = hub.subscribe('chart-2', 'btc', '1m', () => received.push('two'));
    expect(sockets).toHaveLength(1);
    expect(hub.upstreamCount).toBe(1);
    expect(hub.listenerCount('btc', '1m')).toBe(2);

    sockets[0].emit('message', new MessageEvent('message', { data: JSON.stringify({ type: 'error', code: 'x', message: 'test' }) }));
    expect(received).toEqual(['one', 'two']);
    first();
    expect(sockets[0].closeCalls).toBe(0);
    second();
    expect(sockets[0].closeCalls).toBe(1);
    expect(hub.upstreamCount).toBe(0);
  });

  it('keeps different intervals on separate sockets', () => {
    let sockets = 0;
    const hub = new TradingStreamHub(() => {
      sockets += 1;
      return new FakeSocket() as unknown as WebSocket;
    });
    hub.subscribe('one', 'btc', '1m', () => undefined);
    hub.subscribe('two', 'btc', '5m', () => undefined);
    expect(sockets).toBe(2);
    expect(hub.upstreamCount).toBe(2);
    hub.dispose();
    expect(hub.upstreamCount).toBe(0);
  });

  it('reconnects a closed socket while charts are still subscribed', () => {
    vi.useFakeTimers();
    try {
      const sockets: FakeSocket[] = [];
      const statuses: string[] = [];
      const received: string[] = [];
      const hub = new TradingStreamHub(() => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      });
      const unsubscribe = hub.subscribe(
        'chart-1',
        'btc',
        '1m',
        (message) => received.push(message.type),
        (status) => statuses.push(status),
      );

      sockets[0].emit('close', new Event('close'));
      expect(statuses).toEqual(['connecting', 'closed']);
      vi.advanceTimersByTime(500);
      expect(sockets).toHaveLength(2);
      expect(statuses.at(-1)).toBe('connecting');

      sockets[1].emit('open', new Event('open'));
      sockets[1].emit('message', new MessageEvent('message', {
        data: JSON.stringify({ type: 'error', code: 'test', message: 'test' }),
      }));
      expect(statuses.at(-1)).toBe('live');
      expect(received).toEqual(['error']);
      unsubscribe();
    } finally {
      vi.useRealTimers();
    }
  });
});
