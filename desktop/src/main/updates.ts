import { app, Notification } from 'electron';
import { autoUpdater } from 'electron-updater';

/**
 * Updates from the public releases feed (roadmap MP-9.3). A downloaded update installs when the app quits; the next
 * start snapshots the database before migrating (marketpulse.supervise), so a failed migration can be rolled back.
 */
export function startUpdateChecks(onStatus: (status: string) => void): void {
  if (!app.isPackaged || process.env.MARKETPULSE_DATA_DIR) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on('update-available', (info) => onStatus(`Downloading MarketPulse ${info.version}…`));
  autoUpdater.on('update-downloaded', (info) => {
    onStatus(`MarketPulse ${info.version} installs when you quit.`);
    new Notification({ title: 'MarketPulse update ready', body: `Version ${info.version} installs the next time you quit.` }).show();
  });
  autoUpdater.on('error', () => onStatus(''));
  const check = () => void autoUpdater.checkForUpdates().catch(() => undefined);
  check();
  setInterval(check, 6 * 60 * 60_000);
}
