import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldImagesPanel } from './RpgWorldImagesPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

const targetResponse = {
  ok: true,
  world: {
    id: 'world:aurelia',
    title: 'Aurelia',
    description: '',
    status: 'draft',
    source_mode: 'ai',
    genre: 'fantasy',
    tone: 'heroic',
    seed: 4,
    draft_revision: 1,
    metadata: {},
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
  },
  targets: [
    {
      world_id: 'world:aurelia',
      target_id: 'world:cover',
      target_type: 'world',
      entity_id: 'world:aurelia',
      role: 'cover',
      source_content_hash: 'sha256:cover',
      status: 'missing',
      review_state: 'pending',
      suggested_prompt: 'A cinematic cover for Aurelia',
      active_asset_id: null,
      latest_job_id: 'job:old',
      metadata: { entity_name: 'Aurelia cover' },
      attempts: [
        {
          job_id: 'job:old',
          prompt: 'An older cover',
          source_content_hash: 'sha256:cover',
          status: 'completed',
          asset_id: 'image:old-cover',
          error: {},
          created_at: '2026-07-20T00:00:00Z',
          updated_at: '2026-07-20T00:00:00Z',
        },
      ],
      created_at: '2026-07-20T00:00:00Z',
      updated_at: '2026-07-20T00:00:00Z',
    },
  ],
};

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPanel() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldImagesPanel worldId="world:aurelia" />
    </QueryClientProvider>,
  );
}

describe('RpgWorldImagesPanel', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST') {
        return jsonResponse({ ok: true, world_id: 'world:aurelia', jobs: [{ job_id: 'job:new' }] });
      }
      if (init?.method === 'PATCH') return jsonResponse(targetResponse);
      return jsonResponse(targetResponse);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('bulk selects missing targets and queues independent image jobs', async () => {
    renderPanel();

    expect(await screen.findByText('Aurelia cover')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '0 of 1 images ready' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('1 awaiting generation')).toBeInTheDocument();
    expect(screen.getByLabelText('Image category')).toHaveValue('all');
    expect(screen.getByRole('option', { name: 'World' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Select Missing & Stale' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate Selected (1)' })).toBeEnabled());
    fireEvent.click(screen.getByText('Advanced generation settings'));
    fireEvent.change(screen.getByLabelText('Image provider route'), {
      target: { value: 'image:flux-klein' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Selected (1)' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'POST')).toBe(true));
    const generated = requests.find((request) => request.init?.method === 'POST');
    expect(generated?.url).toContain('/api/rpg/worlds/world%3Aaurelia/image-generation');
    expect(JSON.parse(String(generated?.init?.body))).toMatchObject({
      target_ids: ['world:cover'],
      provider_id: 'image:flux-klein',
      style: 'concept art',
      width: 768,
      height: 768,
    });
    expect(await screen.findByText('Queued 1 image job.')).toBeInTheDocument();
  });

  it('can make a previous image active without regenerating canon', async () => {
    renderPanel();

    expect(await screen.findByText('Aurelia cover')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Previous images (1)'));
    fireEvent.click(screen.getByRole('button', { name: 'Make active' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'PATCH')).toBe(true));
    const reviewed = requests.find((request) => request.init?.method === 'PATCH');
    expect(reviewed?.url).toContain('/image-targets/world%3Acover');
    expect(JSON.parse(String(reviewed?.init?.body))).toEqual({
      review_state: 'approved',
      active_asset_id: 'image:old-cover',
    });
  });

  it('keeps an edited image prompt when regenerating', async () => {
    renderPanel();

    const prompt = await screen.findByLabelText('Prompt for Aurelia cover');
    fireEvent.change(prompt, { target: { value: 'A hand-painted moonlit cover for Aurelia' } });
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    await waitFor(() => expect(requests.some((request) => request.url.includes('/regenerate'))).toBe(true));
    const regenerated = requests.find((request) => request.url.includes('/regenerate'));
    expect(JSON.parse(String(regenerated?.init?.body))).toMatchObject({
      prompt: 'A hand-painted moonlit cover for Aurelia',
      no_cache: true,
    });
  });

  it('regenerates selected or all suggested prompts without queueing images', async () => {
    renderPanel();

    expect(await screen.findByText('Aurelia cover')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate All Prompts' }));
    await waitFor(() => expect(requests.some((request) => request.url.includes('/image-prompts/regenerate'))).toBe(true));
    const allPrompts = requests.find((request) => request.url.includes('/image-prompts/regenerate'));
    expect(JSON.parse(String(allPrompts?.init?.body))).toEqual({ target_ids: ['world:cover'] });

    fireEvent.click(screen.getByLabelText('Select'));
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate Selected Prompts (1)' }));
    await waitFor(() => expect(requests.filter((request) => request.url.includes('/image-prompts/regenerate'))).toHaveLength(2));
  });

  it('regenerates every image with no-cache enabled', async () => {
    renderPanel();

    expect(await screen.findByText('Aurelia cover')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate All Images' }));
    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/image-generation'))).toBe(true));
    const regenerated = requests.find((request) => request.url.endsWith('/image-generation'));
    expect(JSON.parse(String(regenerated?.init?.body))).toMatchObject({
      target_ids: ['world:cover'],
      prompts: { 'world:cover': 'A cinematic cover for Aurelia' },
      no_cache: true,
    });
  });
});
