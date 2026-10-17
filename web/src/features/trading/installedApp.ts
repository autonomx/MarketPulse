// The installed app (TVP-4.5): Omnix Trading installed from the browser runs
// in its own window (a web app manifest, no native wrapper, decision D-8).
// There the browser-reserved keys (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+1..9)
// reach the page, so the trading commands switch to TradingView's keys.
import { useEffect, useSyncExternalStore } from 'react';
import { isDesktopApp } from '../../shared/desktopBridge';
import { installPrompt, onInstallPromptChange, takeInstallPrompt } from '../../shared/installPrompt';
import { setTradingCommandAvailability } from './commands/useTradingCommands';

/** The display modes of an installed app's own window. */
// Not fullscreen: a browser tab in fullscreen (F11) reports it too, and the manifest asks for standalone.
const INSTALLED_QUERIES = ['(display-mode: standalone)', '(display-mode: window-controls-overlay)'] as const;

type MediaQueries = (query: string) => Pick<MediaQueryList, 'matches' | 'addEventListener' | 'removeEventListener'>;

const browserQueries: MediaQueries = (query) => window.matchMedia(query);

/** Whether this page runs in the installed app's window, or in the desktop app (MarketPulse W-2). */
export function isInstalledApp(media: MediaQueries = browserQueries): boolean {
  if (isDesktopApp()) return true;
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return INSTALLED_QUERIES.some((query) => media(query).matches);
}

/** Keeps the trading commands' keys in step with the window: app keys in the installed app, browser keys otherwise. */
export function useInstalledAppCommandKeys(media: MediaQueries = browserQueries): void {
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const update = () => setTradingCommandAvailability(isInstalledApp(media) ? 'installed' : 'browser');
    update();
    const lists = INSTALLED_QUERIES.map((query) => media(query));
    lists.forEach((list) => list.addEventListener('change', update));
    return () => {
      lists.forEach((list) => list.removeEventListener('change', update));
      setTradingCommandAvailability('browser');
    };
  }, [media]);
}

/** Installs the app through the browser's prompt; null when the browser offers none (installed, or not supported). */
export function useInstallApp(): (() => Promise<boolean>) | null {
  const prompt = useSyncExternalStore(onInstallPromptChange, installPrompt, () => null);
  if (!prompt) return null;
  return async () => {
    const event = takeInstallPrompt();
    if (!event) return false;
    await event.prompt();
    return (await event.userChoice).outcome === 'accepted';
  };
}
