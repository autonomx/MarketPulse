import { app, dialog, Notification, shell } from 'electron';
import { autoUpdater } from 'electron-updater';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { checkResultMessage, type UpdateAction, type UpdateState } from './updateStatus';

const RELEASES_URL = 'https://github.com/autonomx/MarketPulse/releases/latest';
const CHECK_INTERVAL_MS = 6 * 60 * 60_000;

/**
 * Updates from the public releases feed (roadmap MP-9.3). A downloaded update installs when the app quits, or at once
 * with Restart to Update; the next start snapshots the database before migrating (marketpulse.supervise), so a failed
 * migration can be rolled back. The tray and Help menus show the state and offer Check for Updates.
 */
export class Updates {
  /** Installed apps only. A test run of an installed app (MARKETPULSE_DATA_DIR) checks only when asked to; the feed
   *  it checks is the one built into the app (app-update.yml). */
  readonly enabled = app.isPackaged && !(process.env.MARKETPULSE_DATA_DIR && process.env.MARKETPULSE_UPDATE_CHECKS !== '1');
  private state: UpdateState = { kind: 'idle' };
  /** An ad hoc signed Mac build: it announces a version instead of downloading one it cannot install. */
  private announceOnly = false;
  /** The user asked for the check in progress, so its answer is shown. */
  private asked = false;
  private readonly announced = new Set<string>();
  // Kept, so their click listeners live as long as the notifications (a collected Notification ignores clicks).
  private availableNotice: Notification | null = null;
  private readyNotice: Notification | null = null;

  constructor(private readonly onChange: (state: UpdateState) => void) {}

  current(): UpdateState {
    return this.state;
  }

  start(): void {
    if (!this.enabled) return;
    // Squirrel.Mac installs only an update that satisfies the running app's signature requirement, which an ad hoc
    // signature (a build without a Developer ID) never does.
    this.announceOnly = process.platform === 'darwin' && adHocSigned();
    autoUpdater.autoDownload = !this.announceOnly;
    autoUpdater.autoInstallOnAppQuit = !this.announceOnly;
    autoUpdater.on('update-not-available', () => {
      this.set({ kind: 'up-to-date' });
      this.answer();
    });
    autoUpdater.on('update-available', (info) => {
      if (this.announceOnly) {
        this.set({ kind: 'available', version: info.version });
        // A check the user asked for answers in a message instead.
        if (!this.asked) this.announce(info.version);
      } else {
        this.set({ kind: 'downloading', version: info.version, percent: null });
      }
      this.answer();
    });
    autoUpdater.on('download-progress', (progress) => {
      if (this.state.kind === 'downloading') this.set({ ...this.state, percent: Math.floor(progress.percent) });
    });
    autoUpdater.on('update-downloaded', (info) => {
      this.set({ kind: 'ready', version: info.version });
      this.notifyReady(info.version);
    });
    autoUpdater.on('error', () => this.fail());
    this.check(false);
    setInterval(() => this.check(false), CHECK_INTERVAL_MS);
  }

  /** Runs a menu item's action. */
  act(action: UpdateAction): void {
    if (action === 'check') this.check(true);
    else if (action === 'restart') this.restartToUpdate();
    else void shell.openExternal(RELEASES_URL);
  }

  /** Checks the feed; `asked` (Check for Updates) shows the answer, even "up to date". */
  check(asked: boolean): void {
    if (!this.enabled) return;
    if (this.state.kind === 'checking') {
      this.asked ||= asked;
      return;
    }
    if (this.state.kind === 'downloading' || this.state.kind === 'ready') {
      // Already under way or done: say where it stands rather than starting over.
      if (asked) this.show(this.state);
      return;
    }
    this.asked = asked;
    this.set({ kind: 'checking' });
    autoUpdater.checkForUpdates().catch(() => this.fail());
  }

  /** Quits, installs the downloaded update silently and starts the new version. Quitting stops the backend first;
   *  the installer also waits for every MarketPulse process to exit before it replaces files (installer.nsh). */
  restartToUpdate(): void {
    if (this.state.kind !== 'ready') return;
    autoUpdater.quitAndInstall(true, true);
  }

  private set(state: UpdateState): void {
    this.state = state;
    this.onChange(state);
  }

  private fail(): void {
    // A failed check or download leaves the menu offering a new check; a ready update stays ready.
    if (this.state.kind === 'ready' || this.state.kind === 'failed') return;
    this.set({ kind: 'failed' });
    this.answer();
  }

  /** Shows the answer to a check the user asked for. */
  private answer(): void {
    if (!this.asked) return;
    this.asked = false;
    this.show(this.state);
  }

  private show(state: UpdateState): void {
    const result = checkResultMessage(state, app.getVersion());
    if (!result) return;
    void dialog.showMessageBox({ type: 'info', title: 'MarketPulse', message: result.message, detail: result.detail,
      buttons: result.buttons, defaultId: 0, cancelId: result.buttons.length - 1, noLink: true })
      .then(({ response }) => {
        if (response === 0 && result.confirm) this.act(result.confirm);
      });
  }

  private announce(version: string): void {
    if (this.announced.has(version)) return;
    this.announced.add(version);
    this.availableNotice = new Notification({ title: `MarketPulse ${version} is available`,
      body: 'Download it from the releases page and drag it to Applications. Your data is kept.' });
    this.availableNotice.on('click', () => void shell.openExternal(RELEASES_URL));
    this.availableNotice.show();
  }

  private notifyReady(version: string): void {
    this.readyNotice = new Notification({ title: `MarketPulse ${version} is ready`,
      body: 'Click to restart and update now, or it installs the next time you quit.' });
    this.readyNotice.on('click', () => this.restartToUpdate());
    this.readyNotice.show();
  }
}

/** Whether this app bundle is signed ad hoc (`codesign -dv` reports `Signature=adhoc`). */
function adHocSigned(): boolean {
  const bundle = resolve(dirname(process.execPath), '..', '..');
  const result = spawnSync('/usr/bin/codesign', ['-dv', bundle], { encoding: 'utf8' });
  return /^Signature=adhoc$/m.test(`${result.stderr}${result.stdout}`);
}
