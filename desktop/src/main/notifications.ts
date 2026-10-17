import { createServer, type IncomingMessage, type Server } from 'node:http';
import { timingSafeEqual } from 'node:crypto';

/** One delivery from the gateway's DesktopSender (Omnix S-6). */
export interface DesktopDelivery {
  delivery_id: string;
  event_kind: 'alert' | 'margin_call' | string;
  workspace_id: string;
  title: string;
  message: string;
  alert_id: string | null;
  trigger_id: string | null;
  instrument_id: string | null;
  created_at: string | null;
  url: string;
}

export interface NotificationHandlers {
  /** The workspace of the account signed in to the app now, or null when nobody is. */
  signedInWorkspace(): Promise<string | null>;
  show(title: string, body: string, url: string): void;
}

// Deliveries older than this are from while the computer slept: shown as one summary per burst.
const STALE_AFTER_MS = 15 * 60_000;
const SUMMARY_WINDOW_MS = 20_000;
const MAX_BODY_BYTES = 64 * 1024;

function sameSecret(supplied: string, expected: string): boolean {
  const left = Buffer.from(supplied);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('too large'));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => resolvePromise(Buffer.concat(chunks).toString('utf8')));
    request.on('error', reject);
  });
}

/**
 * The loopback listener the gateway's DesktopSender posts to (roadmap §4.9). It answers 200 once a notification is
 * shown, which marks the outbox row delivered (at least once). Another account's alert shows no content; stale
 * deliveries after a sleep are collected into one summary.
 */
export class NotificationListener {
  private server: Server | null = null;
  private staleCount = 0;
  private staleTimer: NodeJS.Timeout | null = null;

  constructor(private readonly secret: string, private readonly handlers: NotificationHandlers,
              private readonly now: () => number = Date.now) {}

  listen(port: number): Promise<void> {
    this.server = createServer((request, response) => {
      void this.handle(request).then(([status, body]) => {
        response.writeHead(status, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify(body));
      });
    });
    return new Promise((resolvePromise, reject) => {
      this.server!.once('error', reject);
      this.server!.listen({ port, host: '127.0.0.1' }, () => resolvePromise());
    });
  }

  close(): void {
    this.server?.close();
    if (this.staleTimer) clearTimeout(this.staleTimer);
  }

  async handle(request: IncomingMessage): Promise<[number, Record<string, unknown>]> {
    if (request.method !== 'POST' || request.url !== '/notify') return [404, { error: 'not_found' }];
    const authorization = request.headers.authorization ?? '';
    if (!authorization.startsWith('Bearer ') || !sameSecret(authorization.slice(7), this.secret)) {
      return [401, { error: 'unauthorized' }];
    }
    let delivery: DesktopDelivery;
    try {
      delivery = JSON.parse(await readBody(request)) as DesktopDelivery;
    } catch {
      return [400, { error: 'invalid_body' }];
    }
    await this.deliver(delivery);
    return [200, { shown: true }];
  }

  async deliver(delivery: DesktopDelivery): Promise<void> {
    const workspace = await this.handlers.signedInWorkspace();
    if (workspace !== delivery.workspace_id) {
      // Never another account's content on a shared PC; that account sees it in its alert history.
      this.handlers.show(delivery.title, 'An alert fired for another account.', '/trading');
      return;
    }
    const created = delivery.created_at ? Date.parse(delivery.created_at) : this.now();
    if (Number.isFinite(created) && this.now() - created > STALE_AFTER_MS) {
      this.staleCount += 1;
      if (this.staleTimer) clearTimeout(this.staleTimer);
      this.staleTimer = setTimeout(() => this.flushStale(delivery.title), SUMMARY_WINDOW_MS);
      return;
    }
    this.handlers.show(delivery.title, delivery.message, delivery.url || '/trading');
  }

  flushStale(title: string): void {
    const count = this.staleCount;
    this.staleCount = 0;
    this.staleTimer = null;
    if (count === 0) return;
    const product = title.split(' ')[0] || 'MarketPulse';
    this.handlers.show(product, count === 1 ? '1 alert fired while you were away.' : `${count} alerts fired while you were away.`, '/trading');
  }
}

/**
 * The alert badge (roadmap MP-8.2): alerts that arrive while MarketPulse is not in front are counted, and the count
 * clears when the user comes back. `apply` shows it (a taskbar overlay on Windows, the Dock badge on macOS).
 */
export class AlertBadge {
  private count = 0;

  constructor(private readonly apply: (count: number) => void) {}

  alert(focused: boolean): void {
    if (focused) return;
    this.count += 1;
    this.apply(this.count);
  }

  seen(): void {
    if (this.count === 0) return;
    this.count = 0;
    this.apply(0);
  }
}

/** A 16 px red dot (the chart's down colour) for the Windows taskbar overlay, as premultiplied BGRA. */
export function alertDotBitmap(size = 16): Buffer {
  const pixels = Buffer.alloc(size * size * 4);
  const centre = (size - 1) / 2;
  const radius = size / 2 - 1;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const alpha = Math.max(0, Math.min(1, radius + 0.5 - Math.hypot(x - centre, y - centre)));
      const offset = (y * size + x) * 4;
      pixels[offset] = Math.round(0x45 * alpha);
      pixels[offset + 1] = Math.round(0x36 * alpha);
      pixels[offset + 2] = Math.round(0xf2 * alpha);
      pixels[offset + 3] = Math.round(255 * alpha);
    }
  }
  return pixels;
}
