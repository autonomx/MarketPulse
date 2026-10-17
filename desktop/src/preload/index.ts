import { contextBridge, ipcRenderer } from 'electron';

// The bridge (Omnix W-2's `window.marketPulse`) for the gateway's pages, and `marketPulseShell` for the bundled
// splash and error pages. Each call is checked again in the main process against the calling page's origin.
const info = ipcRenderer.sendSync('mp:info') as { version: string; platform: string; origin: string | null };

if (location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('marketPulseShell', {
    onStatus: (listener: (status: unknown) => void) => {
      ipcRenderer.on('mp:status', (_event, status) => listener(status));
      ipcRenderer.send('mp:status-ready');
    },
    retry: () => ipcRenderer.send('mp:retry'),
    newPorts: () => ipcRenderer.send('mp:new-ports'),
    restore: () => ipcRenderer.send('mp:restore-snapshot'),
    previousRelease: () => ipcRenderer.send('mp:previous-release'),
    exportDiagnostics: () => ipcRenderer.send('mp:export-diagnostics'),
    revealLogs: () => ipcRenderer.send('mp:reveal-logs'),
    quit: () => ipcRenderer.send('mp:quit'),
    importReference: (source: string, apply: boolean): Promise<unknown> =>
      ipcRenderer.invoke('mp:import-reference', String(source), Boolean(apply)),
    importAccountData: (source: string, apply: boolean, workspace: string): Promise<unknown> =>
      ipcRenderer.invoke('mp:import-account-data', String(source), Boolean(apply), String(workspace)),
    closeWindow: () => ipcRenderer.send('mp:close-window'),
  });
} else if (info.origin && location.origin === info.origin) {
  contextBridge.exposeInMainWorld('marketPulse', {
    version: info.version,
    platform: info.platform,
    setTitleBarOverlay: (colors: { color: string; symbolColor: string }) => {
      ipcRenderer.send('mp:title-bar-overlay', { color: String(colors.color), symbolColor: String(colors.symbolColor) });
    },
    revealLogs: () => ipcRenderer.send('mp:reveal-logs'),
    openExternal: (url: string) => ipcRenderer.send('mp:open-external', String(url)),
    requestLoginCode: (): Promise<boolean> => ipcRenderer.invoke('mp:request-login-code'),
  });
}
