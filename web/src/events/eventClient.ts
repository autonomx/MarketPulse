import { jobEventSchema } from '../api/schemas/streams';

export type OmnixEventHandler<TPayload = unknown> = (payload: TPayload) => void;

export type OmnixEventConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface OmnixEventConnectionStatus {
  state: OmnixEventConnectionState;
  reconnectAttempt: number;
  nextReconnectDelayMs?: number;
  lastError?: string;
}

export interface OmnixEventSource {
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
  close(): void;
}

export interface OmnixMalformedEvent {
  eventName: string;
  data: string;
  error: unknown;
}

type TimeoutHandle = ReturnType<typeof globalThis.setTimeout>;

export interface OmnixEventClientOptions {
  endpoint?: string;
  eventSourceFactory?: (endpoint: string) => OmnixEventSource;
  initialReconnectDelayMs?: number;
  maxReconnectDelayMs?: number;
  reconnectJitterRatio?: number;
  random?: () => number;
  setTimeout?: (callback: () => void, delayMs: number) => TimeoutHandle;
  clearTimeout?: (handle: TimeoutHandle) => void;
  onMalformedEvent?: (event: OmnixMalformedEvent) => void;
}

const DEFAULT_INITIAL_RECONNECT_DELAY_MS = 500;
const DEFAULT_MAX_RECONNECT_DELAY_MS = 15_000;
const DEFAULT_RECONNECT_JITTER_RATIO = 0.25;

/**
 * Sent by the server before it closes a stream that fell too far behind.
 * The client drops its cursor, reconnects at the live tail, and subscribers
 * to this event refetch their state.
 */
export const RESYNC_EVENT = 'resync';

export class OmnixEventClient {
  private readonly endpoint: string;
  private readonly eventSourceFactory: (endpoint: string) => OmnixEventSource;
  private readonly hasCustomEventSource: boolean;
  private readonly initialReconnectDelayMs: number;
  private readonly maxReconnectDelayMs: number;
  private readonly reconnectJitterRatio: number;
  private readonly random: () => number;
  private readonly setTimeoutFn: (callback: () => void, delayMs: number) => TimeoutHandle;
  private readonly clearTimeoutFn: (handle: TimeoutHandle) => void;
  private readonly onMalformedEvent?: (event: OmnixMalformedEvent) => void;

  private source: OmnixEventSource | null = null;
  private reconnectTimer: TimeoutHandle | null = null;
  private reconnectAttempt = 0;
  private lastEventId: string | null = null;
  private closedByClient = false;
  private status: OmnixEventConnectionStatus = {
    state: 'idle',
    reconnectAttempt: 0,
  };

  private readonly handlers = new Map<string, Set<OmnixEventHandler<unknown>>>();
  private readonly sourceEventListeners = new Map<string, EventListener>();
  private readonly statusHandlers = new Set<OmnixEventHandler<OmnixEventConnectionStatus>>();

  private readonly handleOpen: EventListener = () => {
    this.reconnectAttempt = 0;
    this.setStatus({
      state: 'open',
      reconnectAttempt: 0,
    });
  };

  private readonly handleResync: EventListener = () => {
    this.lastEventId = null;
  };

  private readonly handleError: EventListener = (event) => {
    this.closeSource();

    if (this.closedByClient || !this.hasEventSubscribers()) {
      this.reconnectAttempt = 0;
      this.setStatus({
        state: this.closedByClient ? 'closed' : 'idle',
        reconnectAttempt: 0,
        lastError: this.describeError(event),
      });
      return;
    }

    this.scheduleReconnect(event);
  };

