import { afterEach, describe, expect, it, vi } from 'vitest';
import { OmnixEventClient, type OmnixEventClientOptions, type OmnixEventSource } from './eventClient';

class FakeEventSource implements OmnixEventSource {
  readonly listeners = new Map<string, Set<EventListener>>();
  closed = false;

  constructor(readonly endpoint: string) {}

  addEventListener(type: string, listener: EventListener) {
    const listenersForType = this.listeners.get(type) ?? new Set<EventListener>();
    listenersForType.add(listener);
    this.listeners.set(type, listenersForType);
  }

  removeEventListener(type: string, listener: EventListener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  listenerCount(type: string) {
    return this.listeners.get(type)?.size ?? 0;
  }

  emitOpen() {
    this.emit('open', new Event('open'));
  }

  emitError() {
    this.emit('error', new Event('error'));
  }

  emitMessage(type: string, data: string, lastEventId = '') {
    this.emit(type, new MessageEvent(type, { data, lastEventId }));
  }

  private emit(type: string, event: Event) {
    for (const listener of [...(this.listeners.get(type) ?? [])]) {
      listener(event);
    }
  }
}

function createClient(options: Partial<OmnixEventClientOptions> = {}) {
  const sources: FakeEventSource[] = [];
  const client = new OmnixEventClient({
    endpoint: '/events',
    eventSourceFactory: (endpoint) => {
      const source = new FakeEventSource(endpoint);
      sources.push(source);
      return source;
    },
    initialReconnectDelayMs: 100,
    maxReconnectDelayMs: 1_000,
    reconnectJitterRatio: 0,
    ...options,
  });

  return { client, sources };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('OmnixEventClient', () => {
  it('subscribes, parses named events, reports status, and closes after unsubscribe', () => {
    const { client, sources } = createClient();
    const statuses: string[] = [];
    const handler = vi.fn();

    client.subscribeStatus((status) => statuses.push(status.state));
    const unsubscribe = client.subscribe('job.updated', handler);

    expect(sources).toHaveLength(1);
    expect(sources[0].endpoint).toBe('/events');
    expect(sources[0].listenerCount('job.updated')).toBe(1);
    expect(statuses).toEqual(['idle', 'connecting']);

    sources[0].emitOpen();
    sources[0].emitMessage('job.updated', '{"id":1,"job_id":"job-1","event_type":"job.updated","payload":{"progress":0.5}}');

    expect(handler).toHaveBeenCalledWith({ id: 1, job_id: 'job-1', event_type: 'job.updated', payload: { progress: 0.5 } });
    expect(client.getStatus().state).toBe('open');

    unsubscribe();

    expect(sources[0].closed).toBe(true);
    expect(client.getStatus()).toEqual({ state: 'idle', reconnectAttempt: 0 });
  });

  it('multiplexes multiple named events over one source', () => {
    const { client, sources } = createClient();
    const updatedHandler = vi.fn();
    const completedHandler = vi.fn();

    client.subscribe('job.updated', updatedHandler);
    client.subscribe('job.completed', completedHandler);

    expect(sources).toHaveLength(1);
    expect(sources[0].listenerCount('job.updated')).toBe(1);
    expect(sources[0].listenerCount('job.completed')).toBe(1);

    sources[0].emitMessage('job.updated', '{"id":2,"job_id":"job-2"}');
    sources[0].emitMessage('job.completed', '{"id":3,"job_id":"job-2","payload":{"status":"completed"}}');

    expect(updatedHandler).toHaveBeenCalledWith({ id: 2, job_id: 'job-2' });
    expect(completedHandler).toHaveBeenCalledWith({ id: 3, job_id: 'job-2', payload: { status: 'completed' } });
  });

  it('supports multiple listeners for one named event', () => {
    const { client, sources } = createClient();
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const unsubscribeFirst = client.subscribe('asset.created', firstHandler);
    client.subscribe('asset.created', secondHandler);

    expect(sources[0].listenerCount('asset.created')).toBe(1);

    sources[0].emitMessage('asset.created', '{"assetId":"asset-1"}');
    unsubscribeFirst();
    sources[0].emitMessage('asset.created', '{"assetId":"asset-2"}');

    expect(firstHandler).toHaveBeenCalledTimes(1);
    expect(secondHandler).toHaveBeenCalledTimes(2);
  });

  it('reports malformed JSON without calling subscribers', () => {
    const malformedHandler = vi.fn();
    const { client, sources } = createClient({ onMalformedEvent: malformedHandler });
    const handler = vi.fn();

    client.subscribe('job.updated', handler);
    sources[0].emitMessage('job.updated', '{not-json');

    expect(handler).not.toHaveBeenCalled();
    expect(malformedHandler).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: 'job.updated',
        data: '{not-json',
      }),
    );
  });

