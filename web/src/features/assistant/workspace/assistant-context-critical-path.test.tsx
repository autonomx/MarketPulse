import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { resetFetchPipelineForTests } from '../../../api/fetchPipeline';
import { openStream } from '../../../api/transport';
import { AssistantContextControls } from './assistant-context-controls';
import { sendChatWithAssistantContext } from './assistant-context-chat';
import { initializeAssistantContextController } from './assistant-context-controller';
import { assistantContextStore } from './assistant-context-store';

let dispose: (() => void) | null = null;

afterEach(() => {
  dispose?.();
  dispose = null;
  cleanup();
  resetFetchPipelineForTests();
  assistantContextStore.resetForTests();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

/** Opens Chat's message stream the way Chat's voice turns do. */
function openChatStream(sessionId: string, body: Record<string, unknown>): Promise<Response> {
  return sendChatWithAssistantContext(sessionId, body, (route, payload) => openStream(
    route === 'context'
      ? `/api/assistant/context/chat/sessions/${sessionId}/messages/stream`
      : `/api/chat/sessions/${sessionId}/messages/stream`,
    { body: payload },
  ));
}

function profileResponse(researchDefaultMode = 'disabled'): Response {
  return Response.json({ settings: { settings_control_center: { assistant: { researchDefaultMode } } } });
}

describe('assistant context live-chat critical path', () => {
  it('opens a plus menu that chooses the research modes used by chat requests', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => profileResponse()));
    dispose = initializeAssistantContextController();
    render(<AssistantContextControls />);

    const addButton = screen.getByRole('button', { name: 'Add tools' });
    const menu = screen.getByRole('menu', { hidden: true });
    expect(menu).not.toBeVisible();
    expect(within(menu).getAllByRole('menuitemradio', { hidden: true })).toHaveLength(3);
    for (const name of [/Agent mode/, /Desktop/, /Local folder/]) {
      expect(within(menu).getByRole('menuitemcheckbox', { name, hidden: true })).toBeInTheDocument();
    }
    const fileInput = screen.getByLabelText<HTMLInputElement>('Choose photos and files from computer');
    expect(fileInput).toHaveAttribute('accept', expect.stringContaining('image/png'));
    expect(fileInput).toHaveAttribute('accept', expect.stringContaining('.txt'));

    const selectedImage = vi.fn();
    window.addEventListener('omnix:chat-image-selected', selectedImage);
    const imageFile = new File(['image-data'], 'clipboard.png', { type: 'image/png' });
    fireEvent.change(fileInput, { target: { files: [imageFile] } });
    await vi.waitFor(() => expect(selectedImage).toHaveBeenCalledTimes(1));
    expect((selectedImage.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ mimeType: 'image/png', size: imageFile.size });
    window.removeEventListener('omnix:chat-image-selected', selectedImage);

    const selectedTextFile = vi.fn();
    window.addEventListener('omnix:chat-text-file-selected', selectedTextFile);
    const textFile = new File(['# notes'], 'notes.md', { type: 'text/markdown' });
    Object.defineProperty(textFile, 'text', { configurable: true, value: () => Promise.resolve('# notes') });
    fireEvent.change(fileInput, { target: { files: [textFile] } });
    await vi.waitFor(() => expect(selectedTextFile).toHaveBeenCalledTimes(1));
    expect((selectedTextFile.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ filename: 'notes.md', mimeType: 'text/markdown', text: '# notes' });
    window.removeEventListener('omnix:chat-text-file-selected', selectedTextFile);

    fireEvent.click(addButton);
    expect(menu).toBeVisible();
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: /Quick search/ }));

    // The menu stays open after a research choice, so Desktop and Local folder can be toggled too.
    expect(menu).toBeVisible();
    expect(assistantContextStore.getState().researchMode).toBe('quick');
    expect(screen.getByText('Quick search', { selector: '.assistant-context-tool-summary' })).toBeInTheDocument();

    fireEvent.click(within(menu).getByRole('menuitemcheckbox', { name: /Agent mode/ }));
    expect(window.localStorage.getItem('omnix.chat.mode')).toBe('agent');
    expect(within(menu).getByRole('menuitemcheckbox', { name: /Agent mode/ })).toHaveAttribute('aria-checked', 'true');
    expect(document.querySelector('.assistant-context-tool-summary')).toHaveTextContent('Agent mode');
  });

  it('offers the Quick Search fallback only when Deep Research is withheld', () => {
    assistantContextStore.update({
      researchMode: 'deep',
      availability: { disabled: true, quick: true, deep: false, hermes_planner: false },
      releaseMessage: 'Quick Search is available. Deep Research is not released for this session.',
    });
    render(<AssistantContextControls />);

    const fallback = screen.getByRole('checkbox', { name: 'Allow Quick Search fallback' });
    expect(fallback).toBeVisible();
    expect(within(screen.getByRole('menu', { hidden: true })).getByRole('menuitemradio', { name: /Deep research/, hidden: true })).toBeDisabled();
    fireEvent.click(fallback);
    expect(assistantContextStore.getState().allowDowngrade).toBe(true);
    expect(screen.getByText('Quick Search is available. Deep Research is not released for this session.')).toBeInTheDocument();
  });

  it('persists Agent mode and forces streamed requests through the Pi route', async () => {
    window.localStorage.setItem('omnix.chat.mode', 'agent');
    assistantContextStore.resetForTests();
    const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), window.location.origin).pathname;
      if (path === '/api/settings/profile') return profileResponse();
      if (init?.body) requests.push({ path, body: JSON.parse(String(init.body)) as Record<string, unknown> });
      return new Response('data: {"type":"done"}\n\n', { status: 200 });
    }));
    dispose = initializeAssistantContextController();
    render(<AssistantContextControls />);

    expect(screen.getByRole('menuitemcheckbox', { name: /Agent mode/, hidden: true })).toHaveAttribute('aria-checked', 'true');
    expect(document.querySelector('.assistant-context-tool-summary')).toHaveTextContent('Agent mode');

    await openChatStream('s1', { content: 'fix the layout' });

    expect(requests).toContainEqual({
      path: '/api/assistant/context/chat/sessions/s1/messages/stream',
      body: expect.objectContaining({ content: 'fix the layout', agent_mode: true, dry_run: false }),
    });
  });

  it('opens the chat response before deferred research-mode persistence completes', async () => {
    let resolvePersistence: (response: Response) => void = () => undefined;
    const persistenceResponse = new Promise<Response>((resolve) => {
      resolvePersistence = resolve;
    });
    const requestPaths: string[] = [];
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL): Promise<Response> => {
      const path = new URL(String(input), window.location.origin).pathname;
      requestPaths.push(path);
      if (path === '/api/settings/profile') return Promise.resolve(profileResponse());
      if (path === '/api/chat/sessions/s1/messages/stream') {
        return Promise.resolve(new Response('data: {"type":"done"}\n\n', { status: 200, headers: { 'content-type': 'text/event-stream' } }));
      }
      if (path === '/api/chat/sessions/s1/research-mode') return persistenceResponse;
      return Promise.resolve(new Response(null, { status: 404 }));
    }));
    dispose = initializeAssistantContextController();
    await Promise.resolve();

    const response = await Promise.race([
      openChatStream('s1', { content: 'hello' }),
      new Promise<never>((_, reject) => {
        window.setTimeout(() => reject(new Error('chat response was blocked by persistence')), 100);
      }),
    ]);

    expect(response.status).toBe(200);
    await vi.waitFor(() => expect(requestPaths).toContain('/api/chat/sessions/s1/research-mode'));
    expect(requestPaths.indexOf('/api/chat/sessions/s1/messages/stream')).toBeLessThan(
      requestPaths.indexOf('/api/chat/sessions/s1/research-mode'),
    );
    resolvePersistence(new Response(null, { status: 200 }));
  });
});
