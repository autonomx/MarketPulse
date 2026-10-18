import {
  app, BrowserWindow, ipcMain, Menu, nativeImage, Notification, powerSaveBlocker, session, shell, Tray,
  type IpcMainEvent, type IpcMainInvokeEvent, type MenuItemConstructorOptions,
} from 'electron';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AlertBadge, alertDotBitmap, NotificationListener } from './notifications';
import { desktopPaths } from './paths';
import { SecretStore, secretEnvironment } from './secrets';
import { SettingsStore, choosePorts, type DesktopSettings } from './settings';
import { BackendSupervisor, type SupervisorEvent } from './supervisor';
import { SESSION_PARTITION, WindowManager } from './windows';
import { isMarketHours } from './marketHours';
import { exportDiagnostics } from './diagnostics';
import { startUpdateChecks } from './updates';
import { appUserModelId, DEV_SHORTCUT_NAME } from './identity';

const paths = desktopPaths();
// Electron's own data (session, cookies, caches) lives with the rest of MarketPulse's data.
app.setPath('userData', join(paths.data, 'electron'));
app.setAppUserModelId(appUserModelId(app.isPackaged));

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else if (process.argv.includes('--quit')) {
  // `MarketPulse --quit` (the installer's shutdown request) with no MarketPulse running: nothing to stop.
  app.exit(0);
} else {
  void main();
}

let quitting = false;

