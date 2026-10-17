import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Phase 7 gate, the shell's part of desktop Google sign-in (S-5): the window's button opens the system browser on
// the gateway's begin link, which sends it to Google with this build's client, PKCE and the loopback callback, while
// the window waits for its handoff. The callback and handoff legs run against a local identity provider in
// src/tests/persistence/test_auth_desktop_google_integration.py; only Google's own servers are not exercised.
const DESKTOP = resolve(__dirname, '..');
const STAGE = resolve(DESKTOP, '..', 'build', 'stage');
const CLIENT_ID = 'marketpulse-test.apps.googleusercontent.com';

let app: ElectronApplication;
let dataDir: string;

test.beforeAll(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'marketpulse-google-'));
  const client = join(dataDir, 'google-oauth.json');
  writeFileSync(client, JSON.stringify({ client_id: CLIENT_ID, client_secret: 'test-secret' }));
  app = await electron.launch({
    args: [join(DESKTOP, 'out', 'main.js')],
    env: { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => name !== 'ELECTRON_RUN_AS_NODE')) as Record<string, string>,
      MARKETPULSE_DATA_DIR: join(dataDir, 'data'), MARKETPULSE_RESOURCES: STAGE, MARKETPULSE_GOOGLE_OAUTH: client },
  });
});

test.afterAll(async () => {
  await app?.close();
  rmSync(dataDir, { recursive: true, force: true, maxRetries: 5 });
});

async function welcomePage(): Promise<Page> {
  const deadline = Date.now() + 300_000;
  while (Date.now() < deadline) {
    const page = app.windows().find((candidate) => candidate.url().includes('/welcome'));
    if (page) return page;
    await new Promise((resolveLater) => setTimeout(resolveLater, 500));
  }
  throw new Error('no welcome page');
}

test('Continue with Google opens the system browser on a begin link that goes to Google with this client', async () => {
  const main = await welcomePage();
  const origin = new URL(main.url()).origin;
  await main.getByLabel('Your name').fill('Owner');
  await main.getByLabel('Email').fill('owner@marketpulse.test');
  await main.getByRole('textbox', { name: 'Password' }).fill('a long first passphrase');
  await main.getByRole('button', { name: 'Save and continue' }).click();
  await main.waitForURL('**/trading**');

  // Signed out, the login page offers Google through the system browser.
  await main.evaluate(async () => {
    const csrf = decodeURIComponent((document.cookie.split('; ').find((part) => part.startsWith('omnix_csrf=')) ?? '').split('=')[1] ?? '');
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-Omnix-Client': 'web', 'X-Omnix-CSRF': csrf } });
  });
  await app.evaluate(({ shell }) => {
    const opened: string[] = [];
    Object.assign(globalThis, { opened });
    (shell as unknown as { openExternal: (url: string) => Promise<void> }).openExternal = async (url: string) => {
      opened.push(url);
    };
  });
  await main.goto(`${origin}/login`);
  const session = await main.evaluate(async () => (await (await fetch('/api/auth/session')).json()).options);
  expect(session).toMatchObject({ google: true, google_desktop: true });
  await main.getByRole('button', { name: 'Continue with Google' }).click();
  await expect(main.getByRole('status').filter({ hasText: 'Finish signing in with Google in your browser' })).toBeVisible();

  const opened = await expect.poll(() => app.evaluate(() => (globalThis as unknown as { opened: string[] }).opened), { timeout: 30_000 })
    .toHaveLength(1).then(() => app.evaluate(() => (globalThis as unknown as { opened: string[] }).opened));
  const begin = new URL(opened[0]);
  expect(begin.origin).toBe(origin);
  expect(begin.pathname).toBe('/api/auth/google/desktop/begin');

  // The system browser: no cookies of the window's; the gateway sends it to Google.
  const redirected = await fetch(begin, { redirect: 'manual' });
  expect(redirected.status).toBe(302);
  const google = new URL(redirected.headers.get('location') ?? '');
  expect(google.origin).toBe('https://accounts.google.com');
  expect(google.searchParams.get('client_id')).toBe(CLIENT_ID);
  expect(google.searchParams.get('redirect_uri')).toBe(`${origin}/api/auth/google/callback`);
  expect(google.searchParams.get('code_challenge_method')).toBe('S256');
  expect(google.searchParams.get('code_challenge')).toBeTruthy();
  expect(google.searchParams.get('state')).toBeTruthy();
  // The begin link works once; the window is still signed out and waiting for its handoff.
  expect((await fetch(begin, { redirect: 'manual' })).status).toBe(400);
  expect(await main.evaluate(async () => (await (await fetch('/api/auth/session')).json()).authenticated)).toBe(false);
});
