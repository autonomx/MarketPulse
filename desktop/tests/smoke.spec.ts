import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { execFileSync, spawn } from 'node:child_process';
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

test('an alert that fires while every window is hidden raises a native notification', async () => {
  await app.evaluate(({ BrowserWindow, Notification }) => {
    const shown: string[] = [];
    (globalThis as unknown as { shown: string[] }).shown = shown;
    Notification.prototype.show = function show(this: Electron.Notification) {
      shown.push(`${this.title}|${this.body}`);
    };
    for (const window of BrowserWindow.getAllWindows()) window.hide();
  });
  const created = await main.evaluate(async () => {
    const csrf = decodeURIComponent((document.cookie.split('; ').find((part) => part.startsWith('omnix_csrf=')) ?? '').split('=')[1] ?? '');
    const headers = { 'Content-Type': 'application/json', 'X-Omnix-Client': 'web', 'X-Omnix-CSRF': csrf };
    const instrument = 'crypto:BINANCE:spot:BTC-USDT';
    const alert = await fetch('/api/trading/alerts', { method: 'POST', headers, body: JSON.stringify({
      alert_id: `smoke-${Date.now()}`,
      instrument_id: instrument,
      conditions: [{ source: { kind: 'price', field: 'close' }, operator: 'crossing_up', target: { kind: 'value', value: '100' } }],
      parameters: { notification_channels: ['app', 'desktop'], message: 'BTC crossed 100 (smoke test)' },
    }) });
    const observed = Date.now();
    for (const [price, offset] of [['99', 0], ['101', 30_000]] as const) {
      await fetch('/api/trading/alerts/evaluate', { method: 'POST', headers, body: JSON.stringify({
        instrument_id: instrument, observed_price: price, observed_at: new Date(observed + offset).toISOString(),
      }) });
    }
    return alert.status === 201 ? 201 : `${alert.status} ${await alert.text()}`;
  });
  expect(created).toBe(201);
  // The delivery monitor runs every 10 s; the listener shows the notification, then answers 200.
  await expect.poll(() => app.evaluate(() => (globalThis as unknown as { shown: string[] }).shown), { timeout: 90_000 })
    .toContain('MarketPulse alert|BTC crossed 100 (smoke test)');
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.show());
});

test('a window whose page crashes offers a reload and comes back', async () => {
  // The crash dialog is native; answer it with "Reload" from the main process.
  await app.evaluate(({ dialog }) => {
    (dialog as unknown as { showMessageBox: () => Promise<unknown> }).showMessageBox = async () => {
      (globalThis as unknown as { crashDialogs: number }).crashDialogs = ((globalThis as unknown as { crashDialogs?: number }).crashDialogs ?? 0) + 1;
      return { response: 0, checkboxChecked: false };
    };
  });
  const id = await app.evaluate(({ BrowserWindow }, url) => {
    const contents = BrowserWindow.getAllWindows().find((window) => window.webContents.getURL() === url)!.webContents;
    contents.forcefullyCrashRenderer();
    return contents.id;
  }, main.url());
  // Playwright keeps a crashed page dead, so the reloaded page is checked from the main process.
  await expect.poll(() => app.evaluate(async ({ webContents }, contentsId) => {
    const contents = webContents.fromId(contentsId);
    const dialogs = (globalThis as unknown as { crashDialogs?: number }).crashDialogs ?? 0;
    if (!contents) return 'gone';
    if (contents.isCrashed()) return `crashed (dialogs ${dialogs})`;
    try {
      const found = await Promise.race([
        contents.executeJavaScript("Boolean(document.querySelector('.trading-workspace'))"),
        new Promise((resolveLater) => setTimeout(() => resolveLater(`timeout (dialogs ${dialogs}, loading ${contents.isLoading()})`), 2000)),
      ]);
      if (typeof found === 'string') return found;
      return found ? 'ready' : `no workspace at ${contents.getURL()}`;
    } catch (error) {
      return `script failed: ${String(error)}`;
    }
  }, id), { timeout: 90_000 }).toBe('ready');
});

test('the Omnix import page checks a source before importing, through the backend', async () => {
  await app.evaluate(({ Menu }) => Menu.getApplicationMenu()?.getMenuItemById('import-omnix')?.click());
  const page = await appWindow(app, (url) => url.endsWith('/pages/import.html'), 30_000);
  await page.locator('#source').fill('http://not-a-database');
  await page.locator('#check').click();
  await expect(page.locator('#state')).toContainText('postgresql://');
  await expect(page.locator('#apply')).toBeDisabled();
  // A real source when the scratch Omnix test database is configured (OMNIX_TEST_DATABASE_URL).
  const omnix = process.env.OMNIX_TEST_DATABASE_URL;
  if (omnix) {
    await page.locator('#source').fill(omnix);
    await page.locator('#check').click();
    await expect(page.locator('#report tbody tr')).toHaveCount(5, { timeout: 60_000 });
    await expect(page.locator('#report tbody')).toContainText('Company profiles');
  }
  await page.locator('#close').click();
  await expect.poll(() => app.windows().some((candidate) => candidate.url().endsWith('/pages/import.html'))).toBe(false);
});

test('quitting leaves no backend process', async () => {
  expect(backendProcesses(dataDir).length).toBeGreaterThan(0);
  await app.close();
  await expect.poll(() => backendProcesses(dataDir).length, { timeout: 60_000 }).toBe(0);
});

test('the next launch opens the charts signed in, without the first-run page', async () => {
  app = await electron.launch({
    args: [join(DESKTOP, 'out', 'main.js')],
    env: { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => name !== 'ELECTRON_RUN_AS_NODE')) as Record<string, string>,
      MARKETPULSE_DATA_DIR: dataDir, MARKETPULSE_RESOURCES: STAGE },
  });
  const page = await appWindow(app, (url) => url.startsWith(origin) && url.includes('/trading'), 300_000);
  await expect(page.locator('.trading-workspace')).toBeVisible({ timeout: 120_000 });
  expect(page.url()).not.toContain('/welcome');
  expect(await page.evaluate(async () => (await (await fetch('/api/auth/session')).json()).account?.has_password)).toBe(true);
});

test('a second launch with --quit (the installer, before replacing files) stops the running app cleanly', async () => {
  const running = app.process();
  expect(backendProcesses(dataDir).length).toBeGreaterThan(0);
  const electronPath = (await import('electron')).default as unknown as string;
  const quitter = spawn(electronPath, [join(DESKTOP, 'out', 'main.js'), '--quit'], {
    env: { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => name !== 'ELECTRON_RUN_AS_NODE')) as Record<string, string>,
      MARKETPULSE_DATA_DIR: dataDir, MARKETPULSE_RESOURCES: STAGE },
    stdio: 'ignore',
  });
  await new Promise((resolveExit) => quitter.on('exit', resolveExit));
  await expect.poll(() => running.exitCode, { timeout: 60_000 }).not.toBeNull();
  await expect.poll(() => backendProcesses(dataDir).length, { timeout: 60_000 }).toBe(0);
});