async function main(): Promise<void> {
  await app.whenReady();
  const store = SettingsStore.in(paths.data);
  let settings: DesktopSettings = await store.loadOrCreate();
  const secrets = new SecretStore(paths.secrets).loadOrCreate();
  let origin: string | null = null;
  const windows = new WindowManager(paths, store, () => origin);
  const startHidden = process.argv.includes('--hidden');
  let splash: BrowserWindow | null = null;
  let errorWindow: BrowserWindow | null = null;
  let lastStatus: unknown = { kind: 'step', message: 'Starting…' };
  let supervisor: BackendSupervisor;
  let awakeBlocker: number | null = null;

  hardenSession();

  // Single instance: a second launch shows the running app; `--quit` (the installer, before it replaces the files)
  // quits it cleanly, stopping the backend and PostgreSQL first.
  app.on('second-instance', (_event, argv) => {
    if (argv.includes('--quit')) app.quit();
    else windows.focusMain();
  });

  const sendStatus = (status: unknown) => {
    lastStatus = status;
    for (const window of [splash, errorWindow]) {
      if (window && !window.isDestroyed()) window.webContents.send('mp:status', status);
    }
  };

  // The snapshot a failed update took just before migrating: the only data the user may put back (roadmap MP-9.3).
  let failedUpdateSnapshot: string | null = null;
  const showError = (event: { message: string; log?: string | null; step?: string; snapshot?: string | null }) => {
    splash?.destroy();
    splash = null;
    if (!errorWindow || errorWindow.isDestroyed()) {
      errorWindow = windows.shellPage('error', { width: 680, height: 420 });
    }
    failedUpdateSnapshot = event.snapshot ?? null;
    const taken = failedUpdateSnapshot ? snapshotTime(failedUpdateSnapshot) : null;
    sendStatus({ kind: 'error', message: event.message, log: event.log ?? '', step: event.step, snapshot: taken });
    errorWindow.show();
  };

  const signedInWorkspace = async (): Promise<string | null> => {
    if (!origin) return null;
    try {
      const response = await session.fromPartition(SESSION_PARTITION).fetch(`${origin}/api/auth/session`, { credentials: 'include' });
      const body = await response.json() as { authenticated?: boolean; workspace_id?: string | null };
      return body.authenticated ? body.workspace_id ?? null : null;
    } catch {
      return null;
    }
  };

  // The alert badge (MP-8.2): a dot on the taskbar button on Windows, a count on the Dock on macOS.
  const alertDot = nativeImage.createFromBitmap(alertDotBitmap(16), { width: 16, height: 16 });
  const badge = new AlertBadge((count) => {
    if (process.platform === 'darwin') app.dock?.setBadge(count ? String(count) : '');
    else windows.get('main')?.setOverlayIcon(count ? alertDot : null, count ? `${count} new alert${count === 1 ? '' : 's'}` : '');
  });
  app.on('browser-window-focus', () => badge.seen());

  // Shown notifications are kept until clicked or closed: a collected Notification loses its click listener, and the
  // click (macOS Notification Center, Windows Action Center) then does nothing. The oldest go past the cap.
  const shown = new Set<Notification>();
  const listener = new NotificationListener(secrets.notifySecret, {
    signedInWorkspace,
    show: (title, body, url) => {
      const notification = new Notification({ title, body, icon: join(paths.icons, 'icon-256.png') });
      shown.add(notification);
      for (const old of shown) {
        if (shown.size <= 100) break;
        shown.delete(old);
      }
      notification.on('close', () => shown.delete(notification));
      notification.on('click', () => {
        shown.delete(notification);
        if (!origin) return;
        // Only a page of the app itself: the delivery's link is relative, and anything else is ignored.
        const target = new URL(url, origin);
        if (target.origin !== origin) return;
        const main = windows.get('main');
        if (main) {
          void main.loadURL(target.toString());
          windows.focusMain();
        } else {
          windows.open('main', target.toString());
        }
      });
      notification.show();
      tray?.setToolTip(`MarketPulse — ${title}`);
      badge.alert(BrowserWindow.getFocusedWindow() !== null);
    },
  });
  // The listener must own its port: if another process held it, alerts would go to that process (with the secret).
  let notificationsListening = false;
  const listenForNotifications = async (): Promise<boolean> => {
    if (notificationsListening) return true;
    try {
      await listener.listen(settings.notifyPort);
      notificationsListening = true;
    } catch {
      showError({ message: `port_busy: notifications ${settings.notifyPort}`, step: 'ports' });
    }
    return notificationsListening;
  };

  const openApp = async (firstRun: boolean) => {
    if (!origin) return;
    let url = `${origin}/trading`;
    if (firstRun) {
      // First run: the owner's one-time sign-in, then the email and password step (roadmap §4.7).
      try {
        const result = await supervisor.command('login-code');
        if (typeof result.code === 'string' && result.code) {
          url = `${origin}/api/auth/local/callback?code=${encodeURIComponent(result.code)}&next=${encodeURIComponent('/welcome')}`;
        }
      } catch {
        url = `${origin}/login`;
      }
    }
    const main = windows.open('main', url, { show: !startHidden || firstRun });
    main.on('close', (event) => {
      // Closing the last window keeps MarketPulse running in the tray, so alerts and monitors keep working.
      if (!quitting && windows.all().length === 1) {
        event.preventDefault();
        main.hide();
      }
    });
    splash?.destroy();
    splash = null;
  };

  const startBackend = (options: { restoreSnapshot?: string } = {}) => {
    errorWindow?.destroy();
    errorWindow = null;
    if (!startHidden && (!splash || splash.isDestroyed())) splash = windows.shellPage('splash');
    const google = googleClient();
    supervisor = new BackendSupervisor(paths, settings,
      { ...secretEnvironment(secrets), ...(google ? { MARKETPULSE_GOOGLE_CLIENT_SECRET: google.clientSecret } : {}) },
      google?.clientId ?? null);
    supervisor.on('event', (event: SupervisorEvent) => {
      if (event.event === 'step') sendStatus({ kind: 'step', message: event.message, step: event.step });
      else if (event.event === 'ready') {
        const firstLaunch = origin === null;
        origin = event.origin;
        if (firstLaunch || !windows.get('main')) void openApp(event.first_run);
      } else if (event.event === 'error') showError(event);
      else if (event.event === 'restored') {
        // The data is back as the previous release left it; this build would only migrate it again.
        showError({
          step: 'restored',
          message: 'Your data is back as it was before the update. This version of MarketPulse cannot use it: '
            + 'install the previous release, then start MarketPulse again.',
        });
      }
    });
    supervisor.on('exit', (code: number | null) => {
      if (!quitting && code !== 0 && !errorWindow) showError({ message: `The backend stopped (exit ${code}).`, step: 'backend' });
    });
    supervisor.start(options);
  };

  // IPC from the bundled shell pages.
  ipcMain.on('mp:info', (event) => {
    event.returnValue = { version: app.getVersion(), platform: process.platform, origin };
  });
  ipcMain.on('mp:status-ready', (event) => event.sender.send('mp:status', lastStatus));
  ipcMain.on('mp:retry', (event) => {
    if (!fromShellPage(event)) return;
    void supervisor.stop().then(async () => {
      if (await listenForNotifications()) startBackend();
    });
  });
  ipcMain.on('mp:new-ports', async (event) => {
    if (!fromShellPage(event)) return;
    // The origin changes with the port, which resets window layouts kept in the browser (roadmap §4.3).
    const ports = await choosePorts();
    settings = store.update((current) => Object.assign(current, ports));
    app.relaunch();
    quitting = true;
    await supervisor.stop();
    app.exit(0);
  });
  ipcMain.on('mp:restore-snapshot', (event) => {
    const snapshot = failedUpdateSnapshot;
    if (!fromShellPage(event) || !snapshot) return;
    // A failed update migration: put back exactly the snapshot it took (roadmap MP-9.3).
    void supervisor.stop().then(() => startBackend({ restoreSnapshot: snapshot }));
  });
  ipcMain.on('mp:previous-release', (event) => {
    if (fromShellPage(event)) void shell.openExternal(RELEASES_URL);
  });
  ipcMain.on('mp:export-diagnostics', (event) => {
    if (fromShellPage(event) || fromApp(event)) void exportDiagnostics(paths, settings, BrowserWindow.fromWebContents(event.sender));
  });
  ipcMain.on('mp:quit', (event) => {
    if (fromShellPage(event) || fromApp(event)) app.quit();
  });
  ipcMain.on('mp:reveal-logs', (event) => {
    if (fromShellPage(event) || fromApp(event)) void shell.openPath(paths.logs);
  });

  // IPC from the gateway's pages (the `window.marketPulse` bridge).
  function fromApp(event: IpcMainEvent | IpcMainInvokeEvent): boolean {
    const url = event.senderFrame?.url;
    return Boolean(origin && url && new URL(url).origin === origin);
  }
  function fromShellPage(event: IpcMainEvent | IpcMainInvokeEvent): boolean {
    const url = event.senderFrame?.url ?? '';
    if (!url.startsWith('file:')) return false;
    // Exactly one of the bundled pages: the file must sit in the app's own pages folder.
    try {
      return dirname(fileURLToPath(url)).toLowerCase() === resolve(paths.pages).toLowerCase();
    } catch {
      return false;
    }
  }
  // The imports write into the install owner's workspace: only the owner, signed in, may run them.
  async function ownerSignedIn(): Promise<boolean> {
    if (!origin) return false;
    try {
      const response = await session.fromPartition(SESSION_PARTITION).fetch(`${origin}/api/auth/session`, { credentials: 'include' });
      const body = await response.json() as { authenticated?: boolean; user_id?: string | null };
      return Boolean(body.authenticated && body.user_id === 'user:local');
    } catch {
      return false;
    }
  }
  ipcMain.on('mp:title-bar-overlay', (event, colors: { color: string; symbolColor: string }) => {
    if (!fromApp(event) || process.platform === 'darwin') return;
    const window = BrowserWindow.fromWebContents(event.sender);
    window?.setTitleBarOverlay({ color: colors.color, symbolColor: colors.symbolColor, height: 38 });
  });
  ipcMain.on('mp:open-external', (event, url: string) => {
    if (!fromApp(event)) return;
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'https:' || (parsed.protocol === 'http:' && origin && parsed.origin === origin)) {
        void shell.openExternal(parsed.toString());
      }
    } catch {
      // Not a URL: ignored.
    }
  });
  ipcMain.handle('mp:import-reference', async (event, source: string, apply: boolean) => {
    if (!fromShellPage(event)) return { error: 'refused' };
    if (!(await ownerSignedIn())) return { error: 'Only the owner can import: sign in to MarketPulse as the owner first.' };
    // Omnix's market reference data (roadmap MP-10.1); the URL goes to the supervisor on stdin, never a command line.
    try {
      return await supervisor.command('import-reference', 30 * 60_000, { source: String(source), apply: Boolean(apply) });
    } catch (error) {
      return { error: error instanceof Error ? error.message : String(error) };
    }
  });
  ipcMain.handle('mp:import-account-data', async (event, source: string, apply: boolean, workspace: string) => {
    if (!fromShellPage(event)) return { error: 'refused' };
    if (!(await ownerSignedIn())) return { error: 'Only the owner can import: sign in to MarketPulse as the owner first.' };
    // One Omnix workspace's documents, alerts and paper accounts (roadmap MP-10.2), as the owner's.
    try {
      return await supervisor.command('import-account-data', 30 * 60_000,
        { source: String(source), apply: Boolean(apply), source_workspace: String(workspace) });
    } catch (error) {
      return { error: error instanceof Error ? error.message : String(error) };
    }
  });
  ipcMain.on('mp:close-window', (event) => {
    if (fromShellPage(event)) BrowserWindow.fromWebContents(event.sender)?.close();
  });
  ipcMain.handle('mp:request-login-code', async (event) => {
    if (!fromApp(event) || !origin) return false;
    // Only while the owner has no password; the supervisor refuses otherwise (roadmap §4.7).
    const result = await supervisor.command('login-code');
    if (typeof result.code !== 'string' || !result.code) return false;
    await event.sender.loadURL(`${origin}/api/auth/local/callback?code=${encodeURIComponent(result.code)}&next=${encodeURIComponent('/welcome')}`);
    return true;
  });

  // Tray, menu, start at login and the optional keep-awake (roadmap Phase 8).
  // Start at login applies to an installed app only; a development or test run never touches the OS setting.
  const manageLoginItem = app.isPackaged && !process.env.MARKETPULSE_DATA_DIR;
  const tray: Tray | null = createTray(() => windows.focusMain(), () => openPage(origin, windows, '/settings'), () => app.quit(), {
    startAtLogin: () => settings.startAtLogin,
    setStartAtLogin: (on) => {
      settings = store.update((current) => Object.assign(current, { startAtLogin: on }));
      if (manageLoginItem) app.setLoginItemSettings({ openAtLogin: on, args: ['--hidden'] });
    },
    keepAwake: () => settings.keepAwakeDuringMarketHours,
    setKeepAwake: (on) => {
      settings = store.update((current) => Object.assign(current, { keepAwakeDuringMarketHours: on }));
    },
  });
  if (process.platform === 'darwin') {
    // The Dock: its menu reopens the window; a development run shows the brand icon (packaged, the .icns does).
    app.dock?.setMenu(Menu.buildFromTemplate([{ label: 'Show MarketPulse', click: () => windows.focusMain() }]));
    if (!app.isPackaged) app.dock?.setIcon(join(paths.icons, 'icon-256.png'));
  }
  if (process.platform === 'win32' && !app.isPackaged && !process.env.MARKETPULSE_DATA_DIR) {
    // Windows shows a development run's notifications only through a Start menu shortcut with its AppUserModelID
    // (the installer makes the installed app's); a test run leaves the Start menu alone.
    shell.writeShortcutLink(join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', DEV_SHORTCUT_NAME), {
      target: process.execPath,
      args: `"${app.getAppPath()}"`,
      icon: join(paths.icons, 'icon.ico'),
      iconIndex: 0,
      appUserModelId: appUserModelId(false),
      description: 'MarketPulse (development run)',
    });
  }
  let importWindow: BrowserWindow | null = null;
  const openImport = () => {
    if (importWindow && !importWindow.isDestroyed()) importWindow.focus();
    else importWindow = windows.shellPage('import', { width: 640, height: 520 });
  };
  Menu.setApplicationMenu(buildMenu(() => origin, windows, paths.logs, openImport));
  if (manageLoginItem) app.setLoginItemSettings({ openAtLogin: settings.startAtLogin, args: ['--hidden'] });
  // A daily pg_dump while the backend runs (roadmap §4.5); the supervisor keeps the newest seven.
  setInterval(() => void backupIfDue(), 60 * 60_000);
  setInterval(() => {
    const want = settings.keepAwakeDuringMarketHours && isMarketHours(new Date());
    if (want && awakeBlocker === null) awakeBlocker = powerSaveBlocker.start('prevent-app-suspension');
    if (!want && awakeBlocker !== null) {
      powerSaveBlocker.stop(awakeBlocker);
      awakeBlocker = null;
    }
  }, 60_000);

  app.on('window-all-closed', () => {
    // Keep running in the tray (macOS and Windows alike); Quit is in the tray and the menu.
  });
  app.on('activate', () => windows.focusMain());
  app.on('before-quit', (event) => {
    // Every quit waits for the backend: a second Quit while it stops (nothing shows for a few seconds) would otherwise
    // end the shell first and orphan the supervisor. app.exit below emits no before-quit.
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    listener.close();
    void supervisor.stop().then(() => app.exit(0));
  });

  startUpdateChecks((status) => {
    if (status) tray?.setToolTip(`MarketPulse — ${status}`);
  });
  if (await listenForNotifications()) startBackend();

  async function backupIfDue(): Promise<void> {
    const last = settings.lastBackupAt ? Date.parse(settings.lastBackupAt) : 0;
    if (!origin || Date.now() - last < 24 * 60 * 60_000) return;
    try {
      const result = await supervisor.command('backup', 30 * 60_000);
      if (typeof result.path === 'string') {
        settings = store.update((current) => Object.assign(current, { lastBackupAt: new Date().toISOString() }));
      }
    } catch {
      // Retried within the hour.
    }
  }
}

