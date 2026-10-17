import { tradingStreamUrl } from '../tradingApi';
import type { TradingStreamMessage } from '../tradingTypes';
import { parseJson, tradingStreamMessageSchema } from '../../../api/schemas/streams';

export type TradingStreamStatus = 'connecting' | 'live' | 'polling' | 'closed' | 'error';

type MessageListener = (message: TradingStreamMessage) => void;
type StatusListener = (status: TradingStreamStatus) => void;

type SocketLike = Pick<WebSocket, 'addEventListener' | 'close'>;
type SocketFactory = (url: string) => SocketLike;

const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 10_000;

type HubEntry = {
  key: string;
  url: string;
  socket: SocketLike | null;
  messages: Map<string, MessageListener>;
  statuses: Map<string, StatusListener>;
  status: TradingStreamStatus;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  reconnectAttempt: number;
};

export class TradingStreamHub {
  private readonly entries = new Map<string, HubEntry>();

  constructor(private readonly socketFactory: SocketFactory = (url) => new WebSocket(url)) {}

  static key(instrumentId: string, interval: string, bindingId?: string | null): string {
    return `${instrumentId}|${interval}|${bindingId ?? 'default'}`;
  }

  subscribe(
    listenerId: string,
    instrumentId: string,
    interval: string,
    onMessage: MessageListener,
    onStatus?: StatusListener,
    bindingId?: string | null,
  ): () => void {
    const key = TradingStreamHub.key(instrumentId, interval, bindingId);
    let entry = this.entries.get(key);
    let created = false;
    if (!entry) {
      entry = {
        key,
        url: tradingStreamUrl(instrumentId, interval, bindingId),
        socket: null,
        messages: new Map(),
        statuses: new Map(),
        status: 'connecting',
        reconnectTimer: null,
        reconnectAttempt: 0,
      };
      this.entries.set(key, entry);
      created = true;
    }
    entry.messages.set(listenerId, onMessage);
    if (created) this.openSocket(entry);
    if (onStatus) {
      entry.statuses.set(listenerId, onStatus);
      onStatus(entry.status);
    }
    return () => {
      const current = this.entries.get(key);
      if (!current) return;
      current.messages.delete(listenerId);
      current.statuses.delete(listenerId);
      if (current.messages.size === 0) {
        if (current.reconnectTimer) clearTimeout(current.reconnectTimer);
        current.reconnectTimer = null;
        const socket = current.socket;
        current.socket = null;
        socket?.close(1000, 'last chart disposed');
        this.entries.delete(key);
      }
    };
  }

  private openSocket(entry: HubEntry): void {
    if (this.entries.get(entry.key) !== entry || entry.messages.size === 0) return;
    let socket: SocketLike;
    try {
      socket = this.socketFactory(entry.url);
    } catch {
      entry.socket = null;
      this.updateStatus(entry, 'error');
      this.scheduleReconnect(entry);
      return;
    }
    entry.socket = socket;
    socket.addEventListener('open', () => {
      if (entry.socket !== socket) return;
      entry.reconnectAttempt = 0;
      this.updateStatus(entry, 'live');
    });
    socket.addEventListener('error', () => {
      if (entry.socket !== socket) return;
      this.updateStatus(entry, 'error');
      this.scheduleReconnect(entry);
    });
    socket.addEventListener('close', () => {
      if (entry.socket !== socket) return;
      entry.socket = null;
      this.updateStatus(entry, 'closed');
      this.scheduleReconnect(entry);
    });
    socket.addEventListener('message', (event: Event) => {
      if (entry.socket !== socket) return;
      const data = 'data' in event ? String((event as MessageEvent).data) : '';
      const message: TradingStreamMessage = (parseJson(tradingStreamMessageSchema, data) as TradingStreamMessage | null)
        ?? { type: 'error', code: 'invalid_stream_message', message: 'Trading stream returned an invalid message.' };
      entry.messages.forEach((listener) => listener(message));
    });
  }

  private scheduleReconnect(entry: HubEntry): void {
    if (this.entries.get(entry.key) !== entry || entry.messages.size === 0 || entry.reconnectTimer) return;
    const delay = Math.min(
      RECONNECT_MAX_DELAY_MS,
      RECONNECT_BASE_DELAY_MS * (2 ** entry.reconnectAttempt),
    );
    entry.reconnectAttempt += 1;
    entry.reconnectTimer = setTimeout(() => {
      entry.reconnectTimer = null;
      if (this.entries.get(entry.key) !== entry || entry.messages.size === 0) return;
      const previousSocket = entry.socket;
      entry.socket = null;
      previousSocket?.close(1000, 'reconnecting');
      this.updateStatus(entry, 'connecting');
      this.openSocket(entry);
    }, delay);
  }

  private updateStatus(entry: HubEntry, status: TradingStreamStatus): void {
    entry.status = status;
    entry.statuses.forEach((listener) => listener(status));
  }

  get upstreamCount(): number {
    return this.entries.size;
  }

  listenerCount(instrumentId: string, interval: string, bindingId?: string | null): number {
    return this.entries.get(TradingStreamHub.key(instrumentId, interval, bindingId))?.messages.size ?? 0;
  }

  dispose(): void {
    this.entries.forEach((entry) => {
      if (entry.reconnectTimer) clearTimeout(entry.reconnectTimer);
      entry.reconnectTimer = null;
      const socket = entry.socket;
      entry.socket = null;
      socket?.close(1000, 'hub disposed');
    });
    this.entries.clear();
  }
}

export const tradingStreamHub = new TradingStreamHub();
