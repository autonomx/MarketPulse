import { useSyncExternalStore } from 'react';

/**
 * The live voice transcript (WP-9.4): what the speaker said and what Omnix is
 * saying, as state the chat workspace renders. Live voice code writes here
 * instead of inserting rows into the rendered transcript.
 */
export type LiveVoiceSpeaker = 'You' | 'Omnix';

export type LiveVoiceTranscriptRow = {
  id: string;
  speaker: LiveVoiceSpeaker;
  text: string;
  /** ISO timestamp of the row's first words. */
  at: string;
  /** The row still being spoken; at most one row is a draft. */
  draft: boolean;
};

/** The assistant text delivered so far by the unified audio path. */
export type LiveVoiceDelivery = { text: string; partial: boolean };

export type LiveVoiceTranscriptState = {
  rows: readonly LiveVoiceTranscriptRow[];
  delivery: LiveVoiceDelivery | null;
};

const EMPTY: LiveVoiceTranscriptState = { rows: [], delivery: null };

let state: LiveVoiceTranscriptState = EMPTY;
let sequence = 0;
const listeners = new Set<() => void>();

function publish(next: LiveVoiceTranscriptState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function nextId(): string {
  sequence += 1;
  return `live-voice-${Date.now()}-${sequence}`;
}

export const liveVoiceTranscriptStore = {
  getState(): LiveVoiceTranscriptState {
    return state;
  },

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /**
   * The speaker's draft row is updated, and settled by a final. Another
   * speaker's words settle the draft as it stands and start their own row, so
   * an Omnix message never lands in the speaker's row.
   */
  write(speaker: LiveVoiceSpeaker, text: string, mode: 'draft' | 'final'): void {
    const content = text.trim();
    if (!content) return;
    const rows = [...state.rows];
    const draftIndex = rows.findIndex((row) => row.draft);
    const draft = draftIndex >= 0 ? rows[draftIndex] : null;
    if (draft && draft.speaker === speaker) {
      rows[draftIndex] = { ...draft, text: content, draft: mode === 'draft' };
    } else {
      if (draft) rows[draftIndex] = { ...draft, draft: false };
      rows.push({ id: nextId(), speaker, text: content, at: new Date().toISOString(), draft: mode === 'draft' });
    }
    publish({ ...state, rows });
  },

  /** The words of the row being spoken. */
  draftText(): string {
    return state.rows.find((row) => row.draft)?.text ?? '';
  },

  /** Drops the settled rows of the speaker; the persisted chat message replaces them. Returns how many. */
  removeFinalUserRows(): number {
    const kept = state.rows.filter((row) => row.draft || row.speaker !== 'You');
    const removed = state.rows.length - kept.length;
    if (removed) publish({ ...state, rows: kept });
    return removed;
  },

  setDelivery(delivery: LiveVoiceDelivery | null): void {
    if (state.delivery?.text === delivery?.text && state.delivery?.partial === delivery?.partial) return;
    publish({ ...state, delivery });
  },

  /** Clears the rows; delivery belongs to the audio turn and is left alone. */
  clear(): void {
    if (state.rows.length) publish({ ...state, rows: [] });
  },

  resetForTests(): void {
    sequence = 0;
    publish(EMPTY);
  },
};

export function useLiveVoiceTranscript(): LiveVoiceTranscriptState {
  return useSyncExternalStore(liveVoiceTranscriptStore.subscribe, liveVoiceTranscriptStore.getState, liveVoiceTranscriptStore.getState);
}
