import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AssistantToolSettingsPanel } from './AssistantToolSettingsPanel';
import type { AssistantToolsConfigPayload } from './assistantToolConfigClient';
import { fixture } from '../../../test/fixture';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderToolsPanel() {
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <AssistantToolSettingsPanel enabledToolCount={0} toolExecutionRows={0} onShowExecutionPanel={vi.fn()} />
    </QueryClientProvider>,
  );
}

function defaultConfig(): AssistantToolsConfigPayload {
  return {
    tools: [
      fixture({
        tool_id: 'gmail',
        enabled: false,
        connection_status: 'not_configured',
        actions: [
          { action_id: 'gmail.read', enabled: true, approval_policy: 'allow_automatic' },
          { action_id: 'gmail.draft', enabled: true, approval_policy: 'ask_sensitive' },
          { action_id: 'gmail.send', enabled: true, approval_policy: 'always_ask' },
          { action_id: 'gmail.delete', enabled: false, approval_policy: 'always_ask' },
          { action_id: 'gmail.attachments', enabled: true, approval_policy: 'ask_sensitive' },
        ],
      }),
    ],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AssistantToolSettingsPanel', () => {
  it('opens a dedicated configure view and requires provider login before connecting a tool', async () => {
    const savedPayloads: AssistantToolsConfigPayload[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/assistant/tools/connect/gmail') {
        return Response.json({
          tool_id: 'gmail',
          provider: 'Google',
          configured: false,
          auth_url: null,
          redirect_uri: 'http://localhost:8000/api/assistant/tools/connect/google/callback',
          message: 'Google OAuth is not configured.',
        });
      }
      if (url.pathname === '/api/assistant/tools/connect/gmail/oauth-client' && init?.method === 'POST') {
        const payload = JSON.parse(String(init.body)) as { client_id: string; client_secret: string };
        return Response.json({
          tool_id: 'gmail',
          provider: 'Google',
          configured: false,
          auth_url: null,
          redirect_uri: 'http://localhost:8000/api/assistant/tools/connect/google/callback',
          message: `Saved ${payload.client_id} with ${payload.client_secret.length} secret characters.`,
        });
      }
      if (url.pathname === '/api/assistant/tools/config' && init?.method === 'POST') {
        const payload = JSON.parse(String(init.body)) as AssistantToolsConfigPayload;
        savedPayloads.push(payload);
        return Response.json(payload);
      }
      if (url.pathname === '/api/assistant/tools/config') {
        return Response.json(defaultConfig());
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderToolsPanel();

    const gmailCard = await screen.findByRole('heading', { name: 'Gmail' });
    fireEvent.click(gmailCard.closest('article')?.querySelector('button') as HTMLButtonElement);

    expect(await screen.findByRole('button', { name: 'Back to tools' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Connect Google account' })).toBeInTheDocument();
    expect(screen.getByText('No account connected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Connect real account' }));

    expect(await screen.findByText('Google OAuth is not configured.')).toBeInTheDocument();
    expect(savedPayloads).toEqual([]);

    fireEvent.change(screen.getByLabelText('Google OAuth client ID'), { target: { value: 'client-123' } });
    fireEvent.change(screen.getByLabelText('Google OAuth client secret'), { target: { value: 'secret-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save OAuth app and connect' }));

    expect(await screen.findByText('Saved client-123 with 10 secret characters.')).toBeInTheDocument();
  });
});
