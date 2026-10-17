import { useEffect, useRef, useState } from 'react';
import {
  readChatSidebarState,
  sidebarSessionTitle,
  sortChatSidebarSessions,
  visibleChatSidebarSessions,
  writeChatSidebarState,
  type SidebarSession,
  type SidebarState,
} from './chatSidebarState';
import { startBlankChat } from './sessionTools';

const STATUS_VISIBLE_MS = 6_000;

type Status = { message: string; danger: boolean };

export type ChatSidebarSessionsProps<T extends SidebarSession> = {
  sessions: readonly T[];
  loading: boolean;
  failed: boolean;
  selectedSessionId: string | null;
  onSelect: (session: T) => void;
  /** Deletes the session on the server; rejects when it could not. */
  onDelete: (session: T) => Promise<unknown>;
};

/**
 * Pinned and recent chat sessions with per-row pin, share, rename, archive
 * and delete. Pins, archives and renames are this browser's choices.
 */
export function ChatSidebarSessions<T extends SidebarSession>({ sessions, loading, failed, selectedSessionId, onSelect, onDelete }: ChatSidebarSessionsProps<T>) {
  const [state, setState] = useState<SidebarState>(() => readChatSidebarState());
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const sharedSessionApplied = useRef(false);

  const visible = sortChatSidebarSessions(visibleChatSidebarSessions(sessions, state));
  const pinned = visible.filter((session) => state[session.id]?.pinned);
  const regular = visible.filter((session) => !state[session.id]?.pinned);

  function updateState(change: (current: SidebarState) => SidebarState): void {
    setState((current) => {
      const next = change(current);
      writeChatSidebarState(next);
      return next;
    });
  }

  function showStatus(message: string, danger = false): void {
    setStatus({ message, danger });
  }

  useEffect(() => {
    if (!status) return;
    const timer = window.setTimeout(() => setStatus(null), STATUS_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [status]);

  // A row's menu closes on Escape or a press outside the sidebar.
  useEffect(() => {
    if (!openMenuId) return;
    const handlePointer = (event: Event) => {
      if (event.target instanceof Node && hostRef.current?.contains(event.target)) return;
      setOpenMenuId(null);
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenuId(null);
    };
    document.addEventListener('pointerdown', handlePointer, true);
    document.addEventListener('keydown', handleKey, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointer, true);
      document.removeEventListener('keydown', handleKey, true);
    };
  }, [openMenuId]);

  // A shared link (`?chat_session=`) opens its session once the list has it.
  useEffect(() => {
    if (sharedSessionApplied.current || loading) return;
    const sessionId = new URLSearchParams(window.location.search).get('chat_session')?.trim() ?? '';
    if (!sessionId) {
      sharedSessionApplied.current = true;
      return;
    }
    const session = visible.find((candidate) => candidate.id === sessionId);
    if (!session) return;
    sharedSessionApplied.current = true;
    onSelect(session);
  });

  async function createChat(): Promise<void> {
    setCreating(true);
    showStatus('Creating new chat...');
    try {
      await startBlankChat();
      setStatus(null);
    } catch (error) {
      showStatus(error instanceof Error ? error.message : 'New chat could not be created.', true);
    } finally {
      setCreating(false);
    }
  }

  function togglePinned(sessionId: string): void {
    updateState((current) => ({ ...current, [sessionId]: { ...current[sessionId], pinned: !current[sessionId]?.pinned, archived: false } }));
  }

  function archive(sessionId: string): void {
    updateState((current) => ({ ...current, [sessionId]: { ...current[sessionId], archived: true, pinned: false } }));
    showStatus('Chat archived.');
  }

  function rename(session: T): void {
    const next = window.prompt('Rename chat', sidebarSessionTitle(session, state[session.id]));
    const title = next?.trim();
    if (!title) return;
    updateState((current) => ({ ...current, [session.id]: { ...current[session.id], title } }));
  }

  async function share(session: T): Promise<void> {
    const url = new URL('/chatbot', window.location.origin);
    url.searchParams.set('chat_session', session.id);
    try {
      if (typeof navigator.share === 'function') {
        await navigator.share({ title: sidebarSessionTitle(session, state[session.id]), url: url.toString() });
        showStatus('Share sheet opened.');
        return;
      }
      await navigator.clipboard.writeText(url.toString());
      showStatus('Chat link copied.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      showStatus('Could not share this chat.', true);
    }
  }

  async function remove(session: T): Promise<void> {
    const title = sidebarSessionTitle(session, state[session.id]);
    if (!window.confirm(`Delete "${title}"? This permanently removes the chat history.`)) return;
    try {
      await onDelete(session);
      updateState((current) => {
        const next = { ...current };
        delete next[session.id];
        return next;
      });
      showStatus('Chat deleted.');
    } catch {
      showStatus('Chat could not be deleted.', true);
    }
  }

  function row(session: T, isPinned: boolean) {
    const title = sidebarSessionTitle(session, state[session.id]);
    const menuOpen = openMenuId === session.id;
    const menuItem = (icon: string, label: string, action: () => void, danger = false) => (
      <button type="button" role="menuitem" className={danger ? 'danger' : ''} onClick={(event) => { event.stopPropagation(); setOpenMenuId(null); action(); }}>
        <span className="assistant-chatgpt-menu-icon" aria-hidden="true">{icon}</span><span>{label}</span>
      </button>
    );
    return (
      <div key={session.id} className={session.id === selectedSessionId ? 'assistant-chatgpt-row active' : 'assistant-chatgpt-row'} data-session-id={session.id}>
        <button type="button" className="assistant-chatgpt-session" title={title} onClick={() => onSelect(session)}>
          <span className="assistant-chatgpt-title">{title}</span>
        </button>
        <div className="assistant-chatgpt-actions">
          <button type="button" className={isPinned ? 'assistant-chatgpt-pin pinned' : 'assistant-chatgpt-pin'} aria-label={isPinned ? `Unpin ${title}` : `Pin ${title}`} title={isPinned ? 'Unpin chat' : 'Pin chat'} onClick={() => togglePinned(session.id)}>⌖</button>
          <button type="button" className="assistant-chatgpt-more" aria-label={`More options for ${title}`} aria-expanded={menuOpen} title="More options" onClick={() => setOpenMenuId(menuOpen ? null : session.id)}>•••</button>
        </div>
        {menuOpen ? (
          <div className="assistant-chatgpt-menu" role="menu">
            {menuItem('⇧', 'Share', () => void share(session))}
            {menuItem('✎', 'Rename', () => rename(session))}
            {menuItem('⌖', isPinned ? 'Unpin chat' : 'Pin chat', () => togglePinned(session.id))}
            {menuItem('▣', 'Archive', () => archive(session.id))}
            {menuItem('⌫', 'Delete', () => void remove(session), true)}
          </div>
        ) : null}
      </div>
    );
  }

  const newChatButton = (
    <button type="button" className="assistant-chatgpt-new" aria-label={creating ? 'Creating new chat' : 'New chat'} aria-busy={creating || undefined} title="New chat" disabled={creating} onClick={() => void createChat()}>
      <span aria-hidden="true">＋</span><span>{creating ? 'Creating...' : 'New'}</span>
    </button>
  );

  return (
    <div className="assistant-chatgpt-sidebar" ref={hostRef}>
      {!failed ? (
        <section className="assistant-chatgpt-section" aria-labelledby="assistant-chatgpt-pinned">
          <header><h2 id="assistant-chatgpt-pinned">Pinned</h2></header>
          <div className="assistant-chatgpt-list">
            {pinned.length ? pinned.map((session) => row(session, true)) : <p className="assistant-chatgpt-empty">No pinned chats yet.</p>}
          </div>
        </section>
      ) : null}
      <section className="assistant-chatgpt-section" aria-labelledby="assistant-chatgpt-sessions">
        <header><h2 id="assistant-chatgpt-sessions">Sessions</h2>{newChatButton}</header>
        <div className="assistant-chatgpt-list">
          {failed ? <p className="assistant-chatgpt-empty">Chat sessions failed to load.</p>
            : loading ? <p className="assistant-chatgpt-empty">Loading chat sessions...</p>
              : regular.length ? regular.map((session) => row(session, false)) : <p className="assistant-chatgpt-empty">No other chat sessions.</p>}
        </div>
      </section>
      <div className={`assistant-chatgpt-status${status ? ' visible' : ''}${status?.danger ? ' danger' : ''}`} role="status" aria-live="polite">{status?.message ?? ''}</div>
    </div>
  );
}
