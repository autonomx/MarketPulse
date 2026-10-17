// The browser's install prompt (Chromium's `beforeinstallprompt`), captured
// from the first page so the trading app can offer "Install app" later
// (TVP-4.5). Imported for its listener by main.tsx; the event usually fires
// before a feature's code is loaded.
export type InstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
};

let deferred: InstallPromptEvent | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    // Desktop Chromium keeps its address-bar install icon; the app offers the prompt in the shortcut dialog.
    event.preventDefault();
    deferred = event as InstallPromptEvent;
    notify();
  });
  window.addEventListener('appinstalled', () => {
    deferred = null;
    notify();
  });
}

export function installPrompt(): InstallPromptEvent | null {
  return deferred;
}

/** Takes the prompt: the browser shows it once. */
export function takeInstallPrompt(): InstallPromptEvent | null {
  const event = deferred;
  deferred = null;
  notify();
  return event;
}

export function onInstallPromptChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only. */
export function setInstallPromptForTests(event: InstallPromptEvent | null): void {
  deferred = event;
  notify();
}