  constructor(options: OmnixEventClientOptions = {}) {
    this.endpoint = options.endpoint ?? '/events';
    this.eventSourceFactory = options.eventSourceFactory ?? ((endpoint) => new EventSource(endpoint));
    this.hasCustomEventSource = Boolean(options.eventSourceFactory);
    this.initialReconnectDelayMs = options.initialReconnectDelayMs ?? DEFAULT_INITIAL_RECONNECT_DELAY_MS;
    this.maxReconnectDelayMs = options.maxReconnectDelayMs ?? DEFAULT_MAX_RECONNECT_DELAY_MS;
    this.reconnectJitterRatio = options.reconnectJitterRatio ?? DEFAULT_RECONNECT_JITTER_RATIO;
    this.random = options.random ?? Math.random;
    this.setTimeoutFn = options.setTimeout ?? globalThis.setTimeout.bind(globalThis);
    this.clearTimeoutFn = options.clearTimeout ?? globalThis.clearTimeout.bind(globalThis);
    this.onMalformedEvent = options.onMalformedEvent;
  }

  getStatus(): OmnixEventConnectionStatus {
    return { ...this.status };
  }

  subscribeStatus(handler: OmnixEventHandler<OmnixEventConnectionStatus>) {
    this.statusHandlers.add(handler);
    handler(this.getStatus());

    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  connect() {
    if (this.source) {
      return this.source;
    }

    this.closedByClient = false;
    this.openConnection('connecting');
    return this.source;
  }

  subscribe<TPayload = unknown>(eventName: string, handler: OmnixEventHandler<TPayload>) {
    this.closedByClient = false;

    let handlersForEvent = this.handlers.get(eventName);
    if (!handlersForEvent) {
      handlersForEvent = new Set();
      this.handlers.set(eventName, handlersForEvent);
      this.bindEventName(eventName);
    }

    handlersForEvent.add(handler as OmnixEventHandler<unknown>);

    if (!this.source && !this.reconnectTimer && this.canConnect()) {
      this.openConnection('connecting');
    }

    return () => {
      this.unsubscribe(eventName, handler as OmnixEventHandler<unknown>);
    };
  }

  close() {
    this.closedByClient = true;
    this.cancelReconnect();
    this.closeSource();
    this.reconnectAttempt = 0;
    this.lastEventId = null;
    this.setStatus({
      state: 'closed',
      reconnectAttempt: 0,
    });
  }

  private unsubscribe(eventName: string, handler: OmnixEventHandler<unknown>) {
    const handlersForEvent = this.handlers.get(eventName);
    if (!handlersForEvent) {
      return;
    }

    handlersForEvent.delete(handler);

    if (handlersForEvent.size === 0) {
      this.handlers.delete(eventName);
      this.unbindEventName(eventName);
    }

    if (!this.hasEventSubscribers()) {
      this.cancelReconnect();
      this.closeSource();
      this.reconnectAttempt = 0;
      this.setStatus({
        state: 'idle',
        reconnectAttempt: 0,
      });
    }
  }

  /** False where the browser has no EventSource (tests, old runtimes); subscribers then see no events. */
  private canConnect(): boolean {
    return this.hasCustomEventSource || typeof EventSource !== 'undefined';
  }

  private openConnection(state: OmnixEventConnectionState) {
    this.cancelReconnect();
    this.closeSource();
    this.source = this.eventSourceFactory(this.connectionEndpoint());
    this.source.addEventListener('open', this.handleOpen);
    this.source.addEventListener('error', this.handleError);
    this.source.addEventListener(RESYNC_EVENT, this.handleResync);

    for (const eventName of this.handlers.keys()) {
      this.bindEventName(eventName);
    }

    this.setStatus({
      state,
      reconnectAttempt: this.reconnectAttempt,
    });
  }

  private connectionEndpoint() {
    if (!this.lastEventId) {
      return this.endpoint;
    }

    const [endpointWithoutHash, hashFragment] = this.endpoint.split('#', 2);
    const [path, queryString = ''] = endpointWithoutHash.split('?', 2);
    const params = new URLSearchParams(queryString);
    params.set('after_id', this.lastEventId);
    const query = params.toString();
    const hashSuffix = hashFragment === undefined ? '' : `#${hashFragment}`;

    return `${path}${query ? `?${query}` : ''}${hashSuffix}`;
  }

  private bindEventName(eventName: string) {
    if (!this.source) {
      return;
    }

    const listener = this.getSourceEventListener(eventName);
    this.source.addEventListener(eventName, listener);
  }

  private unbindEventName(eventName: string) {
    const listener = this.sourceEventListeners.get(eventName);
    if (!listener) {
      return;
    }

    this.source?.removeEventListener(eventName, listener);
    this.sourceEventListeners.delete(eventName);
  }

  private getSourceEventListener(eventName: string) {
    let listener = this.sourceEventListeners.get(eventName);
    if (listener) {
      return listener;
    }

    listener = (event: Event) => {
      const message = event as MessageEvent<string>;
      let payload: unknown;

      if (eventName !== RESYNC_EVENT) {
        this.rememberEventId(message.lastEventId);
      }

      try {
        payload = JSON.parse(message.data);
      } catch (error) {
        this.onMalformedEvent?.({
          eventName,
          data: message.data,
          error,
        });
        return;
      }
      if (eventName.startsWith('job.')) {
        const checked = jobEventSchema.safeParse(payload);
        if (!checked.success) {
          this.onMalformedEvent?.({ eventName, data: message.data, error: checked.error });
          return;
        }
      }

      const handlersForEvent = this.handlers.get(eventName);
      if (!handlersForEvent) {
        return;
      }

      for (const handler of [...handlersForEvent]) {
        handler(payload);
      }
    };

    this.sourceEventListeners.set(eventName, listener);
    return listener;
  }

  private rememberEventId(eventId: string) {
    if (eventId) {
      this.lastEventId = eventId;
    }
  }

  private scheduleReconnect(error: Event) {
    this.cancelReconnect();
    this.reconnectAttempt += 1;
    const delayMs = this.getReconnectDelayMs(this.reconnectAttempt);

    this.setStatus({
      state: 'reconnecting',
      reconnectAttempt: this.reconnectAttempt,
      nextReconnectDelayMs: delayMs,
      lastError: this.describeError(error),
    });

    this.reconnectTimer = this.setTimeoutFn(() => {
      this.reconnectTimer = null;

      if (this.closedByClient || !this.hasEventSubscribers()) {
        this.reconnectAttempt = 0;
        this.setStatus({
          state: this.closedByClient ? 'closed' : 'idle',
          reconnectAttempt: 0,
        });
        return;
      }

      this.openConnection('connecting');
    }, delayMs);
  }

  private getReconnectDelayMs(attempt: number) {
    const exponentialDelay = Math.min(
      this.initialReconnectDelayMs * 2 ** Math.max(0, attempt - 1),
      this.maxReconnectDelayMs,
    );
    const jitter = Math.round(exponentialDelay * this.reconnectJitterRatio * this.random());

    return exponentialDelay + jitter;
  }

  private cancelReconnect() {
    if (!this.reconnectTimer) {
      return;
    }

    this.clearTimeoutFn(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private closeSource() {
    if (!this.source) {
      return;
    }

    this.source.removeEventListener('open', this.handleOpen);
    this.source.removeEventListener('error', this.handleError);
    this.source.removeEventListener(RESYNC_EVENT, this.handleResync);

    for (const [eventName, listener] of this.sourceEventListeners) {
      this.source.removeEventListener(eventName, listener);
    }

    this.source.close();
    this.source = null;
  }

  private hasEventSubscribers() {
    for (const handlersForEvent of this.handlers.values()) {
      if (handlersForEvent.size > 0) {
        return true;
      }
    }

    return false;
  }

  private setStatus(status: OmnixEventConnectionStatus) {
    this.status = { ...status };

    for (const handler of [...this.statusHandlers]) {
      handler(this.getStatus());
    }
  }

  private describeError(error: unknown) {
    if (error instanceof Error) {
      return error.message;
    }

    if (error instanceof Event) {
      return error.type;
    }

    return String(error);
  }
}

export const omnixEventClient = new OmnixEventClient();
