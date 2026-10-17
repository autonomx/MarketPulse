/* eslint-disable @typescript-eslint/no-this-alias -- baseline WP-9.x */
import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../../app/modules';
import { omnixTheme } from '../../../design/theme';
import { ChatbotWorkspace } from './ChatbotWorkspace';
import { selectFreshChatSession } from './chatMessageModel';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderChatbot() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'chatbot');

  if (!module) {
    throw new Error('Chatbot module is missing');
  }

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <ChatbotWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

function providerPayload() {
  return {
    providers: [
      {
        id: 'openai',
        label: 'OpenAI compatible',
        family: 'llm',
        source: 'settings',
        status: 'configured',
        capabilities: ['chat'],
      },
    ],
    models: [
      {
        id: 'gpt-mini',
        label: 'GPT mini',
        provider_id: 'openai',
        location: 'remote',
        capabilities: ['chat'],
      },
    ],
  };
}

function assetPayload() {
  return {
    assets: [
      {
        id: 'voice-profile-1',
        type: 'voice_profile',
        module: 'voice-cloning',
        title: 'Ari Clone',
        file_name: 'ari-clone.json',
        metadata: { voice_id: 'ari-clone', profile_name: 'Ari Clone' },
        created_at: '2026-06-14T00:00:00Z',
        updated_at: '2026-06-14T00:00:00Z',
      },
    ],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

afterEach(() => {
  window.localStorage.clear();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('ChatbotWorkspace', () => {
  it('keeps the selected session when a previous mutation snapshot is still cached', () => {
    const previous = {
      id: 'chat:previous',
      message_count: 8,
      messages: [{ id: 'msg:previous', role: 'assistant', content: 'Previous chat' }],
    };
    const selected = {
      id: 'chat:selected',
      message_count: 2,
      messages: [
        { id: 'msg:selected-user', role: 'user', content: 'Selected chat' },
        { id: 'msg:selected-assistant', role: 'assistant', content: 'Selected reply' },
      ],
    };

    expect(selectFreshChatSession(previous, selected)).toBe(selected);
  });

  it('prefers the fuller transcript when response counts tie', () => {
    const queued = {
      id: 'chat:research',
      message_count: 4,
      messages: [
        { id: 'msg:recent-user', role: 'user', content: 'Recent prompt' },
        { id: 'msg:recent-assistant', role: 'assistant', content: 'Recent answer' },
      ],
    };
    const refreshed = {
      id: 'chat:research',
      message_count: 4,
      messages: [
        { id: 'msg:old-user', role: 'user', content: 'Earlier prompt' },
        { id: 'msg:old-assistant', role: 'assistant', content: 'Earlier answer' },
        ...queued.messages,
      ],
    };

    expect(selectFreshChatSession(queued, refreshed)).toBe(refreshed);
  });

  it('prefers a refreshed session over the queued mutation snapshot', () => {
    const queued = {
      id: 'chat:research',
      message_count: 1,
      messages: [{ id: 'msg:user', role: 'user', content: 'Research this' }],
    };
    const refreshed = {
      id: 'chat:research',
      message_count: 2,
      messages: [
        { id: 'msg:user', role: 'user', content: 'Research this' },
        { id: 'msg:assistant', role: 'assistant', content: 'Research result' },
      ],
    };

    expect(selectFreshChatSession(queued, refreshed)).toBe(refreshed);
  });

  it('shows chat loading states instead of empty states while sessions are pending', async () => {
    const sessions = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/api/chat/sessions') {
        return sessions.promise;
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    expect(await screen.findByText('Loading chat sessions...')).toBeInTheDocument();
    expect(screen.getByText('Loading chat messages...')).toBeInTheDocument();
    expect(screen.queryByText('No other chat sessions.')).not.toBeInTheDocument();
    expect(screen.queryByText('No chat messages yet.')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input) === '/api/assets')).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input) === '/api/voice-library')).toBe(false);

    sessions.resolve(Response.json({ sessions: [] }));

    expect(await screen.findByText('No other chat sessions.')).toBeInTheDocument();
    expect(await screen.findByText('No chat messages yet.')).toBeInTheDocument();
  });

  it('projects the selected character identity into the Live Voice rail without starting a call', async () => {
    const characterSession = {
      id: 'chat:character',
      title: 'Chat with Sofia',
      interaction_mode: 'character',
      character_id: 'sofia',
      voice_asset_id: 'voice-cloning:sofia',
      message_count: 0,
      messages: [],
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets' || path === '/api/voice-library') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [characterSession] });
      if (path === '/api/chat/sessions/chat%3Acharacter') return Response.json(characterSession);
      if (path === '/api/chat/sessions/chat%3Acharacter/interaction') {
        return Response.json({
          id: characterSession.id,
          title: characterSession.title,
          interaction_mode: 'character',
          character_id: 'sofia',
          voice_asset_id: 'voice-cloning:sofia',
          read_memory: false,
          write_memory: false,
          shared_memory_access: 'none',
          transcript_policy: 'persistent',
          messages: [],
        });
      }
      if (path === '/api/characters') {
        return Response.json({ characters: [{
          id: 'sofia',
          display_name: 'Sofia',
          description: '',
          personality_prompt: '',
          default_greeting: '',
          default_voice_asset_id: 'voice-cloning:sofia',
          speech_style: {},
          identity_policy: {},
          shared_memory_policy: {},
          active_version: 1,
          enabled: true,
          status: 'active',
          created_at: '2026-06-14T00:00:00Z',
          updated_at: '2026-06-14T00:00:00Z',
        }] });
      }
      if (path === '/api/chat/sessions/chat%3Acharacter/live-call/runtime') {
        return Response.json({
          session_id: characterSession.id,
          interaction_mode: 'character',
          display_name: 'Sofia',
          character_id: 'sofia',
          character_profile_version: 1,
          voice_asset_id: 'voice-cloning:sofia',
          greeting: '',
          speech_style: { speed: 1, temperature: 0.6, top_k: 20, top_p: 0.85, repetition_penalty: 1, expressiveness: 'neutral', emotion: 'neutral', interruption_style: 'balanced' },
          read_memory: false,
          write_memory: false,
          shared_memory_access: 'none',
          memory_snapshot_id: null,
          preload: { profile_loaded: true, voice_resolved: true, memory_snapshot_loaded: true, memory_record_count: 0, preload_ms: 1, resolved_at: '2026-06-14T00:00:00Z' },
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await waitFor(() => {
      expect(document.querySelector('.assistant-live-identity')).toHaveTextContent('Character Mode · Sofia');
    });
    expect(document.querySelector('.assistant-live-identity')).toHaveTextContent('Character Mode · Sofia');
    expect(screen.getByRole('button', { name: 'Start Call' })).toBeInTheDocument();
  });

  it('keeps a newly created chat selected while the session list catches up', async () => {
    const existingSession = {
      id: 'chat:existing',
      title: 'Existing chat',
      message_count: 1,
      messages: [{ id: 'msg:existing', role: 'user', content: 'Existing message', created_at: '2026-06-14T00:00:01Z' }],
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:01Z',
    };
    const newSession = {
      id: 'chat:new',
      title: 'New chat',
      message_count: 0,
      messages: [],
      created_at: '2026-06-14T00:01:00Z',
      updated_at: '2026-06-14T00:01:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [existingSession] });
      if (path === '/api/chat/sessions/chat%3Aexisting') return Response.json(existingSession);
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();
    await screen.findAllByText('Existing chat');

    act(() => {
      window.dispatchEvent(new CustomEvent('omnix:chat-session-created', { detail: { session: newSession } }));
      window.dispatchEvent(new CustomEvent('omnix:live-chat-session-changed', { detail: { sessionId: newSession.id } }));
    });

    await waitFor(() => {
      expect(document.querySelector('.assistant-chat-header h2')).toBeNull();
    });
    expect(document.querySelector('.assistant-chat-header')).not.toHaveTextContent('New chat');
    expect(document.querySelector('.assistant-chat-header')).not.toHaveTextContent('Existing chat');
  });

  it('keeps the fullscreen action from overlapping the Personality control', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    const page = document.querySelector('.assistant-chat-page');
    expect(page).not.toBeNull();
    const enterFullscreen = screen.getByRole('button', { name: 'Enter full screen chat' });
    const integratedActions = page?.querySelector('.assistant-chat-integrated-actions');
    const identityControl = page?.querySelector('.chat-identity-mode-control');

    expect(integratedActions).not.toBeNull();
    expect(identityControl).not.toBeNull();
    expect(identityControl?.parentElement).toBe(integratedActions);
    expect(enterFullscreen.parentElement).toBe(integratedActions);
    expect(enterFullscreen.previousElementSibling).toBe(identityControl);

    fireEvent.click(enterFullscreen);

    expect(page).toHaveClass('assistant-chat-page-fullscreen');
    expect(screen.getByRole('button', { name: 'Exit full screen chat' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(page).not.toHaveClass('assistant-chat-page-fullscreen');
    expect(screen.getByRole('button', { name: 'Enter full screen chat' })).toHaveAttribute('aria-pressed', 'false');
    expect(document.body.style.overflow).toBe('');
  });

  it('minimizes and restores the chat side panel', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    const minimize = screen.getByRole('button', { name: 'Minimize side panel' });
    expect(minimize).toHaveAttribute('aria-pressed', 'false');
    expect(minimize).toHaveTextContent('›');

    fireEvent.click(minimize);

    const expand = screen.getByRole('button', { name: 'Expand side panel' });
    expect(expand).toHaveAttribute('aria-pressed', 'true');
    expect(expand).toHaveTextContent('‹');
    expect(document.querySelector('.assistant-chat-layout')).toHaveClass('assistant-chat-layout-side-minimized');
    expect(document.querySelector('.assistant-chat-side')).toHaveClass('assistant-chat-side-minimized');
    expect(window.localStorage.getItem('omnix.chatbot.sidePanelMinimized')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Expand side panel' }));

    expect(screen.getByRole('button', { name: 'Minimize side panel' })).toHaveAttribute('aria-pressed', 'false');
    expect(document.querySelector('.assistant-chat-layout')).not.toHaveClass('assistant-chat-layout-side-minimized');
  });

  it('minimizes and restores the assistant navigation sidebar', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    const minimize = screen.getByRole('button', { name: 'Minimize assistant sidebar' });
    expect(minimize).toHaveAttribute('aria-pressed', 'false');
    expect(minimize).toHaveTextContent('‹');

    fireEvent.click(minimize);

    const expand = screen.getByRole('button', { name: 'Expand assistant sidebar' });
    expect(expand).toHaveAttribute('aria-pressed', 'true');
    expect(expand).toHaveTextContent('›');
    expect(document.querySelector('.assistant-chat-layout')).toHaveClass('assistant-chat-layout-sidebar-minimized');
    expect(document.querySelector('.assistant-chat-sidebar')).toHaveClass('assistant-chat-sidebar-minimized');
    expect(window.localStorage.getItem('omnix.chatbot.assistantSidebarMinimized')).toBe('true');

    fireEvent.click(expand);

    expect(screen.getByRole('button', { name: 'Minimize assistant sidebar' })).toHaveAttribute('aria-pressed', 'false');
    expect(document.querySelector('.assistant-chat-layout')).not.toHaveClass('assistant-chat-layout-sidebar-minimized');
  });

  it('keeps the side panel focused on Live Voice without a duplicate Tools tab', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    const sidePanel = screen.getByRole('complementary', { name: 'Live voice and tool execution' });
    expect(within(sidePanel).getByRole('button', { name: 'Live Voice' })).toBeInTheDocument();
    expect(within(sidePanel).queryByRole('button', { name: /^Tools$/ })).not.toBeInTheDocument();
    expect(within(sidePanel).queryByText('Assistant workspace', { exact: true })).not.toBeInTheDocument();
    expect(within(sidePanel).queryByText('Workspace activity', { exact: true })).not.toBeInTheDocument();
    expect(sidePanel.querySelector('.assistant-supporting-panels')).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Tools view' })).toBeInTheDocument();
  });

  it('restores the minimized side panel state from local storage', async () => {
    window.localStorage.setItem('omnix.chatbot.sidePanelMinimized', 'true');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    expect(screen.getByRole('button', { name: 'Expand side panel' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.querySelector('.assistant-chat-layout')).toHaveClass('assistant-chat-layout-side-minimized');
    expect(document.querySelector('.assistant-chat-side')).toHaveClass('assistant-chat-side-minimized');
  });

  it('accepts multiple pasted images, previews them, and sends them with the chat message', async () => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
    const firstImageDataUrl = 'data:image/png;base64,Zmlyc3QtaW1hZ2U=';
    const secondImageDataUrl = 'data:image/jpeg;base64,c2Vjb25kLWltYWdl';
    let session = {
      id: 'chat:image',
      title: 'Image chat',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [] as Array<{ id: string; role: 'user' | 'assistant'; content: string; created_at: string; metadata?: Record<string, unknown> }>,
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions' && init?.method === 'POST') return Response.json(session);
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/chat/sessions/chat%3Aimage') return Response.json(session);
      if (path === '/api/chat/sessions/chat%3Aimage/messages') {
        session = {
          ...session,
          message_count: 2,
          messages: [
            { id: 'msg:image-user', role: 'user', content: 'Compare these images', created_at: '2026-06-14T00:00:01Z', metadata: { image_data_url: firstImageDataUrl, image_data_urls: [firstImageDataUrl, secondImageDataUrl] } },
            { id: 'msg:image-assistant', role: 'assistant', content: 'I can see both attached images.', created_at: '2026-06-14T00:00:02Z' },
          ],
        };
        return Response.json({
          generation_status: 'queued',
          session,
          user_message: session.messages[0],
          job: { id: 'job:image', module: 'chatbot', type: 'chat.generate', status: 'queued', resource_class: 'gpu:llm', created_at: '2026-06-14T00:00:01Z', updated_at: '2026-06-14T00:00:01Z', priority: 0 },
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const originalFileReader = globalThis.FileReader;
    class TestFileReader {
      result: string | ArrayBuffer | null = null;
      error: DOMException | null = null;
      onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null;
      onerror: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null;
      readAsDataURL(file: Blob): void {
        const typed = file as File;
        this.result = typed.type === 'image/jpeg' ? secondImageDataUrl : firstImageDataUrl;
        queueMicrotask(() => this.onload?.call(this as unknown as FileReader, new ProgressEvent('load') as ProgressEvent<FileReader>));
      }
    }
    vi.stubGlobal('FileReader', TestFileReader as unknown as typeof FileReader);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    const textarea = screen.getByLabelText('Message');
    const firstImage = new File(['first-image'], 'first.png', { type: 'image/png' });
    const secondImage = new File(['second-image'], 'second.jpg', { type: 'image/jpeg' });
    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          { type: 'image/png', getAsFile: () => firstImage },
          { type: 'image/jpeg', getAsFile: () => secondImage },
        ],
      },
    });

    expect(await screen.findByAltText('Attached image preview 1')).toBeInTheDocument();
    expect(await screen.findByAltText('Attached image preview 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove attached image 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove attached image 2' })).toBeInTheDocument();
    fireEvent.change(textarea, { target: { value: 'Compare these images' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    await waitFor(() => {
      const messageCall = fetchMock.mock.calls.find(
        ([input, callInit]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && callInit?.method === 'POST',
      );
      expect(messageCall?.[1]?.body).toContain(`"image_data_urls":["${firstImageDataUrl}","${secondImageDataUrl}"]`);
      expect(messageCall?.[1]?.body).not.toContain('"image_data_url":');
    });
    expect(await screen.findByAltText('User-provided attachment')).toBeInTheDocument();
    expect(await screen.findByAltText('User-provided attachment 2')).toBeInTheDocument();
    expect((await screen.findAllByText('I can see both attached images.')).length).toBeGreaterThan(0);

    vi.stubGlobal('FileReader', originalFileReader);
  });

  it('sends a text file chosen from the add menu through the normal chat request', async () => {
    const attachedFile = { filename: 'notes.md', mimeType: 'text/markdown', size: 7, text: '# Notes' };
    const session = {
      id: 'chat:file', title: 'File chat', provider_id: 'openai', model_id: 'gpt-mini', message_count: 0, messages: [],
      created_at: '2026-06-14T00:00:00Z', updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions' && init?.method === 'POST') return Response.json(session);
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/chat/sessions/chat%3Afile/messages') return Response.json({ generation_status: 'queued', session, user_message: { id: 'msg:file', role: 'user', content: 'Please analyze the attached file.', created_at: '2026-06-14T00:00:01Z' }, job: { id: 'job:file', module: 'chatbot', type: 'chat.generate', status: 'queued', resource_class: 'gpu:llm', created_at: '2026-06-14T00:00:01Z', updated_at: '2026-06-14T00:00:01Z', priority: 0 } });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();
    await screen.findByText('No chat messages yet.');
    window.dispatchEvent(new CustomEvent('omnix:chat-text-file-selected', { detail: attachedFile }));

    expect(await screen.findByText('notes.md')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    await waitFor(() => {
      const messageCall = fetchMock.mock.calls.find(
        ([input, callInit]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && callInit?.method === 'POST',
      );
      expect(messageCall?.[1]?.body).toContain('"text_attachment":{"filename":"notes.md","mime_type":"text/markdown","text":"# Notes"}');
    });
  });

  it('opens Live Chat from the sidebar, right after Chats', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/characters') return Response.json({ characters: [] });
      return new Response('not found', { status: 404 });
    }));

    renderChatbot();

    const liveChat = await screen.findByRole('button', { name: 'Open Live Chat view' });
    expect(liveChat.previousElementSibling).toHaveAccessibleName('Open Chats view');
    fireEvent.click(liveChat);

    expect(await screen.findByRole('heading', { name: 'Live Chat' })).toBeInTheDocument();
    expect(liveChat).toHaveClass('active');
    expect(screen.queryByRole('textbox', { name: 'Message' })).not.toBeInTheDocument();
  });

  it('opens a dedicated Characters destination from the assistant sidebar', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/characters') return Response.json({ characters: [] });

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    fireEvent.click(await screen.findByRole('button', { name: 'Open Characters view' }));

    expect(await screen.findByRole('heading', { name: 'Characters' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Profiles and relationship data' })).toBeInTheDocument();
    expect(await screen.findByText('No characters have been created.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Memory' })).not.toBeInTheDocument();
  });

  it('opens an existing chat scrolled to the latest message', async () => {
    const scrollIntoViewMock = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoViewMock,
    });
    const session = {
      id: 'chat:scroll',
      title: 'Long chat',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 3,
      messages: [
        { id: 'msg:old', role: 'user', content: 'Oldest message', created_at: '2026-06-14T00:00:01Z' },
        { id: 'msg:middle', role: 'assistant', content: 'Middle message', created_at: '2026-06-14T00:00:02Z' },
        { id: 'msg:latest', role: 'assistant', content: 'Newest message', created_at: '2026-06-14T00:00:03Z' },
      ],
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:03Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/api/chat/sessions') {
        return Response.json({ sessions: [session] });
      }

      if (path === '/api/chat/sessions/chat%3Ascroll') {
        return Response.json(session);
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    expect((await screen.findAllByText('Newest message')).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(scrollIntoViewMock).toHaveBeenCalledWith({ block: 'end', behavior: 'auto' });
    });
  });

  it('uses provider/model selectors and renders the assistant response with activity events', async () => {
    vi.stubEnv('VITE_ASSISTANT_TTS_URL', 'http://tts.local');
    vi.stubEnv('VITE_ASSISTANT_TTS_VOICE', 'narrator-clone');
    const playMock = vi.fn().mockResolvedValue(undefined);
    const audioCtor = vi.fn().mockImplementation((src: string) => ({ src, play: playMock }));
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('Audio', audioCtor);
    Object.assign(navigator, { clipboard: { writeText: writeTextMock } });

    let session: {
      id: string;
      title: string;
      provider_id: string;
      model_id: string;
      message_count: number;
      messages: Array<{ id: string; role: 'system' | 'user' | 'assistant'; content: string; created_at: string; metadata?: Record<string, unknown> }>;
      created_at: string;
      updated_at: string;
    } = {
      id: 'chat:1',
      title: 'Hello Omnix',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [],
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/synthesize') {
        return Response.json({
          audioBase64: 'UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=',
          mimeType: 'audio/wav',
        });
      }

      if (path === '/api/chat/sessions' && init?.method === 'POST') {
        return Response.json(session);
      }

      if (path === '/api/chat/sessions') {
        return Response.json({ sessions: [] });
      }

      if (path === '/api/chat/sessions/chat%3A1') {
        return Response.json(session);
      }

      if (path === '/api/chat/sessions/chat%3A1/messages') {
        session = {
          ...session,
          message_count: 2,
          messages: [
            {
              id: 'msg:1',
              role: 'user',
              content: 'Hello Omnix',
              created_at: '2026-06-14T00:00:01Z',
              metadata: { generation_status: 'completed' },
            },
            {
              id: 'msg:2',
              role: 'assistant',
              content: 'Provider reply from the selected model.',
              created_at: '2026-06-14T00:00:02Z',
              metadata: { generation_status: 'completed' },
            },
          ],
        };
        return Response.json({
          generation_status: 'queued',
          session,
          user_message: session.messages[0],
          job: {
            id: 'job:1',
            module: 'chatbot',
            type: 'chat.generate',
            status: 'queued',
            resource_class: 'gpu:llm',
            created_at: '2026-06-14T00:00:01Z',
            updated_at: '2026-06-14T00:00:01Z',
            priority: 0,
          },
        });
      }

      if (path === '/api/jobs/job%3A1') {
        return Response.json({ id: 'job:1', status: 'completed', input_payload: { session_id: 'chat:1' } });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    expect(await screen.findByText('No chat messages yet.')).toBeInTheDocument();
    expect(await screen.findByText('OpenAI compatible')).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'Ari Clone' })).toBeInTheDocument();
    expect(screen.getByText('Mic input')).toBeInTheDocument();
    const chatHeader = document.querySelector('.assistant-chat-header');
    expect(chatHeader).not.toBeNull();
    expect(within(chatHeader as HTMLElement).queryByRole('button', { name: /Tools/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Share' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Star conversation' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
    expect(screen.queryByText('Workspace activity')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('System voice'), { target: { value: 'ari-clone' } });
    expect(screen.getByLabelText('System voice')).toHaveValue('ari-clone');
    expect(JSON.parse(window.localStorage.getItem('omnix.chatbot.assistantSettings') ?? '{}')).toMatchObject({ voiceId: 'ari-clone' });

    fireEvent.click(screen.getByRole('button', { name: 'Open Settings view' }));
    expect(screen.getByLabelText('Live mic sensitivity')).toHaveValue('55');
    fireEvent.change(screen.getByLabelText('Coding agent permissions'), { target: { value: 'always_ask' } });
    expect(screen.getByLabelText('Coding agent permissions')).toHaveValue('always_ask');
    expect(JSON.parse(window.localStorage.getItem('omnix.chatbot.assistantSettings') ?? '{}')).toMatchObject({ codingApprovalPolicy: 'always_ask' });
    fireEvent.change(screen.getByLabelText('Live mic sensitivity'), { target: { value: '35' } });
    expect(JSON.parse(window.localStorage.getItem('omnix.chatbot.assistantSettings') ?? '{}')).toMatchObject({ liveVoiceSensitivity: 35 });
    fireEvent.click(screen.getByRole('button', { name: 'Open Chats view' }));

    fireEvent.click(screen.getByRole('button', { name: 'Tell me a fun fact' }));
    expect(screen.getByLabelText('Message')).toHaveValue('Tell me a fun fact');
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-mini' } });
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Hello Omnix' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    expect(await screen.findByText('Response ready: job:1')).toBeInTheDocument();
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('session')).toBe('chat:1'));
    expect((await screen.findAllByText('Provider reply from the selected model.')).length).toBeGreaterThan(0);
    const transcriptMessage = screen.getAllByText('Hello Omnix').find((element) => within(element.closest('article') ?? element).queryByText('You'));
    expect(transcriptMessage ?? screen.getByText('Hello Omnix')).toBeTruthy();
    const voiceTranscript = screen.getByText('Transcript').closest('.assistant-voice-transcript');
    expect(voiceTranscript).not.toBeNull();
    expect(within(voiceTranscript as HTMLElement).getByText('Provider reply from the selected model.')).toBeInTheDocument();
    fireEvent.click(within(voiceTranscript as HTMLElement).getByRole('button', { name: 'Clear' }));
    expect(within(voiceTranscript as HTMLElement).queryByText('Provider reply from the selected model.')).not.toBeInTheDocument();
    expect(within(voiceTranscript as HTMLElement).getByText('Voice transcript will appear here during live calls.')).toBeInTheDocument();

    expect(screen.getAllByRole('button', { name: 'Play response audio' }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: 'Like response' })[0]);
    expect(screen.getAllByRole('button', { name: 'Like response' })[0]).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getAllByRole('button', { name: 'Copy response' })[0]);
    await waitFor(() => expect(writeTextMock).toHaveBeenCalledWith('Provider reply from the selected model.'));
    fireEvent.click(screen.getAllByRole('button', { name: 'More response actions' })[0]);
    expect(screen.getByRole('menuitem', { name: 'Copy text' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Play audio' })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Play response audio' })[0]);

    await waitFor(() => {
      const playedAudio = audioCtor.mock.calls.length > 0 && playMock.mock.calls.length > 0;
      const voiceStatus = screen.queryAllByText(/Playing response voice|Playing cloned response voice|Configure VITE_ASSISTANT_TTS_URL|Voice Studio|Omnix API request failed/).length > 0;
      expect(playedAudio || voiceStatus).toBeTruthy();
    });

    await waitFor(() => {
      const persistedEvents = JSON.parse(window.localStorage.getItem('omnix.assistantWorkspace.events') ?? '[]') as unknown[];
      expect(persistedEvents).toHaveLength(2);
    });

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL) === '/api/chat/sessions' && init?.method === 'POST',
      );
      const messageCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && init?.method === 'POST',
      );
      expect(createCall?.[1]).toEqual(expect.objectContaining({ method: 'POST' }));
      expect(createCall?.[1]?.body).toContain('"provider_id":"openai"');
      expect(createCall?.[1]?.body).toContain('"model_id":"gpt-mini"');
      expect(messageCall?.[1]).toEqual(expect.objectContaining({ method: 'POST' }));
      expect(messageCall?.[1]?.body).toContain('"model_id":"gpt-mini"');
      expect(messageCall?.[1]?.body).toContain('"coding_approval_policy":"always_ask"');
      expect(messageCall?.[1]?.body).toMatch(/"user_turn_id":"web-user-turn:[^"]+"/);
    });
  });

  it('starts, advances, and resets the live call timer from call controls', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/api/chat/sessions') {
        return Response.json({ sessions: [] });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    expect(await screen.findByText('No chat messages yet.')).toBeInTheDocument();
    expect(screen.getByText('00:00:00')).toBeInTheDocument();
    expect(screen.getByText('Disconnected')).toBeInTheDocument();

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: 'Start Call' }));

    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.queryByText('Live voice call started.') ?? screen.queryByText(/Browser speech recognition/)).toBeTruthy();
    act(() => {
      window.dispatchEvent(new CustomEvent('omnix:assistant-voice-interrupt'));
    });
    expect(screen.getByText('Interrupted. Listening for your next message.')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(65_000);
    });

    expect(screen.getByText('00:01:05')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'End Call' }));

    expect(screen.getByText('00:00:00')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Call' })).toBeInTheDocument();
    expect(screen.getByText('Live voice call ended.')).toBeInTheDocument();
  });

  it('auto-sends finalized live speech into the chat stream', async () => {
    let recognitionInstance: {
      continuous: boolean;
      interimResults: boolean;
      lang: string;
      onresult: ((event: unknown) => void) | null;
      onerror: ((event: unknown) => void) | null;
      onend: (() => void) | null;
      start: ReturnType<typeof vi.fn>;
      stop: ReturnType<typeof vi.fn>;
      abort: ReturnType<typeof vi.fn>;
    } | null = null;

    class FakeSpeechRecognition {
      continuous = false;
      interimResults = false;
      lang = '';
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = vi.fn();
      stop = vi.fn();
      abort = vi.fn();

      constructor() {
        recognitionInstance = this;
      }
    }

    vi.stubGlobal('SpeechRecognition', FakeSpeechRecognition);

    let session = {
      id: 'chat:voice-auto',
      title: 'Voice command',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [] as Array<{ id: string; role: 'system' | 'user' | 'assistant'; content: string; created_at: string }>,
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions' && init?.method === 'POST') return Response.json(session);
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/chat/sessions/chat%3Avoice-auto/messages/stream') {
        session = {
          ...session,
          message_count: 2,
          messages: [
            { id: 'msg:voice-user', role: 'user', content: 'open the pod bay doors', created_at: '2026-06-14T00:00:01Z' },
            { id: 'msg:voice-assistant', role: 'assistant', content: 'Opening them now.', created_at: '2026-06-14T00:00:02Z' },
          ],
        };
        return new Response(
          [
            'data: {"type":"text_chunk","text":"Opening them now."}\n\n',
            `data: ${JSON.stringify({ type: 'session', session })}\n\n`,
            'data: {"type":"done"}\n\n',
          ].join(''),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    fireEvent.click(screen.getByLabelText('Auto-speak assistant replies'));
    fireEvent.click(screen.getByRole('button', { name: 'Start Call' }));

    await waitFor(() => expect(recognitionInstance).not.toBeNull());

    act(() => {
      window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
        detail: { stage: 'stt_final_requested', turnId: 'voice-turn:test-auto' },
      }));
      window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
        detail: {
          stage: 'stt_final_received',
          turnId: 'voice-turn:test-auto',
          transcriptChars: 22,
          sttFinalizeMs: 180,
        },
      }));
      recognitionInstance?.onresult?.({
        resultIndex: 0,
        results: {
          length: 1,
          0: { isFinal: true, 0: { transcript: 'open the pod bay doors' } },
        },
      });
    });

    expect(screen.getByText('open the pod bay doors')).toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 950));
    });

    await waitFor(() => {
      const streamCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages/stream') && init?.method === 'POST',
      );
      expect(streamCall?.[1]?.body).toContain('"content":"open the pod bay doors"');
    });
    expect((await screen.findAllByText('Opening them now.')).length).toBeGreaterThan(0);
    await waitFor(() => {
      const diagnosticBodies = fetchMock.mock.calls
        .filter(([input, init]) => requestPath(input as RequestInfo | URL) === '/api/tts/live-call/diagnostics' && init?.method === 'POST')
        .map(([, init]) => String(init?.body ?? ''))
        .join('\n');
      expect(diagnosticBodies).toContain('"trace_id":"live-call:voice-turn:test-auto"');
      expect(diagnosticBodies).toContain('"event":"chat_submit_started"');
      expect(diagnosticBodies).toContain('"event":"chat_response_opened"');
      expect(diagnosticBodies).toContain('"event":"llm_first_text_chunk_received"');
      expect(diagnosticBodies).toContain('"event":"llm_stream_completed"');
      expect(diagnosticBodies).not.toContain('open the pod bay doors');
      expect(diagnosticBodies).not.toContain('Opening them now.');
    });

    act(() => {
      recognitionInstance?.onresult?.({
        resultIndex: 0,
        results: {
          length: 1,
          0: { isFinal: true, 0: { transcript: 'Open the pod bay doors!' } },
        },
      });
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 950));
    });
    const streamCalls = fetchMock.mock.calls.filter(
      ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages/stream') && init?.method === 'POST',
    );
    expect(streamCalls).toHaveLength(1);
  });

  it('streams main composer submissions while a live call is active', async () => {
    class FakeSpeechRecognition {
      continuous = false;
      interimResults = false;
      lang = '';
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = vi.fn();
      stop = vi.fn();
      abort = vi.fn();
    }

    vi.stubGlobal('SpeechRecognition', FakeSpeechRecognition);

    let session = {
      id: 'chat:live-typed',
      title: 'Live typed',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [] as Array<{ id: string; role: 'system' | 'user' | 'assistant'; content: string; created_at: string }>,
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions' && init?.method === 'POST') return Response.json(session);
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/chat/sessions/chat%3Alive-typed/messages') {
        return new Response('typed live call should stream', { status: 500 });
      }
      if (path === '/api/chat/sessions/chat%3Alive-typed/messages/stream') {
        session = {
          ...session,
          message_count: 2,
          messages: [
            { id: 'msg:typed-user', role: 'user', content: 'can you see the screen?', created_at: '2026-06-14T00:00:01Z' },
            { id: 'msg:typed-assistant', role: 'assistant', content: 'I can see it now.', created_at: '2026-06-14T00:00:02Z' },
          ],
        };
        return new Response(
          [
            'data: {"type":"text_chunk","text":"I can see it now."}\n\n',
            `data: ${JSON.stringify({ type: 'session', session })}\n\n`,
            'data: {"type":"done"}\n\n',
          ].join(''),
          { headers: { 'Content-Type': 'text/event-stream' } },
        );
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    await screen.findByText('No chat messages yet.');
    fireEvent.click(screen.getByRole('button', { name: 'Start Call' }));
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'can you see the screen?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    await waitFor(() => {
      const streamCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages/stream') && init?.method === 'POST',
      );
      expect(streamCall?.[1]?.body).toContain('"content":"can you see the screen?"');
    });
    expect(fetchMock.mock.calls.some(
      ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && init?.method === 'POST',
    )).toBe(false);
    expect((await screen.findAllByText('I can see it now.')).length).toBeGreaterThan(0);
  });

  it('submits the composer with Enter and preserves Shift+Enter for new lines', async () => {
    let session = {
      id: 'chat:enter',
      title: 'Keyboard submit',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [] as Array<{ id: string; role: 'system' | 'user' | 'assistant'; content: string; created_at: string }>,
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/api/chat/sessions' && init?.method === 'POST') {
        return Response.json(session);
      }

      if (path === '/api/chat/sessions') {
        return Response.json({ sessions: [] });
      }

      if (path === '/api/chat/sessions/chat%3Aenter/messages') {
        session = {
          ...session,
          message_count: 2,
          messages: [
            { id: 'msg:keyboard-user', role: 'user', content: 'Send from keyboard', created_at: '2026-06-14T00:00:01Z' },
            { id: 'msg:keyboard-assistant', role: 'assistant', content: 'Keyboard response.', created_at: '2026-06-14T00:00:02Z' },
          ],
        };
        return Response.json({
          generation_status: 'queued',
          session,
          user_message: session.messages[0],
          job: {
            id: 'job:keyboard',
            module: 'chatbot',
            type: 'chat.generate',
            status: 'queued',
            resource_class: 'gpu:llm',
            created_at: '2026-06-14T00:00:01Z',
            updated_at: '2026-06-14T00:00:01Z',
            priority: 0,
          },
        });
      }

      if (path === '/api/jobs/job%3Akeyboard') {
        return Response.json({ id: 'job:keyboard', status: 'completed', input_payload: { session_id: 'chat:enter' } });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    const messageInput = await screen.findByLabelText('Message');
    fireEvent.change(messageInput, { target: { value: 'Send from keyboard' } });
    fireEvent.keyDown(messageInput, { key: 'Enter', shiftKey: true });

    expect(fetchMock.mock.calls.some(([input, init]) => requestPath(input as RequestInfo | URL) === '/api/chat/sessions' && init?.method === 'POST')).toBe(false);

    fireEvent.keyDown(messageInput, { key: 'Enter' });

    expect(await screen.findByText('Response ready: job:keyboard')).toBeInTheDocument();
    await waitFor(() => {
      const messageCall = fetchMock.mock.calls.find(
        ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && init?.method === 'POST',
      );
      expect(messageCall?.[1]?.body).toContain('"content":"Send from keyboard"');
    });
  });

  it('interrupts the active response when a newer prompt is submitted', async () => {
    const session = {
      id: 'chat:active-job',
      title: 'Active job',
      provider_id: 'openai',
      model_id: 'gpt-mini',
      message_count: 0,
      messages: [] as Array<Record<string, unknown>>,
      created_at: '2026-06-14T00:00:00Z',
      updated_at: '2026-06-14T00:00:00Z',
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/chat/sessions' && init?.method === 'POST') return Response.json(session);
      if (path === '/api/chat/sessions') return Response.json({ sessions: [] });
      if (path === '/api/chat/sessions/chat%3Aactive-job') return Response.json(session);
      if (path === '/api/chat/sessions/chat%3Aactive-job/messages') {
        const userMessage = {
          id: 'msg:active-job',
          role: 'user',
          content: 'Keep working',
          created_at: '2026-06-14T00:00:01Z',
          metadata: { generation_status: 'running' },
        };
        return Response.json({
          generation_status: 'queued',
          session: { ...session, message_count: 1, messages: [userMessage] },
          user_message: userMessage,
          job: {
            id: 'job:active-job',
            module: 'chatbot',
            type: 'chat.generate',
            status: 'running',
            resource_class: 'gpu:llm',
            priority: 0,
            created_at: '2026-06-14T00:00:01Z',
            updated_at: '2026-06-14T00:00:01Z',
          },
        });
      }
      if (path === '/api/jobs/job%3Aactive-job') {
        return Response.json({
          id: 'job:active-job',
          status: 'running',
          input_payload: { session_id: 'chat:active-job' },
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Keep working' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    const activeButton = await screen.findByRole('button', { name: 'Interrupt and send' });
    expect(activeButton).not.toBeDisabled();
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Replace this response' } });
    fireEvent.click(activeButton);
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(
        ([input, init]) => requestPath(input as RequestInfo | URL).endsWith('/messages') && init?.method === 'POST',
      )).toHaveLength(2);
    });
  });

  it('persists gateway failures in the replayable activity stream', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);

      if (path === '/api/providers') {
        return Response.json(providerPayload());
      }

      if (path === '/api/assets') {
        return Response.json(assetPayload());
      }

      if (path === '/api/chat/sessions' && init?.method === 'POST') {
        return new Response('gateway offline', { status: 503 });
      }

      if (path === '/api/chat/sessions') {
        return Response.json({ sessions: [] });
      }

      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderChatbot();

    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Is this wired?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue response' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Omnix API request failed with status 503: gateway offline');
    expect(screen.queryByText('Workspace activity')).not.toBeInTheDocument();

    await waitFor(() => {
      const persistedEvents = JSON.parse(window.localStorage.getItem('omnix.assistantWorkspace.events') ?? '[]') as Array<{
        type?: string;
        payload?: { operation?: string; statusCode?: number; details?: { submittedContent?: string } };
      }>;
      expect(persistedEvents).toHaveLength(1);
      expect(persistedEvents[0]).toMatchObject({
        type: 'operation_failed',
        payload: {
          operation: 'chat_request',
          statusCode: 503,
          details: { submittedContent: 'Is this wired?' },
        },
      });
    });
  });
});