const RELEASES_URL = 'https://github.com/autonomx/MarketPulse/releases';

/** The time in a snapshot's name (pgdata-20261017T183955Z), as the error page shows it. */
function snapshotTime(snapshot: string): string {
  const match = /pgdata-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(snapshot);
  return match ? `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]} UTC` : 'before the update';
}

/**
 * The build's Google OAuth client of type Desktop (google-oauth.json next to the app, or desktop/config in
 * development), or null: Google sign-in is offered only when the publisher configured one. A Desktop client's secret
 * is not confidential (it ships with every install); PKCE protects the flow.
 */
function googleClient(): { clientId: string; clientSecret: string } | null {
  const candidates = [join(process.resourcesPath ?? '', 'google-oauth.json'), join(__dirname, '..', 'config', 'google-oauth.json')];
  // Development and tests may name another file; an installed app reads only its own.
  if (!app.isPackaged && process.env.MARKETPULSE_GOOGLE_OAUTH) candidates.unshift(process.env.MARKETPULSE_GOOGLE_OAUTH);
  for (const candidate of candidates) {
    if (!existsSync(candidate)) continue;
    try {
      const parsed = JSON.parse(readFileSync(candidate, 'utf8')) as { client_id?: string; client_secret?: string };
      if (parsed.client_id && parsed.client_secret) return { clientId: parsed.client_id, clientSecret: parsed.client_secret };
    } catch {
      // A broken file means no Google sign-in, not a broken app.
    }
  }
  return null;
}

