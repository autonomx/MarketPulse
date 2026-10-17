import { expect, test } from '@playwright/test';
import { request } from 'node:http';
import { classify } from '../src/main/navigation';
import { NotificationListener, type DesktopDelivery } from '../src/main/notifications';
import { isMarketHours } from '../src/main/marketHours';

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

test('market hours cover 04:00 to 20:00 New York time on weekdays', () => {
  expect(isMarketHours(new Date('2026-10-19T08:30:00Z'))).toBe(true); // Monday 04:30 EDT
  expect(isMarketHours(new Date('2026-10-19T07:30:00Z'))).toBe(false); // Monday 03:30 EDT
  expect(isMarketHours(new Date('2026-10-20T00:30:00Z'))).toBe(false); // Monday 20:30 EDT
  expect(isMarketHours(new Date('2026-10-18T15:00:00Z'))).toBe(false); // Sunday
});
