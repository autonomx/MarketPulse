/** Per-browser sidebar choices for chat sessions: pinned, archived and renamed. */
export type SidebarEntryState = {
  pinned?: boolean;
  archived?: boolean;
  title?: string;
};

export type SidebarState = Record<string, SidebarEntryState>;

export type SidebarSession = {
  id: string;
  title?: string | null;
  created_at?: string;
  updated_at?: string;
};

const STORAGE_KEY = 'omnix.chat.sidebar.v1';

export function readChatSidebarState(): SidebarState {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as SidebarState;
  } catch {
    return {};
  }
}

export function writeChatSidebarState(state: SidebarState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Optional browser storage. The current page still reflects the change.
  }
}

export function visibleChatSidebarSessions<T extends SidebarSession>(sessions: readonly T[], state: SidebarState): T[] {
  return sessions.filter((session) => !state[session.id]?.archived && !String(session.title ?? '').trim().startsWith('Podcast script:'));
}

export function sortChatSidebarSessions<T extends SidebarSession>(sessions: readonly T[]): T[] {
  return [...sessions].sort((left, right) => {
    const rightTime = Date.parse(right.updated_at || right.created_at || '') || 0;
    const leftTime = Date.parse(left.updated_at || left.created_at || '') || 0;
    return rightTime - leftTime;
  });
}

export function sidebarSessionTitle(session: SidebarSession, entry: SidebarEntryState = {}): string {
  return entry.title?.trim() || session.title?.trim() || 'Untitled chat';
}
