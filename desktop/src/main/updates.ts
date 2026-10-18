import { app, Notification, shell } from 'electron';
import { autoUpdater } from 'electron-updater';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';

const RELEASES_URL = 'https://github.com/autonomx/MarketPulse/releases/latest';
// Kept, so its click listener lives as long as the notification (a collected Notification ignores clicks).
let availableNotice: Notification | null = null;

/**
 * Updates from the public releases feed (roadmap MP-9.3). A downloaded update installs when the app quits; the next
 * start snapshots the database before migrating (marketpulse.supervise), so a failed migration can be rolled back.
 */
export function startUpdateChecks(onStatus: (status: string) => void): void {
  // Installed apps only. A test run of an installed app (MARKETPULSE_DATA_DIR) checks only when asked to; the feed it
  // checks is the one built into the app (app-update.yml).
  if (!app.isPackaged || (process.env.MARKETPULSE_DATA_DIR && process.env.MARKETPULSE_UPDATE_CHECKS !== '1')) return;
  // Squirrel.Mac installs only an update that satisfies the running app's signature requirement, which an ad hoc
  // signature (a build without a Developer ID) never does: such a Mac build says a version is out instead of
  // downloading one it cannot install.
  const manual = process.platform === 'darwin' && adHocSigned();
  autoUpdater.autoDownload = !manual;
  autoUpdater.autoInstallOnAppQuit = !manual;
  const announced = new Set<string>();
  autoUpdater.on('update-available', (info) => {
    if (!manual) {
      onStatus(`Downloading MarketPulse ${info.version}…`);
      return;
    }
    onStatus(`MarketPulse ${info.version} is available`);
    if (announced.has(info.version)) return;
    announced.add(info.version);
    availableNotice = new Notification({ title: `MarketPulse ${info.version} is available`,
      body: 'Download it from the releases page and drag it to Applications. Your data is kept.' });
    availableNotice.on('click', () => void shell.openExternal(RELEASES_URL));
    availableNotice.show();
  });
  autoUpdater.on('update-downloaded', (info) => {
    onStatus(`MarketPulse ${info.version} installs when you quit.`);
    new Notification({ title: 'MarketPulse update ready', body: `Version ${info.version} installs the next time you quit.` }).show();
  });
  autoUpdater.on('error', () => onStatus(''));
  const check = () => void autoUpdater.checkForUpdates().catch(() => undefined);
  check();
  setInterval(check, 6 * 60 * 60_000);
}

/** Whether this app bundle is signed ad hoc (`codesign -dv` reports `Signature=adhoc`). */
function adHocSigned(): boolean {
  const bundle = resolve(dirname(process.execPath), '..', '..');
  const result = spawnSync('/usr/bin/codesign', ['-dv', bundle], { encoding: 'utf8' });
  return /^Signature=adhoc$/m.test(`${result.stderr}${result.stdout}`);
}
