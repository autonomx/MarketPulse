import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const DESKTOP = resolve(__dirname, '..');
const STAGE = resolve(DESKTOP, '..', 'build', 'stage');
const PASSWORD = 'a long first passphrase';

function backendProcesses(dataDir: string): string[] {
  const out = execFileSync('powershell', ['-NoProfile', '-Command',
    "Get-CimInstance Win32_Process -Filter \"Name='postgres.exe' or Name='python.exe'\" | ForEach-Object { $_.CommandLine }"],
  { encoding: 'utf8' });
  const needle = dataDir.toLowerCase();
  return out.split(/\r?\n/).filter((line) => line.toLowerCase().includes(needle) || line.toLowerCase().includes(needle.replaceAll('\\', '/')));
}

async function appWindow(app: ElectronApplication, matcher: (url: string) => boolean, timeout = 300_000): Promise<Page> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const page = app.windows().find((candidate) => matcher(candidate.url()));
    if (page) return page;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
  }
  throw new Error(`no window matched; open: ${app.windows().map((page) => page.url()).join(', ')}`);
}

test.describe.configure({ mode: 'serial' });

let app: ElectronApplication;
let dataDir: string;
let main: Page;
let origin: string;

test.beforeAll(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'marketpulse-smoke-'));
  app = await electron.launch({
    args: [join(DESKTOP, 'out', 'main.js')],
    // Without ELECTRON_RUN_AS_NODE (an editor's extension host sets it), Electron would start as plain Node.
    env: { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => name !== 'ELECTRON_RUN_AS_NODE')) as Record<string, string>,
      MARKETPULSE_DATA_DIR: dataDir, MARKETPULSE_RESOURCES: STAGE },
  });
});

test.afterAll(async () => {
  try {
    await app?.close();
  } catch {
    // Already closed by the quit test.
  }
  rmSync(dataDir, { recursive: true, force: true, maxRetries: 5 });
});

test('first run goes through the splash to the welcome page, then to the charts', async () => {
  const splash = await appWindow(app, (url) => url.startsWith('file:') && url.includes('splash.html'), 30_000);
  await expect(splash.locator('h1')).toHaveText('MarketPulse');
  main = await appWindow(app, (url) => url.startsWith('http://127.0.0.1') && url.includes('/welcome'));
  origin = new URL(main.url()).origin;
  await expect(main.getByRole('heading', { name: 'Welcome to MarketPulse' })).toBeVisible();
  expect(await main.evaluate(() => typeof (window as unknown as { marketPulse?: { requestLoginCode?: unknown } }).marketPulse?.requestLoginCode)).toBe('function');

  await main.getByLabel('Your name').fill('Owner');
  await main.getByLabel('Email').fill('owner@marketpulse.test');
  await main.getByRole('textbox', { name: 'Password' }).fill(PASSWORD);
  await main.getByRole('button', { name: 'Save and continue' }).click();

  await main.waitForURL('**/trading**');
  await expect(main.locator('.trading-workspace')).toBeVisible({ timeout: 120_000 });
  // The session tabs live in the frameless window's title bar (W-4).
  await expect(main.locator('.trading-title-bar .trading-session-tabs')).toBeVisible();
  expect(await main.title()).toBe('MarketPulse');
});

test('a window set opens a native window on the same session that hears BroadcastChannel', async () => {
  const before = app.windows().length;
  await main.evaluate(() => {
    const channel = new BroadcastChannel('marketpulse-smoke');
    (window as unknown as { heard: string[] }).heard = [];
    channel.onmessage = (event) => (window as unknown as { heard: string[] }).heard.push(String(event.data));
    window.open(`${location.origin}/trading?window=smoke`, 'omnix-trading-smoke', 'popup');
  });
  const second = await appWindow(app, (url) => url.includes('window=smoke'), 60_000);
  expect(app.windows().length).toBe(before + 1);
  await second.waitForLoadState('domcontentloaded');
  expect(await second.evaluate(async () => (await (await fetch('/api/auth/session')).json()).authenticated)).toBe(true);
  await second.evaluate(() => new BroadcastChannel('marketpulse-smoke').postMessage('hello from the second window'));
  await expect.poll(() => main.evaluate(() => (window as unknown as { heard: string[] }).heard)).toContain('hello from the second window');
  // The same window name reuses its window.
  await main.evaluate(() => window.open(`${location.origin}/trading?window=smoke&again=1`, 'omnix-trading-smoke'));
  await expect.poll(() => app.windows().length).toBe(before + 1);
  await second.close();
});

test('links leave the app only for the system browser, and navigation stays on the app', async () => {
  await app.evaluate(({ shell }) => {
    (globalThis as unknown as { opened: string[] }).opened = [];
    shell.openExternal = async (url: string) => {
      (globalThis as unknown as { opened: string[] }).opened.push(url);
    };
  });
  const before = app.windows().length;
  await main.evaluate(() => window.open('https://example.com/news', '_blank'));
  await main.evaluate(() => window.open('file:///C:/Windows/win.ini', '_blank'));
  await expect.poll(() => app.evaluate(() => (globalThis as unknown as { opened: string[] }).opened)).toEqual(['https://example.com/news']);
  expect(app.windows().length).toBe(before);
  await main.evaluate(() => {
    location.href = 'https://example.com/elsewhere';
  });
  await main.waitForTimeout(1_000);
  expect(new URL(main.url()).origin).toBe(origin);
});

test('the window can be dragged by the title bar strip but not by a tab', async () => {
  const regions = await main.evaluate(() => {
    const strip = document.querySelector('.trading-title-bar-drag');
    const tab = document.querySelector('.trading-title-bar .trading-session-tab');
    const region = (element: Element | null) => (element ? getComputedStyle(element).getPropertyValue('app-region') || getComputedStyle(element).getPropertyValue('-webkit-app-region') : null);
    return { strip: region(strip), tab: region(tab) };
  });
  expect(regions.strip).toBe('drag');
  expect(regions.tab).toBe('no-drag');
});

test('permissions other than notifications are refused', async () => {
  const camera = await main.evaluate(() => navigator.mediaDevices.getUserMedia({ video: true }).then(() => 'granted', (error: Error) => error.name));
  expect(camera).not.toBe('granted');
});

test('quitting leaves no backend process', async () => {
  expect(backendProcesses(dataDir).length).toBeGreaterThan(0);
  await app.close();
  await expect.poll(() => backendProcesses(dataDir).length, { timeout: 60_000 }).toBe(0);
});
