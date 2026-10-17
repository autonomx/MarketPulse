import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldProfilePreview } from './RpgWorldProfilePreview';
import { createTestQueryClient } from '../../test/renderWithProviders';

const profile = {
  profile_id: 'cyberpunk',
  version: 2,
  display_name: 'Cyberpunk',
  domains: [
    {
      domain_id: 'actors',
      title: 'Actors and NPCs',
      entity_kind: 'actor',
      dependencies: ['groups', 'places'],
      required_before_launch: true,
      fields: [],
      target_range: { quick: [4, 6], standard: [8, 12], epic: [15, 25] },
      semantic_roles: ['initial_actors'],
      generation_guidance: {
        presentation: {
          page_kind: 'collection',
          card_variant: 'npcs',
          image_role: 'portrait',
          group: 'world',
        },
      },
    },
    {
      domain_id: 'places',
      title: 'Places and Points of Interest',
      entity_kind: 'place',
      dependencies: ['regions'],
      required_before_launch: true,
      fields: [],
      target_range: { quick: [3, 5], standard: [7, 10], epic: [14, 22] },
      semantic_roles: ['starting_context'],
      generation_guidance: {
        presentation: {
          page_kind: 'collection',
          card_variant: 'locations',
          image_role: 'scene',
          group: 'world',
        },
      },
    },
  ],
};

function response(status = 'review_required', revision = 1) {
  return {
    ok: true,
    review: {
      world_id: 'world:cyber',
      status,
      profile_revision: revision,
      profile_hash: `sha256:profile-${revision}`,
      approved_profile_hash: status === 'approved' ? `sha256:profile-${revision}` : '',
      approved_at: status === 'approved' ? '2026-07-24T00:00:00Z' : null,
      approved_by: status === 'approved' ? 'local-author' : null,
      profile,
      requested_genre: 'cyberpunk',
      normalized_genre: 'cyberpunk',
      source: 'registry',
      generated: false,
      route: {},
      review_findings: [],
      error: {},
    },
  };
}

function renderPreview(onApprovalChange = vi.fn()) {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldProfilePreview
        onApprovalChange={onApprovalChange}
        worldId="world:cyber"
      />
    </QueryClientProvider>,
  );
  return onApprovalChange;
}

describe('RpgWorldProfilePreview', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows card and image mappings before content generation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(response()), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    renderPreview();

    expect(await screen.findByDisplayValue('Actors and NPCs')).toBeInTheDocument();
    expect(screen.getByLabelText('Presentation type for actors')).toHaveValue('collection');
    expect(screen.getByLabelText('Image role for actors')).toHaveValue('portrait');
    expect(screen.getByLabelText('Presentation type for places')).toHaveValue('collection');
    expect(screen.getByLabelText('Image role for places')).toHaveValue('scene');
    expect(screen.getByLabelText('Minimum standard count for actors')).toHaveValue(8);
    expect(screen.getByLabelText('Maximum standard count for actors')).toHaveValue(12);
  });

  it('saves an edited profile revision before approval', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let readCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'PATCH') {
        return new Response(JSON.stringify(response('review_required', 2)), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      readCount += 1;
      return new Response(JSON.stringify(response('review_required', readCount > 1 ? 2 : 1)), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    renderPreview();
    const title = await screen.findByLabelText('Title for actors');
    fireEvent.change(title, { target: { value: 'Operatives and NPCs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Profile Draft' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'PATCH')).toBe(true));
    const patch = requests.find((request) => request.init?.method === 'PATCH');
    expect(JSON.parse(String(patch?.init?.body))).toMatchObject({
      expected_profile_revision: 1,
      profile: {
        domains: [
          { domain_id: 'actors', title: 'Operatives and NPCs' },
          { domain_id: 'places' },
        ],
      },
    });
    expect(await screen.findByText(/Profile revision 2 saved and requires approval/)).toBeInTheDocument();
  });

  it('approves the exact current profile revision', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let approved = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith('/approve')) {
        approved = true;
      }
      return new Response(JSON.stringify(response(approved ? 'approved' : 'review_required')), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const onApprovalChange = renderPreview();
    const approve = await screen.findByRole('button', { name: 'Approve Profile' });
    fireEvent.click(approve);

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/approve'))).toBe(true));
    const request = requests.find((candidate) => candidate.url.endsWith('/approve'));
    expect(JSON.parse(String(request?.init?.body))).toEqual({ expected_profile_revision: 1 });
    expect(await screen.findByText(/World content generation is unlocked/)).toBeInTheDocument();
    await waitFor(() => expect(onApprovalChange).toHaveBeenCalledWith(true));
    const toggle = screen.getByRole('button', { name: 'Show details' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById(toggle.getAttribute('aria-controls') ?? '')).toHaveAttribute('hidden');
  });

  it('retries a terminal profile validation failure from the preview', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const failed = {
      ...response('validation_failed'),
      review: {
        ...response('validation_failed').review,
        profile: {},
        error: { code: 'world_profile_generation_failed' },
      },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      const result = String(input).endsWith('/retry') ? response('generating') : failed;
      return new Response(JSON.stringify(result), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    renderPreview();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry Profile Generation' }));

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/retry'))).toBe(true));
    expect(await screen.findByText(/Profile generation restarted/)).toBeInTheDocument();
  });

  it('allows an approved profile preview to be expanded after loading collapsed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(response('approved')), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    renderPreview();

    const toggle = await screen.findByRole('button', { name: 'Show details' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById(toggle.getAttribute('aria-controls') ?? '')).toHaveAttribute('hidden');

    fireEvent.click(toggle);

    expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Title for actors')).toBeInTheDocument();
  });
});
