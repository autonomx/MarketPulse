import { expect, test } from '@playwright/test';
import { request } from 'node:http';
import { classify } from '../src/main/navigation';
import { AlertBadge, alertDotBitmap, NotificationListener, type DesktopDelivery } from '../src/main/notifications';
import { APP_ID, appUserModelId } from '../src/main/identity';
import { isMarketHours } from '../src/main/marketHours';
import { rotateLog } from '../src/main/supervisor';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const ORIGIN = 'http://127.0.0.1:47800';

test.describe('navigation rules (roadmap §4.4)', () => {
  test('the app origin stays, its popups become windows, the web opens outside, the rest is refused', () => {
    expect(classify(`${ORIGIN}/trading`, ORIGIN, 'navigate')).toBe('stay');
    expect(classify(`${ORIGIN}/trading?window=a`, ORIGIN, 'window-open')).toBe('window');
    expect(classify('https://www.sec.gov/filing', ORIGIN, 'window-open')).toBe('open-external');
    expect(classify('http://news.example.com/', ORIGIN, 'navigate')).toBe('open-external');
    expect(classify('file:///C:/Windows/win.ini', ORIGIN, 'window-open')).toBe('deny');
    expect(classify('javascript:alert(1)', ORIGIN, 'navigate')).toBe('deny');
    expect(classify('http://127.0.0.1:8000/api/admin', ORIGIN, 'window-open')).toBe('deny');
    expect(classify('http://localhost:5432/', ORIGIN, 'navigate')).toBe('deny');
    expect(classify('https://user:pw@example.com/', ORIGIN, 'navigate')).toBe('deny');
    expect(classify('not a url', ORIGIN, 'navigate')).toBe('deny');
  });
});

function delivery(changes: Partial<DesktopDelivery> = {}): DesktopDelivery {
  return {
    delivery_id: 'd1', event_kind: 'alert', workspace_id: 'workspace:local', title: 'MarketPulse alert',
    message: 'AAPL crossed 200', alert_id: 'a1', trigger_id: 't1', instrument_id: 'equity:NASDAQ:AAPL',
    created_at: new Date().toISOString(), url: '/trading?alert=a1', ...changes,
  };
}

function post(port: number, body: string, secret: string): Promise<number> {
  return new Promise((resolvePromise, reject) => {
    const outgoing = request({ host: '127.0.0.1', port, path: '/notify', method: 'POST',
      headers: { Authorization: `Bearer ${secret}`, 'Content-Type': 'application/json' } }, (response) => {
      response.resume();
      resolvePromise(response.statusCode ?? 0);
    });
    outgoing.on('error', reject);
    outgoing.end(body);
  });
}

test.describe('desktop notification listener (roadmap §4.9)', () => {
  test('shows a delivery for the signed-in account and answers 200; refuses a wrong secret', async () => {
    const shown: Array<[string, string, string]> = [];
    const listener = new NotificationListener('s3cret', {
      signedInWorkspace: async () => 'workspace:local',
      show: (title, body, url) => shown.push([title, body, url]),
    });
    await listener.listen(47960);
    try {
      expect(await post(47960, JSON.stringify(delivery()), 'wrong')).toBe(401);
      expect(shown).toEqual([]);
      expect(await post(47960, JSON.stringify(delivery()), 's3cret')).toBe(200);
      expect(shown).toEqual([['MarketPulse alert', 'AAPL crossed 200', '/trading?alert=a1']]);
      expect(await post(47960, 'not json', 's3cret')).toBe(400);
    } finally {
      listener.close();
    }
  });

  test('never shows another account its content', async () => {
    const shown: string[] = [];
    const listener = new NotificationListener('s', { signedInWorkspace: async () => 'workspace:other', show: (_t, body) => shown.push(body) });

    await listener.deliver(delivery());

    expect(shown).toEqual(['An alert fired for another account.']);
  });

  test('collects deliveries from while the computer slept into one summary', async () => {
    const shown: string[] = [];
    const now = Date.parse('2026-10-19T14:00:00Z');
    const listener = new NotificationListener('s', { signedInWorkspace: async () => 'workspace:local', show: (_t, body) => shown.push(body) }, () => now);
    const stale = new Date(now - 3 * 60 * 60_000).toISOString();

    await listener.deliver(delivery({ created_at: stale }));
    await listener.deliver(delivery({ created_at: stale, delivery_id: 'd2' }));
    await listener.deliver(delivery({ created_at: stale, delivery_id: 'd3' }));
    expect(shown).toEqual([]);
    listener.flushStale('MarketPulse alert');

    expect(shown).toEqual(['3 alerts fired while you were away.']);
    listener.close();
  });
});

test('the alert badge counts alerts that arrive while MarketPulse is not in front, and clears when it is', () => {
  const shown: number[] = [];
  const badge = new AlertBadge((count) => shown.push(count));

  badge.alert(true);
  badge.alert(false);
  badge.alert(false);
  badge.seen();
  badge.seen();
  badge.alert(false);

  expect(shown).toEqual([1, 2, 0, 1]);
  const dot = alertDotBitmap(16);
  expect(dot.length).toBe(16 * 16 * 4);
  expect([...dot.subarray((8 * 16 + 8) * 4, (8 * 16 + 8) * 4 + 4)]).toEqual([0x45, 0x36, 0xf2, 255]);
  expect(dot[3]).toBe(0); // a transparent corner
});

test('the shell rotates supervisor.log past its size, keeping three', () => {
  const folder = mkdtempSync(join(tmpdir(), 'marketpulse-log-'));
  const log = join(folder, 'supervisor.log');
  for (const generation of ['a', 'b', 'c', 'd', 'e']) {
    writeFileSync(log, generation.repeat(20));
    rotateLog(log, 10, 3);
  }
  writeFileSync(log, 'short');
  rotateLog(log, 10, 3);

  expect(readdirSync(folder).sort()).toEqual(['supervisor.log', 'supervisor.log.1', 'supervisor.log.2', 'supervisor.log.3']);
  expect(readFileSync(join(folder, 'supervisor.log.1'), 'utf8')[0]).toBe('e');
  expect(readFileSync(join(folder, 'supervisor.log.3'), 'utf8')[0]).toBe('c');
  rmSync(folder, { recursive: true, force: true });
});

test('market hours cover 04:00 to 20:00 New York time on weekdays', () => {
  expect(isMarketHours(new Date('2026-10-19T08:30:00Z'))).toBe(true); // Monday 04:30 EDT
  expect(isMarketHours(new Date('2026-10-19T07:30:00Z'))).toBe(false); // Monday 03:30 EDT
  expect(isMarketHours(new Date('2026-10-20T00:30:00Z'))).toBe(false); // Monday 20:30 EDT
  expect(isMarketHours(new Date('2026-10-18T15:00:00Z'))).toBe(false); // Sunday
});

test('a development run has its own AppUserModelID, so its shortcut never gives the installed app its icon', () => {
  const builderAppId = /^appId:\s*(\S+)/m.exec(readFileSync(join(__dirname, '..', 'electron-builder.yml'), 'utf8'))?.[1];
  expect(APP_ID).toBe(builderAppId);
  expect(appUserModelId(true)).toBe(APP_ID);
  expect(appUserModelId(false)).toBe(`${APP_ID}.dev`);
});