  it('reports job events without a job id as malformed', () => {
    const malformedHandler = vi.fn();
    const { client, sources } = createClient({ onMalformedEvent: malformedHandler });
    const handler = vi.fn();

    client.subscribe('job.updated', handler);
    sources[0].emitMessage('job.updated', '{"id":"job-1","progress":0.5}');

    expect(handler).not.toHaveBeenCalled();
    expect(malformedHandler).toHaveBeenCalledWith(expect.objectContaining({ eventName: 'job.updated' }));
  });

  it('still resumes after malformed JSON events with SSE ids', () => {
    vi.useFakeTimers();

    const malformedHandler = vi.fn();
    const { client, sources } = createClient({ onMalformedEvent: malformedHandler });

    client.subscribe('job.updated', vi.fn());
    sources[0].emitMessage('job.updated', '{not-json', '99');
    sources[0].emitError();

    vi.runOnlyPendingTimers();

    expect(malformedHandler).toHaveBeenCalledOnce();
    expect(sources[1].endpoint).toBe('/events?after_id=99');
  });

  it('reconnects with backoff and rebinds named-event listeners', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient();
    const handler = vi.fn();

    client.subscribe('job.updated', handler);
    sources[0].emitOpen();
    sources[0].emitError();

    expect(sources[0].closed).toBe(true);
    expect(client.getStatus()).toEqual({
      state: 'reconnecting',
      reconnectAttempt: 1,
      nextReconnectDelayMs: 100,
      lastError: 'error',
    });

    vi.advanceTimersByTime(99);
    expect(sources).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(sources).toHaveLength(2);
    expect(client.getStatus().state).toBe('connecting');

    sources[1].emitOpen();
    sources[1].emitMessage('job.updated', '{"id":4,"job_id":"job-3"}');

    expect(client.getStatus()).toEqual({ state: 'open', reconnectAttempt: 0 });
    expect(sources[1].listenerCount('job.updated')).toBe(1);
    expect(handler).toHaveBeenCalledWith({ id: 4, job_id: 'job-3' });
  });

  it('resumes reconnects from the last delivered SSE id', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient();

    client.subscribe('job.completed', vi.fn());
    sources[0].emitOpen();
    sources[0].emitMessage('job.completed', '{"id":42,"job_id":"job-7"}', '42');
    sources[0].emitError();

    vi.runOnlyPendingTimers();

    expect(sources).toHaveLength(2);
    expect(sources[1].endpoint).toBe('/events?after_id=42');
  });

  it('appends resume cursors to endpoints that already have query strings', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient({ endpoint: '/events?stream=jobs' });

    client.subscribe('job.completed', vi.fn());
    sources[0].emitMessage('job.completed', '{"id":42,"job_id":"job-7"}', '42');
    sources[0].emitError();

    vi.runOnlyPendingTimers();

    expect(sources[1].endpoint).toBe('/events?stream=jobs&after_id=42');
  });

  it('replaces existing resume cursors instead of duplicating after_id params', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient({ endpoint: '/events?after_id=5&stream=jobs' });

    client.subscribe('job.completed', vi.fn());
    sources[0].emitMessage('job.completed', '{"id":42,"job_id":"job-7"}', '42');
    sources[0].emitError();

    vi.runOnlyPendingTimers();

    expect(sources[1].endpoint).toBe('/events?after_id=42&stream=jobs');
  });

  it('reconnects at the live tail and notifies subscribers after a resync', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient();
    const resync = vi.fn();
    client.subscribe('job.updated', vi.fn());
    client.subscribe('resync', resync);
    sources[0].emitMessage('job.updated', '{"id":42,"job_id":"job-7"}', '7:42');
    sources[0].emitMessage('resync', '{}', '7:42');
    sources[0].emitError();

    vi.runOnlyPendingTimers();

    expect(resync).toHaveBeenCalledOnce();
    expect(sources[1].endpoint).toBe('/events');
  });

  it('clears the resume cursor when explicitly closed', () => {
    const { client, sources } = createClient();

    client.subscribe('job.completed', vi.fn());
    sources[0].emitMessage('job.completed', '{"id":42,"job_id":"job-7"}', '42');
    client.close();
    client.connect();

    expect(sources).toHaveLength(2);
    expect(sources[1].endpoint).toBe('/events');
  });

  it('cancels pending reconnect when closed', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient();

    client.subscribe('job.updated', vi.fn());
    sources[0].emitError();
    client.close();
    vi.runAllTimers();

    expect(sources).toHaveLength(1);
    expect(client.getStatus()).toEqual({ state: 'closed', reconnectAttempt: 0 });
  });

  it('does not reconnect after the final event subscriber unsubscribes', () => {
    vi.useFakeTimers();

    const { client, sources } = createClient();

    const unsubscribe = client.subscribe('job.updated', vi.fn());
    sources[0].emitError();
    unsubscribe();
    vi.runAllTimers();

    expect(sources).toHaveLength(1);
    expect(client.getStatus()).toEqual({ state: 'idle', reconnectAttempt: 0 });
  });
});
