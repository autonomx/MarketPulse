import { useSyncExternalStore } from 'react';
import type { StreamingSttConnectionStatus } from './live-voice-websocket';

/**
 * What the live call card shows (WP-9.4). The capture controller reports the
 * microphone stream, the unified audio controller reports playback, and Chat
 * reports the auto-speak setting; Chat renders the card from this state. They
 * used to write the card's text, buttons and data attributes directly and read
 * each other's writes back from the DOM.
 */
export type LiveCallOutputKind = 'greeting' | 'response';
export type LiveCallVoiceMode = 'speaking' | 'listening' | 'error' | 'idle';

export type LiveCallPresentationState = {
  /** The dedicated capture controller is installed and owns the call's microphone. */
  captureOwned: boolean;
  /** The capture stream's connection. */
  captureStatus: StreamingSttConnectionStatus;
  /** A capture call is starting or running. */
  captureActive: boolean;
  /** The microphone level is above the speech threshold. */
  hearing: boolean;
  /** Assistant audio is playing. */
  speaking: boolean;
  outputKind: LiveCallOutputKind | null;
  /** Chat is open and speaks assistant replies aloud. */
  autoSpeak: boolean;
  /** The live task instruction chosen for new calls ('' is plain conversation). */
  taskInstruction: string;
  /** The unified audio path's latest status line. */
  streamStatus: string | null;
};

export const LIVE_TASK_INSTRUCTION_STORAGE_KEY = 'omnix.live.taskInstruction';

export const LIVE_TASK_PRESETS: ReadonlyArray<{ label: string; value: string }> = [
  { label: 'Conversation', value: '' },
  { label: 'Translate Japanese to English', value: 'Translate Japanese speech into concise English continuously. Keep listening while speaking.' },
  { label: 'Live grammar correction', value: 'Correct my grammar continuously while I speak.' },
];

function storedTaskInstruction(): string {
  try {
    return window.localStorage.getItem(LIVE_TASK_INSTRUCTION_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

function initialState(): LiveCallPresentationState {
  return {
    captureOwned: false,
    captureStatus: 'idle',
    captureActive: false,
    hearing: false,
    speaking: false,
    outputKind: null,
    autoSpeak: false,
    taskInstruction: typeof window === 'undefined' ? '' : storedTaskInstruction(),
    streamStatus: null,
  };
}

let state = initialState();
const listeners = new Set<() => void>();

export const liveCallPresentationStore = {
  getState(): LiveCallPresentationState {
    return state;
  },

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /** Merges a change; listeners run only when a value differs. */
  update(change: Partial<LiveCallPresentationState>): void {
    const keys = Object.keys(change) as Array<keyof LiveCallPresentationState>;
    if (keys.every((key) => state[key] === change[key])) return;
    state = { ...state, ...change };
    listeners.forEach((listener) => listener());
  },

  setTaskInstruction(value: string): void {
    try {
      window.localStorage.setItem(LIVE_TASK_INSTRUCTION_STORAGE_KEY, value);
    } catch {
      // The choice still applies to this page.
    }
    this.update({ taskInstruction: value });
  },

  resetForTests(): void {
    state = initialState();
    listeners.forEach((listener) => listener());
  },
};

/** The card's wording for a capture status. */
export function liveCaptureLabels(status: StreamingSttConnectionStatus): { state: string; connection: string; input: string } {
  return {
    state: status === 'connected' ? 'Listening'
      : status === 'connecting' ? 'Connecting'
        : status === 'disconnected' ? 'Reconnecting'
          : status === 'error' ? 'Error' : 'Idle',
    connection: status === 'connected' ? 'Connected'
      : status === 'connecting' || status === 'disconnected' ? 'Connecting' : 'Disconnected',
    input: status === 'connected' ? 'Listening'
      : status === 'connecting' ? 'Requesting mic'
        : status === 'disconnected' ? 'Reconnecting'
          : status === 'error' ? 'Input error' : 'Idle',
  };
}

/** The orb's mode: playback wins, then a capture error, then an open call. */
export function liveCallVoiceMode(presentation: LiveCallPresentationState, callOpen = false): LiveCallVoiceMode {
  if (presentation.speaking) return 'speaking';
  if (presentation.captureStatus === 'error') return 'error';
  if (callOpen || presentation.captureActive || presentation.captureStatus === 'connected') return 'listening';
  return 'idle';
}

export function useLiveCallPresentation(): LiveCallPresentationState {
  return useSyncExternalStore(liveCallPresentationStore.subscribe, liveCallPresentationStore.getState, liveCallPresentationStore.getState);
}
