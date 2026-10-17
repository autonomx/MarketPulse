import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringTopic } from './api/rpgWorldAuthoringClient';
import { RpgWorldTopicEditor } from './RpgWorldTopicEditor';
import { createTestQueryClient } from '../../test/renderWithProviders';

const generatedTopic: RpgAuthoringTopic = {
  topic_id: 'classes',
  draft_revision: 3,
  source: 'ai',
  status: 'ready',
  content: { entities: [] },
  directives: {},
  dependency_hashes: {},
  input_hash: 'sha256:input',
  content_hash: 'sha256:classes',
  provenance: {},
  updated_at: '2026-07-20T00:00:00Z',
};

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderEditor(topic: RpgAuthoringTopic) {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <RpgWorldTopicEditor topic={topic} worldId="world:aurelia" />
    </QueryClientProvider>,
  );
}

describe('RpgWorldTopicEditor regeneration', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return response({
        ok: true,
        worker_started: true,
        run: {
          run_id: 'run:classes-regeneration',
          world_id: 'world:aurelia',
          draft_revision: 3,
          status: 'running',
          graph: {},
          context: {},
          settings: {},
          plan: {},
          progress: { percent: 0 },
          error: {},
          created_at: '2026-07-20T00:00:00Z',
          updated_at: '2026-07-20T00:00:00Z',
        },
      });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('regenerates the current page with a scoped direction', async () => {
    renderEditor(generatedTopic);

    fireEvent.click(screen.getByText('Regenerate this page'));
    fireEvent.change(screen.getByLabelText('Regeneration direction for classes'), {
      target: { value: 'Make class identities more distinct.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate Topic' }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toContain('/api/rpg/worlds/world%3Aaurelia/generation');
    expect(JSON.parse(String(requests[0].init?.body))).toEqual({
      scope: { mode: 'selected', topic_ids: ['classes'] },
      strategy: 'force',
      replace_locked: false,
      directives: { classes: { direction: 'Make class identities more distinct.' } },
      entity_manifest: {},
    });
    expect(await screen.findByText('Regeneration started: run:classes-regeneration')).toBeInTheDocument();
  });

  it('requires explicit approval before replacing a manual or locked topic', () => {
    renderEditor({
      ...generatedTopic,
      source: 'manual',
      provenance: { authoring: { generation_lock: true } },
    });

    fireEvent.click(screen.getByText('Regenerate this page'));
    const regenerate = screen.getByRole('button', { name: 'Regenerate Topic' });
    expect(regenerate).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Replace this protected/ }));
    expect(regenerate).toBeEnabled();
  });
});