function hardenSession(): void {
  for (const target of [session.fromPartition(SESSION_PARTITION), session.defaultSession]) {
    // Only notifications and clipboard writes are granted; everything else is refused (roadmap §4.6).
    target.setPermissionRequestHandler((_contents, permission, callback) => {
      callback(permission === 'notifications' || permission === 'clipboard-sanitized-write');
    });
    target.setPermissionCheckHandler((_contents, permission) => permission === 'notifications' || permission === 'clipboard-sanitized-write');
  }
  app.on('web-contents-created', (_event, contents) => {
    contents.on('will-attach-webview', (event) => event.preventDefault());
  });
}

interface TrayToggles {
  startAtLogin: () => boolean;
  setStartAtLogin: (on: boolean) => void;
  keepAwake: () => boolean;
  setKeepAwake: (on: boolean) => void;
}

function createTray(show: () => void, openSettings: () => void, quit: () => void, toggles: TrayToggles): Tray | null {
  try {
    // macOS: a black-on-transparent template (with @2x) that the menu bar tints for light and dark.
    const name = process.platform === 'darwin' ? 'trayTemplate.png' : process.platform === 'win32' ? 'icon-32.png' : 'icon-16.png';
    const icon = nativeImage.createFromPath(join(__dirname, 'icons', name));
    if (process.platform === 'darwin') icon.setTemplateImage(true);
    const tray = new Tray(icon);
    tray.setToolTip('MarketPulse');
    const menu = () => Menu.buildFromTemplate([
      { label: 'Show MarketPulse', click: show },
      { label: 'Settings…', click: openSettings },
      { type: 'separator' },
      { label: 'Start MarketPulse when I sign in', type: 'checkbox', checked: toggles.startAtLogin(),
        click: (item) => toggles.setStartAtLogin(item.checked) },
      // Market hours: 04:00-20:00 New York time on weekdays (roadmap MP-8.3); off by default.
      { label: 'Keep the computer awake during market hours', type: 'checkbox', checked: toggles.keepAwake(),
        click: (item) => toggles.setKeepAwake(item.checked) },
      { type: 'separator' },
      { label: 'Quit MarketPulse', click: quit },
    ]);
    tray.setContextMenu(menu());
    tray.on('right-click', () => tray.setContextMenu(menu()));
    tray.on('click', show);
    return tray;
  } catch {
    return null;
  }
}

