import { afterEach, describe, expect, it, vi } from 'vitest';
import { stubGateway } from '../../test/renderWithProviders';
import { createPodcastScriptSession } from './podcastScriptSession';

afterEach(() => {
  vi.unstubAllGlobals();
});

const session = { id: 'chat:podcast', title: 'Script', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', messages: [] };

describe('createPodcastScriptSession', () => {
  it("drafts with the podcast provider and model from settings when the request names none", async () => {
    const created: Array<Record<string, unknown>> = [];
    stubGateway({
      'GET /api/settings/profile': () => ({
        settings: { settings_control_center: { podcast: { providerId: 'openai', modelId: 'gpt-script' } } },
      }),
      'POST /api/chat/sessions': ({ body }) => {
        created.push(body as Record<string, unknown>);
        return session;
      },
    });

    await expect(createPodcastScriptSession({ title: 'Script' })).resolves.toMatchObject({ id: 'chat:podcast' });
    expect(created).toEqual([expect.objectContaining({ title: 'Script', provider_id: 'openai', model_id: 'gpt-script' })]);
  });

  it('keeps the request when settings are refused', async () => {
    const created: Array<Record<string, unknown>> = [];
    stubGateway({
      'GET /api/settings/profile': () => new Response('forbidden', { status: 403 }),
      'POST /api/chat/sessions': ({ body }) => {
        created.push(body as Record<string, unknown>);
        return session;
      },
    });

    await createPodcastScriptSession({ title: 'Script', provider_id: 'local', model_id: 'small' });
    expect(created).toEqual([expect.objectContaining({ title: 'Script', provider_id: 'local', model_id: 'small' })]);
  });
});
