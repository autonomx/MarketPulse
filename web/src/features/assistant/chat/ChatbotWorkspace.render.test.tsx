import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../../app/modules';
import { omnixTheme } from '../../../design/theme';
import { ChatbotWorkspace } from './ChatbotWorkspace';
import { renderMarkdownHtml } from './markdownRenderer';
import { createTestQueryClient } from '../../../test/renderWithProviders';

// Counts message renders: a rendered message turns its markdown into HTML.
vi.mock('./markdownRenderer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./markdownRenderer')>();
  return { ...actual, renderMarkdownHtml: vi.fn(actual.renderMarkdownHtml) };
});

const session = {
  id: 'chat:typing',
  title: 'Typing chat',
  provider_id: 'openai',
  model_id: 'gpt-mini',
  message_count: 2,
  messages: [
    { id: 'msg:user', role: 'user', content: 'What is **new**?', created_at: '2026-06-14T00:00:01Z' },
    { id: 'msg:assistant', role: 'assistant', content: 'A *few* things.', created_at: '2026-06-14T00:00:02Z' },
  ],
  created_at: '2026-06-14T00:00:00Z',
  updated_at: '2026-06-14T00:00:02Z',
};

function respond(input: RequestInfo | URL): Response {
  const path = new URL(String(input), 'http://localhost').pathname;
  if (path === '/api/providers') {
    return Response.json({
      providers: [{ id: 'openai', label: 'OpenAI compatible', family: 'llm', source: 'settings', status: 'configured', capabilities: ['chat'] }],
      models: [{ id: 'gpt-mini', provider_id: 'openai', label: 'GPT Mini', capabilities: ['chat'] }],
    });
  }
  if (path === '/api/assets') return Response.json({ assets: [] });
  if (path === '/api/chat/sessions') return Response.json({ sessions: [session] });
  if (path === '/api/chat/sessions/chat%3Atyping') return Response.json(session);
  return new Response('not found', { status: 404 });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('Chat rendering', () => {
  it('does not re-render the transcript while the user types', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => respond(input)));
    const module = omnixModules.find((entry) => entry.id === 'chatbot');
    if (!module) throw new Error('Chatbot module is missing');
    render(
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <QueryClientProvider client={createTestQueryClient()}>
          <ChatbotWorkspace module={module} />
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect((await screen.findAllByText('few')).length).toBeGreaterThan(0);
    const rendersBeforeTyping = vi.mocked(renderMarkdownHtml).mock.calls.length;

    const composer = screen.getByRole('textbox', { name: 'Message' });
    for (const text of ['H', 'He', 'Hel', 'Hell', 'Hello']) fireEvent.input(composer, { target: { value: text } });

    expect(composer).toHaveValue('Hello');
    expect(vi.mocked(renderMarkdownHtml).mock.calls.length).toBe(rendersBeforeTyping);
  });
});