/** Shows a page of the app in the main window, opening it if it is closed; nothing before the backend is up. */
function openPage(origin: string | null, windows: WindowManager, path: string): void {
  const main = windows.get('main');
  if (origin && main) {
    void main.loadURL(`${origin}${path}`);
    windows.focusMain();
  } else if (origin) {
    windows.open('main', `${origin}${path}`);
  }
}

function buildMenu(origin: () => string | null, windows: WindowManager, logs: string, openImport: () => void): Menu {
  const go = (path: string) => () => openPage(origin(), windows, path);
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Charts', click: go('/trading') },
        { label: 'Settings…', click: go('/settings') },
        { type: 'separator' },
        { id: 'import-omnix', label: 'Import from Omnix…', click: openImport },
        { type: 'separator' },
        { label: 'Open logs folder', click: () => void shell.openPath(logs) },
        { label: 'Export diagnostics…', click: () => void exportDiagnostics(paths, SettingsStore.in(paths.data).load(), null) },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      // No accelerators that collide with the trading app's own keys (Ctrl+T/W/Tab/1-9 reach the page).
      submenu: [
        { role: 'reload', accelerator: 'F5' },
        { role: 'togglefullscreen' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        ...(app.isPackaged ? [] : [{ type: 'separator' as const }, { role: 'toggleDevTools' as const }]),
      ],
    },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: `MarketPulse ${app.getVersion()}`, enabled: false },
        { label: 'Third-party licences', click: () => void shell.openPath(join(desktopPaths().resources, 'licenses')) },
      ],
    },
  ];
  return Menu.buildFromTemplate(template);
}
