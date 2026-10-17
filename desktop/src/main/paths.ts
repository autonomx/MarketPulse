import { app } from 'electron';
import { join, resolve } from 'node:path';

/**
 * Where things live (roadmap §4.5). Packaged, the staged runtime is the app's resources folder; in development it is
 * build/stage. MARKETPULSE_DATA_DIR and MARKETPULSE_RESOURCES move them for tests.
 */
export interface DesktopPaths {
  /** python/, postgres/, app/, web/ (build/stage in development). */
  resources: string;
  /** All per-user data: pgdata, logs, secrets, snapshots, backups, resources, Electron's own session data. */
  data: string;
  logs: string;
  secrets: string;
  python: string;
  postgresBin: string;
  icons: string;
  pages: string;
  preload: string;
}

export function desktopPaths(): DesktopPaths {
  const resources = process.env.MARKETPULSE_RESOURCES
    ? resolve(process.env.MARKETPULSE_RESOURCES)
    : app.isPackaged ? join(process.resourcesPath, 'runtime') : resolve(__dirname, '..', '..', 'build', 'stage');
  const data = process.env.MARKETPULSE_DATA_DIR
    ? resolve(process.env.MARKETPULSE_DATA_DIR)
    : process.platform === 'win32'
      ? join(process.env.LOCALAPPDATA ?? app.getPath('appData'), 'MarketPulse')
      : app.getPath('userData');
  const python = process.platform === 'win32' ? join(resources, 'python', 'python.exe') : join(resources, 'python', 'bin', 'python3');
  return {
    resources,
    data,
    logs: join(data, 'logs'),
    secrets: join(data, 'secrets'),
    python,
    postgresBin: join(resources, 'postgres', 'bin'),
    icons: join(__dirname, 'icons'),
    pages: join(__dirname, 'pages'),
    preload: join(__dirname, 'preload.js'),
  };
}
