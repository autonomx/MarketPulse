import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgLorePanel } from './RpgLorePanel';

describe('RpgLorePanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows the complete lore navigation and discovered dossiers', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'campaign:test');
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      session_id: 'campaign:test',
      canon_revision: 2,
      content_hash: 'sha256:test',
      categories: [],
      visible_count: 0,
      hidden_count: 2,
      dossiers: {
        characters: [{ id: 'npc:bran', kind: 'npc', name: 'Bran', status: 'partially_known', appearance: 'A watchful innkeeper.' }],
        locations: [{ id: 'location:tavern', kind: 'location', name: 'Rusty Flagon Tavern', status: 'partially_known', sensory_profile: 'Rain and hearth smoke.' }],
        factions: [{ id: 'faction:watch', kind: 'faction', name: 'Northern Watch', status: 'partially_known', public_goal: 'Keep the roads safe.' }],
      },
      generation: { status: 'ready', launch_ready: true, percent: 100, jobs: [] },
      storage: {
        mode: 'postgresql_authority',
        persisted: true,
        revision: 2,
        current_location: { id: 'location:tavern', name: 'Rusty Flagon Tavern' },
        generated_current_location: true,
      },
    }), { status: 200 })));

    render(<RpgLorePanel />);

    await waitFor(() => expect(screen.getByRole('region', { name: 'Campaign Bible summary' })).toBeInTheDocument());
    expect(screen.getAllByText('PostgreSQL authority')).toHaveLength(2);
    expect(screen.getByText('Lore generated and stored')).toBeInTheDocument();

    for (const category of ['World Lore', 'Areas', 'Points of Interest', 'Characters', 'Races', 'Classes', 'Monsters', 'Items', 'Spells', 'Feats', 'Quests', 'Discoveries']) {
      expect(screen.getByRole('region', { name: category })).toBeInTheDocument();
    }

    expect(screen.getByText('Bran')).toBeInTheDocument();
    expect(screen.getAllByText('Rusty Flagon Tavern').length).toBeGreaterThan(0);
    expect(screen.getByText('Northern Watch')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Rusty Flagon Tavern/i }));
    expect(screen.getAllByText('Rain and hearth smoke.')).toHaveLength(2);
  });

  it('regenerates a selected page with an optional user direction', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'campaign:test');
    const summary = {
      document_id: 'lore:cosmology',
      title: 'Cosmology',
      topic_id: 'cosmology',
      category: 'World Lore',
      summary_120: 'Two moons cross the sky.',
      summary_500: 'Two moons cross the sky above Aurelia.',
      keywords: ['moons'],
      visibility: 'public',
      status: 'public_at_campaign_start',
      canon_revision: 1,
    };
    const lore = {
      ok: true,
      session_id: 'campaign:test',
      canon_revision: 1,
      content_hash: 'old-hash',
      categories: [{ label: 'World Lore', documents: [summary] }],
      visible_count: 1,
      hidden_count: 0,
      dossiers: { characters: [], locations: [], factions: [] },
      generation: { status: 'ready', jobs: [] },
      storage: { mode: 'postgresql_authority', persisted: true, revision: 1 },
    };
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({
          ok: true,
          document: { ...summary, canon_revision: 2, full_text: 'Rich regenerated cosmology in descriptive paragraphs.' },
          lore: { ...lore, canon_revision: 2, content_hash: 'new-hash' },
        }), { status: 200 });
      }
      if (url.includes('/lore/document')) {
        return new Response(JSON.stringify({
          document: { ...summary, full_text: 'Two moons cross the sky above Aurelia.' },
        }), { status: 200 });
      }
      return new Response(JSON.stringify(lore), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<RpgLorePanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Cosmology/ }));
    const direction = await screen.findByLabelText('Optional generation direction');
    fireEvent.change(direction, { target: { value: 'Focus on rituals beneath both moons.' } });
    const generate = await screen.findByRole('button', { name: 'Regenerate lore' });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    await screen.findByText('Rich regenerated cosmology in descriptive paragraphs.');
    const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'POST');
    expect(postCall?.[0]).toBe('/api/rpg/sessions/campaign%3Atest/lore/regenerate');
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      document_id: 'lore:cosmology',
      direction: 'Focus on rituals beneath both moons.',
    });
  });

  it('creates runtime rules and matching lore from the overview', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'campaign:test');
    const baseLore = {
      ok: true,
      session_id: 'campaign:test',
      canon_revision: 4,
      content_hash: 'old-hash',
      categories: [],
      visible_count: 0,
      hidden_count: 0,
      dossiers: { characters: [], locations: [], factions: [] },
      generation: { status: 'ready', jobs: [] },
      storage: { mode: 'postgresql_authority', persisted: true, revision: 4 },
    };
    const document = {
      document_id: 'lore:runtime:creature:echo-wolf',
      title: 'Echo Wolf',
      topic_id: 'monsters',
      category: 'Monsters',
      summary_120: 'A spectral wolf hunts memories.',
      summary_500: 'A spectral wolf hunts memories and recoils from bells.',
      full_text: 'A spectral wolf hunts memories.\n\nClear bronze bells force it into solid form.\n\nHunters mark its paths with chimes.',
      keywords: ['Echo Wolf'],
      visibility: 'public',
      status: 'learned',
      canon_revision: 5,
    };
    const fetchMock = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({
          ok: true,
          document,
          definition: { definition_id: 'creature:echo-wolf', definition_revision: 1 },
          lore: {
            ...baseLore,
            canon_revision: 5,
            content_hash: 'new-hash',
            categories: [{ label: 'Monsters', documents: [document] }],
            visible_count: 1,
          },
        }), { status: 200 });
      }
      return new Response(JSON.stringify(baseLore), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<RpgLorePanel />);
    const materializer = await screen.findByRole('region', { name: 'Materialize runtime lore' });
    fireEvent.change(screen.getByLabelText('Name', { selector: '#rpg-runtime-lore-name' }), {
      target: { value: 'Echo Wolf' },
    });
    fireEvent.change(screen.getByLabelText('Optional direction', { selector: '#rpg-runtime-lore-direction' }), {
      target: { value: 'Make clear bronze bells disrupt its body.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create rules & lore' }));

    expect(materializer).toBeInTheDocument();
    await screen.findByText(/Clear bronze bells force it into solid form/);
    const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === 'POST');
    expect(postCall?.[0]).toBe('/api/rpg/sessions/campaign%3Atest/lore/materialize');
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      kind: 'creature',
      name: 'Echo Wolf',
      direction: 'Make clear bronze bells disrupt its body.',
      document_id: '',
    });
  });

  it('clears stale page content while the newly selected topic loads', async () => {
    window.localStorage.setItem('omnix:rpg:selected-session-id', 'campaign:test');
    const makeSummary = (documentId: string, title: string) => ({
      document_id: documentId,
      title,
      topic_id: 'monsters',
      category: 'Monsters',
      summary_120: `${title} summary.`,
      summary_500: `${title} summary.`,
      keywords: [title],
      visibility: 'public',
      status: 'public_at_campaign_start',
      canon_revision: 1,
    });
    const wolf = makeSummary('lore:echo-wolf', 'Echo Wolf');
    const wight = makeSummary('lore:glass-wight', 'Glass Wight');
    const lore = {
      ok: true,
      session_id: 'campaign:test',
      canon_revision: 1,
      content_hash: 'hash',
      categories: [{ label: 'Monsters', documents: [wolf, wight] }],
      visible_count: 2,
      hidden_count: 0,
      dossiers: { characters: [], locations: [], factions: [] },
      generation: { status: 'ready', jobs: [] },
      storage: { mode: 'postgresql_authority', persisted: true, revision: 1 },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('lore%3Aecho-wolf')) {
        return new Response(JSON.stringify({
          document: { ...wolf, full_text: 'Echo Wolf target details.' },
        }), { status: 200 });
      }
      if (url.includes('lore%3Aglass-wight')) {
        return new Promise<Response>(() => {});
      }
      return new Response(JSON.stringify(lore), { status: 200 });
    }));

    render(<RpgLorePanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Echo Wolf/ }));
    await screen.findByText('Echo Wolf target details.');

    fireEvent.click(screen.getByRole('button', { name: /Glass Wight/ }));

    expect(screen.queryByText('Echo Wolf target details.')).not.toBeInTheDocument();
    expect(screen.getByText('Loading selected lore page…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Regenerate lore' })).toBeDisabled();
  });
});
