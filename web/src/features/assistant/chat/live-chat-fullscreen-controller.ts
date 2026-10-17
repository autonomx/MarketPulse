import { useSyncExternalStore } from 'react';

export type LiveChatBrowserFullscreenState = 'inactive' | 'requesting' | 'active' | 'denied' | 'unavailable';

export type LiveChatFullscreenState = {
  immersive: boolean;
  browserState: LiveChatBrowserFullscreenState;
  source: 'header' | 'call-card' | null;
};

const INITIAL_STATE: LiveChatFullscreenState = {
  immersive: false,
  browserState: 'inactive',
  source: null,
};

type FullscreenDocumentApi = {
  fullscreenElement?: Element | null;
  webkitFullscreenElement?: Element | null;
  exitFullscreen?: () => Promise<void> | void;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type FullscreenElementApi = {
  requestFullscreen?: () => Promise<void> | void;
  webkitRequestFullscreen?: () => Promise<void> | void;
};

let state = INITIAL_STATE;
let installed = false;
let requestToken = 0;
let priorFocus: HTMLElement | null = null;
let priorScroll = { x: 0, y: 0 };
const listeners = new Set<() => void>();

function emit(next: LiveChatFullscreenState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function fullscreenDocumentApi(): FullscreenDocumentApi {
  return document as unknown as FullscreenDocumentApi;
}

function browserFullscreenElement(): Element | null {
  const api = fullscreenDocumentApi();
  return api.fullscreenElement ?? api.webkitFullscreenElement ?? null;
}

function restorePresentationContext(): void {
  const focus = priorFocus;
  const scroll = priorScroll;
  priorFocus = null;
  window.requestAnimationFrame(() => {
    window.scrollTo(scroll.x, scroll.y);
    focus?.focus({ preventScroll: true });
  });
}

export function initializeLiveChatFullscreenController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (installed) return () => undefined;
  installed = true;

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !state.immersive) return;
    event.preventDefault();
    void exitLiveChatFullscreen();
  };
  const handleFullscreenChange = () => {
    if (browserFullscreenElement()) {
      if (state.immersive && state.browserState !== 'active') emit({ ...state, browserState: 'active' });
      return;
    }
    if (state.immersive && state.browserState === 'active') {
      requestToken += 1;
      emit(INITIAL_STATE);
      restorePresentationContext();
    } else if (!state.immersive && state.browserState !== 'inactive') {
      emit(INITIAL_STATE);
    }
  };

  document.addEventListener('keydown', handleKeyDown);
  document.addEventListener('fullscreenchange', handleFullscreenChange);
  document.addEventListener('webkitfullscreenchange', handleFullscreenChange as EventListener);

  return () => {
    document.removeEventListener('keydown', handleKeyDown);
    document.removeEventListener('fullscreenchange', handleFullscreenChange);
    document.removeEventListener('webkitfullscreenchange', handleFullscreenChange as EventListener);
    installed = false;
  };
}

export function enterLiveChatFullscreen(source: 'header' | 'call-card' = 'header'): void {
  initializeLiveChatFullscreenController();
  if (state.immersive) return;
  priorFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  priorScroll = { x: window.scrollX, y: window.scrollY };
  const token = ++requestToken;
  const root = document.documentElement;
  const api = root as unknown as FullscreenElementApi;
  const request = typeof api.requestFullscreen === 'function'
    ? api.requestFullscreen.bind(root)
    : typeof api.webkitRequestFullscreen === 'function'
      ? api.webkitRequestFullscreen.bind(root)
      : null;
  emit({ immersive: true, browserState: request ? 'requesting' : 'unavailable', source });

  window.requestAnimationFrame(() => {
    document.querySelector<HTMLElement>('[data-live-chat-fullscreen-shell]')?.focus({ preventScroll: true });
  });

  if (!request) return;
  Promise.resolve(request())
    .then(() => {
      if (token !== requestToken || !state.immersive) return;
      emit({ ...state, browserState: 'active' });
    })
    .catch(() => {
      if (token !== requestToken || !state.immersive) return;
      emit({ ...state, browserState: 'denied' });
    });
}

export async function exitLiveChatFullscreen(): Promise<void> {
  if (!state.immersive) return;
  requestToken += 1;
  const api = fullscreenDocumentApi();
  const exit = typeof api.exitFullscreen === 'function'
    ? api.exitFullscreen.bind(document)
    : typeof api.webkitExitFullscreen === 'function'
      ? api.webkitExitFullscreen.bind(document)
      : null;
  emit(INITIAL_STATE);
  if (browserFullscreenElement() && exit) {
    try {
      await Promise.resolve(exit());
    } catch {
      // The in-app overlay still exits even if the browser rejects exitFullscreen.
    }
  }
  restorePresentationContext();
}

export function getLiveChatFullscreenState(): LiveChatFullscreenState {
  return state;
}

export function subscribeLiveChatFullscreen(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLiveChatFullscreenState(): LiveChatFullscreenState {
  return useSyncExternalStore(subscribeLiveChatFullscreen, getLiveChatFullscreenState, () => INITIAL_STATE);
}

/** Test-only state reset. Runtime callers should use exitLiveChatFullscreen. */
export function resetLiveChatFullscreenStateForTests(): void {
  requestToken += 1;
  priorFocus = null;
  priorScroll = { x: 0, y: 0 };
  emit(INITIAL_STATE);
}
