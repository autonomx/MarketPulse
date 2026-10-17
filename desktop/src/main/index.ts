import {
  app, BrowserWindow, ipcMain, Menu, nativeImage, Notification, powerSaveBlocker, session, shell, Tray,
  type IpcMainEvent, type IpcMainInvokeEvent, type MenuItemConstructorOptions,
} from 'electron';
import { join } from 'node:path';
import { NotificationListener } from './notifications';
import { desktopPaths } from './paths';
import { SecretStore, secretEnvironment } from './secrets';
import { SettingsStore, choosePorts, type DesktopSettings } from './settings';
import { BackendSupervisor, type SupervisorEvent } from './supervisor';
import { SESSION_PARTITION, WindowManager } from './windows';
import { isMarketHours } from './marketHours';

const paths = desktopPaths();
// Electron's own data (session, cookies, caches) lives with the rest of MarketPulse's data.
app.setPath('userData', join(paths.data, 'electron'));
app.setAppUserModelId('com.autonomx.marketpulse');

if (!app.requestSingleInstanceLock()) {
  app.quit();
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

  // Single instance: a second launch shows the running app.
  app.on('second-instance', () => windows.focusMain());

  const sendStatus = (status: unknown) => {
    lastStatus = status;
    for (const window of [splash, errorWindow]) {
      if (window && !window.isDestroyed()) window.webContents.send('mp:status', status);
    }
  };

  const showError = (event: { message: string; log?: string | null; step?: string }) => {
    splash?.destroy();
    splash = null;
    if (!errorWindow || errorWindow.isDestroyed()) {
      errorWindow = windows.shellPage('error', { width: 680, height: 420 });
    }
    sendStatus({ kind: 'error', message: event.message, log: event.log ?? '', step: event.step });
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

  const listener = new NotificationListener(secrets.notifySecret, {
    signedInWorkspace,
    show: (title, body, url) => {
      const notification = new Notification({ title, body, icon: join(paths.icons, 'icon-256.png') });
      notification.on('click', () => {
        if (!origin) return;
        const main = windows.get('main');
        if (main) {
          void main.loadURL(new URL(url, origin).toString());
          windows.focusMain();
        } else {
          windows.open('main', new URL(url, origin).toString());
        }
      });
      notification.show();
      tray?.setToolTip(`MarketPulse — ${title}`);
    },
  });
  try {
    await listener.listen(settings.notifyPort);
  } catch {
    showError({ message: `port_busy: notifications ${settings.notifyPort}`, step: 'ports' });
  }

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

  const startBackend = () => {
    errorWindow?.destroy();
    errorWindow = null;
    if (!startHidden && (!splash || splash.isDestroyed())) splash = windows.shellPage('splash');
    supervisor = new BackendSupervisor(paths, settings, secretEnvironment(secrets));
    supervisor.on('event', (event: SupervisorEvent) => {
      if (event.event === 'step') sendStatus({ kind: 'step', message: event.message, step: event.step });
      else if (event.event === 'ready') {
        const firstLaunch = origin === null;
        origin = event.origin;
        if (firstLaunch || !windows.get('main')) void openApp(event.first_run);
      } else if (event.event === 'error') showError(event);
    });
    supervisor.on('exit', (code: number | null) => {
      if (!quitting && code !== 0 && !errorWindow) showError({ message: `The backend stopped (exit ${code}).`, step: 'backend' });
    });
    supervisor.start();
  };

  // IPC from the bundled shell pages.
  ipcMain.on('mp:info', (event) => {
    event.returnValue = { version: app.getVersion(), platform: process.platform, origin };
  });
  ipcMain.on('mp:status-ready', (event) => event.sender.send('mp:status', lastStatus));
  ipcMain.on('mp:retry', (event) => {
    if (!fromShellPage(event)) return;
    void supervisor.stop().then(startBackend);
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
    return url.startsWith('file:') && url.includes('/pages/');
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
  ipcMain.handle('mp:request-login-code', async (event) => {
    if (!fromApp(event) || !origin) return false;
    // Only while the owner has no password; the supervisor refuses otherwise (roadmap §4.7).
    const result = await supervisor.command('login-code');
    if (typeof result.code !== 'string' || !result.code) return false;
    await event.sender.loadURL(`${origin}/api/auth/local/callback?code=${encodeURIComponent(result.code)}&next=${encodeURIComponent('/welcome')}`);
    return true;
  });

  // Tray, menu, start at login and the optional keep-awake (roadmap Phase 8).
  const tray: Tray | null = createTray(() => windows.focusMain(), () => app.quit());
  Menu.setApplicationMenu(buildMenu(() => origin, windows, paths.logs));
  app.setLoginItemSettings({ openAtLogin: settings.startAtLogin, args: ['--hidden'] });
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
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    listener.close();
    void supervisor.stop().then(() => app.exit(0));
  });

  startBackend();
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

function createTray(show: () => void, quit: () => void): Tray | null {
  try {
    const icon = nativeImage.createFromPath(join(__dirname, 'icons', process.platform === 'win32' ? 'icon-32.png' : 'icon-16.png'));
    const tray = new Tray(icon);
    tray.setToolTip('MarketPulse');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Show MarketPulse', click: show },
      { type: 'separator' },
      { label: 'Quit MarketPulse', click: quit },
    ]));
    tray.on('click', show);
    return tray;
  } catch {
    return null;
  }
}

function buildMenu(origin: () => string | null, windows: WindowManager, logs: string): Menu {
  const go = (path: string) => () => {
    const current = origin();
    const main = windows.get('main');
    if (current && main) {
      void main.loadURL(`${current}${path}`);
      windows.focusMain();
    }
  };
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Charts', click: go('/trading') },
        { label: 'Settings…', click: go('/settings') },
        { type: 'separator' },
        { label: 'Open logs folder', click: () => void shell.openPath(logs) },
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
