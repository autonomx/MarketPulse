import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatSidebarSessions } from './ChatSidebarSessions';
import { readChatSidebarState, sortChatSidebarSessions, visibleChatSidebarSessions, writeChatSidebarState } from './chatSidebarState';
import { installSessionTools } from './sessionTools';

const sessions = [
  { id: 'chat:one', title: 'First chat', provider_id: 'lm-studio', model_id: 'local-model', interaction_mode: 'character' as const, character_id: 'maya', voice_asset_id: 'voice-cloning:Maya', read_memory: true, write_memory: false, shared_memory_access: 'read_only' as const, transcript_policy: 'temporary' as const, created_at: '2026-08-08T00:00:00Z', updated_at: '2026-08-08T00:01:00Z' },
  { id: 'chat:two', title: 'Second chat', created_at: '2026-08-08T00:00:00Z', updated_at: '2026-08-08T00:02:00Z' },
];

type Session = (typeof sessions)[number];

describe('chat sidebar sessions', () => {
  let disposeSessionTools: () => void;
  let fetchMock: ReturnType<typeof vi.fn>;
  const onSelect = vi.fn();
  const onDelete = vi.fn(async () => undefined);

  function renderSidebar(selectedSessionId: string | null = null) {
    return render(
      <ChatSidebarSessions<Session> sessions={sessions} loading={false} failed={false} selectedSessionId={selectedSessionId} onSelect={onSelect} onDelete={onDelete} />,
    );
  }

  beforeEach(() => {
    window.localStorage.clear();
    onSelect.mockClear();
    onDelete.mockClear();
    fetchMock = vi.fn(async () => Response.json({ id: 'chat:new-session', title: 'New chat', messages: [], message_count: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    disposeSessionTools = installSessionTools();
  });

  afterEach(() => {
    cleanup();
    disposeSessionTools();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('places the newest chat session first', () => {
    const unsorted = [
      { id: 'chat:old', title: 'Older chat', updated_at: '2026-08-08T00:01:00Z' },
      { id: 'chat:new', title: 'New chat', created_at: '2026-08-08T00:03:00Z' },
      { id: 'chat:middle', title: 'Middle chat', updated_at: '2026-08-08T00:02:00Z' },
    ];
    expect(sortChatSidebarSessions(unsorted).map((session) => session.id)).toEqual(['chat:new', 'chat:middle', 'chat:old']);
  });

  it('renders Pinned before Sessions with one New control and row actions', () => {
    renderSidebar('chat:one');

    expect(screen.getAllByRole('heading').map((heading) => heading.textContent)).toEqual(['Pinned', 'Sessions']);
    expect(screen.getAllByRole('button', { name: 'New chat' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Second chat' }).closest('.assistant-chatgpt-row')).not.toHaveClass('active');
    expect(screen.getByRole('button', { name: 'First chat' }).closest('.assistant-chatgpt-row')).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Pin First chat' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'More options for First chat' }));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map((item) => item.textContent?.replace(/^[^A-Za-z]+/, '').trim()))
      .toEqual(['Share', 'Rename', 'Pin chat', 'Archive', 'Delete']);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('selects a session and starts a new chat that keeps its interaction settings', async () => {
    renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: 'First chat' }));
    expect(onSelect).toHaveBeenCalledWith(sessions[0]);
    window.dispatchEvent(new CustomEvent('omnix:chat-session-selected', { detail: { sessionId: 'chat:one', session: sessions[0] } }));
    const created = vi.fn();
    window.addEventListener('omnix:chat-session-created', created, { once: true });

    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));

    await vi.waitFor(() => expect(created).toHaveBeenCalledOnce());
    const postCall = fetchMock.mock.calls.find(([, init]) => String((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase() === 'POST');
    expect(JSON.parse(String((postCall?.[1] as RequestInit).body))).toMatchObject({
      title: 'New chat',
      provider_id: 'lm-studio',
      interaction_mode: 'character',
      character_id: 'maya',
      transcript_policy: 'temporary',
    });
    expect(await screen.findByRole('button', { name: 'New chat' })).not.toBeDisabled();
  });

  it('moves a pinned chat into Pinned and archives one out of the list', () => {
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Pin First chat' }));
    const pinned = screen.getByRole('heading', { name: 'Pinned' }).closest('section')!;
    expect(within(pinned).getByRole('button', { name: 'First chat' })).toBeInTheDocument();
    expect(readChatSidebarState()['chat:one']?.pinned).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'More options for Second chat' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Archive/ }));
    expect(screen.queryByRole('button', { name: 'Second chat' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Chat archived.');
  });

  it('deletes through Chat after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    writeChatSidebarState({ 'chat:two': { title: 'Renamed' } });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'More options for Renamed' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Delete/ }));

    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Chat deleted.'));
    expect(onDelete).toHaveBeenCalledWith(sessions[1]);
    expect(readChatSidebarState()['chat:two']).toBeUndefined();
  });

  it('filters archived and podcast sessions', () => {
    writeChatSidebarState({ 'chat:archived': { archived: true } });
    const visible = visibleChatSidebarSessions([
      { id: 'chat:archived', title: 'Old chat' },
      { id: 'chat:podcast', title: 'Podcast script: episode 1' },
      { id: 'chat:normal', title: 'Normal chat' },
    ], readChatSidebarState());
    expect(visible.map((session) => session.id)).toEqual(['chat:normal']);
  });
});
