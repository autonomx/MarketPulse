import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { StorytellerWorkspace } from './StorytellerWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderStoryteller() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'storyteller');
  if (!module) throw new Error('Storyteller module is missing');
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <StorytellerWorkspace module={module} />
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
        id: 'lmstudio',
        label: 'LM Studio',
        family: 'llm',
        source: 'settings',
        status: 'configured',
        capabilities: ['chat', 'completion'],
      },
    ],
    models: [],
  };
}

function storyJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job:story',
    module: 'storyteller',
    type: 'story.generate',
    status: 'completed',
    resource_class: 'gpu:llm',
    created_at: '2026-06-14T00:00:01Z',
    updated_at: '2026-06-14T00:00:02Z',
    priority: 0,
    progress: { current: 3, total: 3 },
    input_payload: {
      title: 'The Glass Orchard',
      premise: 'A city grows fruit made of memory.',
      provider_id: 'lmstudio',
      action: 'draft',
      ...(typeof overrides.input_payload === 'object' && overrides.input_payload
        ? (overrides.input_payload as Record<string, unknown>)
        : {}),
    },
    output_refs: [
      {
        kind: 'text',
        content: [
          'The orchard rang like crystal at sunset.',
          '',
          'Each branch held a memory bright enough to bruise the dark, and Mira knew the city would wake hungry for forgotten names.',
        ].join('\n'),
      },
    ],
    ...overrides,
  };
}

function assetPayload() {
  return {
    assets: [
      {
        id: 'asset:story',
        module: 'storyteller',
        type: 'story',
        mime_type: 'text/markdown',
        file_name: 'the-glass-orchard.md',
        created_at: '2026-06-14T00:00:00Z',
      },
    ],
  };
}

function assetContentPayload() {
  return {
    asset: assetPayload().assets[0],
    content: '# The Glass Orchard\n\nAsset branches chimed softly when Mira opened the gate.',
    encoding: 'utf-8',
    size_bytes: 68,
    truncated: false,
  };
}

function stubStoryApi(jobs: unknown[] = [storyJob()], assets: ReturnType<typeof assetPayload> = assetPayload()) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path === '/api/providers') return Response.json(providerPayload());
    if (path === '/api/jobs') return Response.json({ jobs });
    if (path === '/api/assets') return Response.json(assets);
    if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
    return new Response('not found', { status: 404 });
  }));
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('StorytellerWorkspace', () => {
  it('renders completed story output as the main manuscript workspace', async () => {
    stubStoryApi([storyJob()]);
    renderStoryteller();
    expect(await screen.findByRole('complementary', { name: 'Story library' })).toBeInTheDocument();
    const manuscript = screen.getByRole('region', { name: 'Story manuscript' });
    expect(screen.getByRole('complementary', { name: 'Story controls' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Story outline' })).toBeInTheDocument();
    expect((await within(manuscript).findAllByText('The orchard rang like crystal at sunset.')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /Continue Story/ })).toBeInTheDocument();
  });

  it('shows an empty manuscript state before the first story is generated', async () => {
    stubStoryApi([]);
    renderStoryteller();
    expect((await screen.findAllByText(/Start with a premise/)).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Save story' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Export Markdown' })).toBeDisabled();
  });

  it('switches Story library sections and starts a new draft from the sidebar', async () => {
    stubStoryApi([storyJob()]);
    renderStoryteller();
    const library = await screen.findByRole('complementary', { name: 'Story library' });
    fireEvent.click(within(library).getByRole('button', { name: 'Characters' }));
    expect(await within(library).findByText('Characters not created yet')).toBeInTheDocument();
    fireEvent.click(within(library).getByRole('button', { name: 'World Notes' }));
    expect(await within(library).findByText('World notes not created yet')).toBeInTheDocument();
    fireEvent.click(within(library).getByRole('button', { name: 'New story draft' }));
    expect(await screen.findByText('New draft ready. Add a premise to begin.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save story' })).toBeDisabled();
  });

  it('generates stories through the shared jobs API from the redesigned controls', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') return Response.json(storyJob());
      if (path === '/api/jobs') return Response.json({ jobs: [] });
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();

    expect(await screen.findByText('LM Studio')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'lmstudio' } });
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'The Glass Orchard' } });
    fireEvent.change(screen.getByLabelText(/Premise/), { target: { value: 'A city grows fruit made of memory.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue story' }));

    expect(await screen.findByText('Story generated: job:story')).toBeInTheDocument();
    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as {
        input_payload?: Record<string, unknown>;
        module?: string;
        type?: string;
      };
      expect(body.module).toBe('storyteller');
      expect(body.type).toBe('story.generate');
      expect(body.input_payload?.prompt_template_id).toBe('storyteller.draft.v1');
      expect(body.input_payload?.action).toBe('draft');
      expect(body.input_payload?.interaction_mode).toBe('writing');
      expect(body.input_payload?.source_text).toBeNull();
    });
  });

  it('loads readable story asset content from the Story library', async () => {
    stubStoryApi([]);
    renderStoryteller();
    const library = await screen.findByRole('complementary', { name: 'Story library' });
    fireEvent.click(await within(library).findByRole('button', { name: /the glass orchard/ }));
    const manuscript = await screen.findByRole('region', { name: 'Story manuscript' });
    expect(await within(manuscript).findByText('Asset branches chimed softly when Mira opened the gate.')).toBeInTheDocument();
  });

  it('moves the selected story to Trash and removes it from recent stories', async () => {
    const deletedStory = storyJob({
      id: 'job:flying-cat',
      input_payload: { title: 'Flying Cat', action: 'draft' },
      output_refs: [{ kind: 'text', content: 'The flying cat skimmed moonlight over the roofs.' }],
    });
    const keptStory = storyJob({
      id: 'job:moon-bakery',
      input_payload: { title: 'Moon Bakery', action: 'continue' },
      output_refs: [{ kind: 'text', content: 'The moon bakery opened only when the tide forgot its name.' }],
    });
    stubStoryApi([deletedStory, keptStory], { assets: [] });
    renderStoryteller();

    const library = await screen.findByRole('complementary', { name: 'Story library' });
    expect(await within(library).findByRole('button', { name: /Flying Cat/ })).toBeInTheDocument();
    fireEvent.click(within(library).getByRole('button', { name: 'Trash' }));

    expect(await screen.findByText('Moved "Flying Cat" to Trash.')).toBeInTheDocument();
    const recentStories = within(library).getByText('Recent stories').closest('section') as HTMLElement | null;
    if (!recentStories) throw new Error('Recent stories section is missing');
    expect(within(recentStories).queryByRole('button', { name: /Flying Cat/ })).not.toBeInTheDocument();
    expect(within(recentStories).getByRole('button', { name: /Moon Bakery/ })).toBeInTheDocument();
    const trashPane = within(library).getByRole('region', { name: 'Trash library pane' });
    expect(within(trashPane).getByRole('button', { name: /Flying Cat/ })).toBeInTheDocument();
  });

  it('saves and exports the selected story version', async () => {
    const createObjectUrl = vi.fn(() => 'blob:story-export');
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const selected = storyJob({
      id: 'job:selected',
      input_payload: { title: 'Saved Orchard', action: 'rewrite' },
      output_refs: [{ kind: 'text', content: 'Saved roots remembered every footstep.' }],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs') return Response.json({ jobs: [selected] });
      if (path === '/api/assets' && init?.method !== 'POST') return Response.json(assetPayload());
      if (path === '/api/assets/story' && init?.method === 'POST') {
        return Response.json({ asset: assetPayload().assets[0], content: 'Saved roots remembered every footstep.' });
      }
      if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();
    expect((await screen.findAllByText('Saved roots remembered every footstep.')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Export Markdown' }));
    expect(await screen.findByText('Exported saved-orchard.md.')).toBeInTheDocument();
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:story-export');
    fireEvent.click(screen.getByRole('button', { name: 'Save story' }));
    expect(await screen.findByText('Saved “Saved Orchard” as a shared story asset.')).toBeInTheDocument();
  });

  it('derives outline entries from generated chapter and scene headings', async () => {
    const chapteredStory = [
      'Chapter 1: The Glass Orchard',
      'Scene 1: The Crystal Row',
      'The orchard rang like crystal at sunset.',
      'Chapter 2: The Memory Market',
      'Scene 1: The Name Seller',
      'Mira traded a silver thread for a forgotten lullaby.',
    ].join('\n\n');
    stubStoryApi([storyJob({ output_refs: [{ kind: 'text', content: chapteredStory }] })]);
    renderStoryteller();
    const outline = await screen.findByRole('complementary', { name: 'Story outline' });
    const chapterTwoButton = await within(outline).findByRole('button', { name: /Chapter 2 The Memory Market/ });
    expect(within(outline).getByRole('button', { name: /Scene 1 The Crystal Row/ })).toBeInTheDocument();
    fireEvent.click(chapterTwoButton);
    const manuscript = screen.getByRole('region', { name: 'Story manuscript' });
    expect((await within(manuscript).findAllByRole('heading', { name: 'The Memory Market' })).length).toBeGreaterThan(0);
  });

  it('adds local chapters from the controls and outline panels', async () => {
    stubStoryApi([storyJob()]);
    renderStoryteller();

    expect(await screen.findByText('The orchard rang like crystal at sunset.')).toBeInTheDocument();
    let outline = await screen.findByRole('complementary', { name: 'Story outline' });
    expect(within(outline).getByRole('button', { name: /Chapter 1 The Glass Orchard/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'New chapter' }));
    expect(await screen.findByText('Added Chapter 2 to The Glass Orchard.')).toBeInTheDocument();
    expect(await within(screen.getByRole('complementary', { name: 'Story outline' })).findByRole('button', { name: /Chapter 2 New chapter/ })).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Story manuscript' })).getAllByRole('heading', { name: 'New chapter' }).length).toBeGreaterThan(0);

    outline = screen.getByRole('complementary', { name: 'Story outline' });
    fireEvent.click(within(outline).getByRole('button', { name: 'Add chapter' }));
    expect(await screen.findByText('Added Chapter 3 to The Glass Orchard.')).toBeInTheDocument();
    expect(await within(screen.getByRole('complementary', { name: 'Story outline' })).findByRole('button', { name: /Chapter 3 New chapter/ })).toBeInTheDocument();

    outline = screen.getByRole('complementary', { name: 'Story outline' });
    fireEvent.click(within(outline).getAllByRole('button')[0]);
    expect(within(outline).queryByRole('button', { name: /Scene 1 Opening/ })).not.toBeInTheDocument();

    fireEvent.click(within(outline).getAllByRole('button')[0]);
    expect(within(outline).getAllByRole('button', { name: /Scene 1 Opening/ }).length).toBeGreaterThan(0);
  });

  it('submits quick actions with active manuscript context', async () => {
    const baseText = 'The orchard rang like crystal at sunset.\n\nEach branch remembered a name.';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json(storyJob({
          id: 'job:continue',
          input_payload: { title: 'Continuation Job', action: 'continue' },
          output_refs: [{ kind: 'text', title: 'Continuation Job', content: 'Mira stepped into the moonlit row.' }],
        }));
      }
      if (path === '/api/jobs') return Response.json({ jobs: [storyJob({ id: 'job:base', output_refs: [{ kind: 'text', content: baseText }] })] });
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();
    const manuscript = await screen.findByRole('region', { name: 'Story manuscript' });
    expect(await within(manuscript).findByText('The orchard rang like crystal at sunset.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Continue Story/ }));
    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as { input_payload?: Record<string, unknown> };
      expect(body.input_payload?.action).toBe('continue');
      expect(body.input_payload?.interaction_mode).toBe('writing');
      expect(body.input_payload?.source_text).toBe(baseText);
      expect(body.input_payload?.source_library_item_id).toBe('job:job:base');
      expect(body.input_payload?.premise).toBe('A city grows fruit made of memory.');
    });
    expect(await within(manuscript).findByText('Mira stepped into the moonlit row.')).toBeInTheDocument();
    const outline = screen.getByRole('complementary', { name: 'Story outline' });
    expect(await within(outline).findByRole('button', { name: /Scene 2 Mira stepped into the moonlit row/ })).toBeInTheDocument();
    const library = screen.getByRole('complementary', { name: 'Story library' });
    const recentStories = within(library).getByText('Recent stories').closest('section') as HTMLElement | null;
    if (!recentStories) throw new Error('Recent stories section is missing');
    expect(within(recentStories).queryByRole('button', { name: /Continuation Job/ })).not.toBeInTheDocument();
  });

  it('submits non-continue quick actions without requiring the premise field', async () => {
    const baseText = 'The orchard rang like crystal at sunset.\n\nEach branch remembered a name.';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json(storyJob({
          id: 'job:rewrite',
          input_payload: { title: 'The Glass Orchard', action: 'rewrite' },
          output_refs: [{ kind: 'text', title: 'The Glass Orchard', content: 'The orchard sang like glass as sunset gathered.' }],
        }));
      }
      if (path === '/api/jobs') return Response.json({ jobs: [storyJob({ id: 'job:base', output_refs: [{ kind: 'text', content: baseText }] })] });
      if (path === '/api/assets') return Response.json({ assets: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();

    expect(await screen.findByText('The orchard rang like crystal at sunset.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Rewrite Paragraph/ }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as { input_payload?: Record<string, unknown> };
      expect(body.input_payload?.action).toBe('rewrite');
      expect(body.input_payload?.interaction_mode).toBe('writing');
      expect(body.input_payload?.source_text).toBe(baseText);
      expect(body.input_payload?.source_library_item_id).toBeNull();
      expect(body.input_payload?.premise).toBe('A city grows fruit made of memory.');
    });
    expect(screen.queryByText('Enter a premise before generating a story.')).not.toBeInTheDocument();
  });

  it('submits typed Story Mode responses with active story context', async () => {
    const baseText = 'The orchard rang like crystal at sunset.\n\nA secret door opened below the oldest glass tree.';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') {
        return Response.json(storyJob({
          id: 'job:story-mode',
          input_payload: { title: 'Story Mode Continuation', action: 'continue' },
          output_refs: [{ kind: 'text', title: 'Story Mode Continuation', content: 'The lantern flared, revealing stairs under the roots.' }],
        }));
      }
      if (path === '/api/jobs') return Response.json({ jobs: [storyJob({ id: 'job:base', output_refs: [{ kind: 'text', content: baseText }] })] });
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();

    expect((await screen.findAllByText('The orchard rang like crystal at sunset.')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Interactive Story Mode/ }));
    expect(await screen.findByRole('region', { name: 'Interactive story mode' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open the door carefully' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Interactive story mode response'), { target: { value: 'I light a lantern and step through the secret door.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue with my response' }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as { input_payload?: Record<string, unknown> };
      expect(body.input_payload?.action).toBe('continue');
      expect(body.input_payload?.interaction_mode).toBe('story');
      expect(body.input_payload?.user_response).toBe('I light a lantern and step through the secret door.');
      expect(String(body.input_payload?.source_text)).toContain('Player response: I light a lantern and step through the secret door.');
      expect(body.input_payload?.source_job_id).toBe('job:base');
    });
    const interactiveReader = screen.getByRole('region', { name: 'Interactive story mode' });
    expect(await within(interactiveReader).findByText('The lantern flared, revealing stairs under the roots.')).toBeInTheDocument();
    const library = screen.getByRole('complementary', { name: 'Story library' });
    const recentStories = within(library).getByText('Recent stories').closest('section') as HTMLElement | null;
    if (!recentStories) throw new Error('Recent stories section is missing');
    expect(within(recentStories).queryByRole('button', { name: /Story Mode Continuation/ })).not.toBeInTheDocument();
  });

  it('asks Story Mode jobs to generate a title when the current story is untitled', async () => {
    const baseText = 'The little engine waited under a sky full of silver smoke.';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') return Response.json(storyJob({ id: 'job:titled-by-ai' }));
      if (path === '/api/jobs') {
        return Response.json({
          jobs: [
            storyJob({
              id: 'job:untitled',
              input_payload: { title: '', premise: 'A tiny train learns courage.', action: 'draft' },
              output_refs: [{ kind: 'text', content: baseText }],
            }),
          ],
        });
      }
      if (path === '/api/assets') return Response.json({ assets: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();

    expect((await screen.findAllByText(baseText)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Interactive Story Mode/ }));
    fireEvent.change(screen.getByLabelText('Interactive story mode response'), { target: { value: 'I climb aboard and ring the bell.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue with my response' }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as { input_payload?: Record<string, unknown> };
      expect(body.input_payload?.interaction_mode).toBe('story');
      expect(body.input_payload?.title).toBeNull();
      expect(body.input_payload?.generate_title).toBe(true);
      expect(String(body.input_payload?.source_text)).toContain(baseText);
    });
  });

  it('submits suggested Story Mode choices as player moves', async () => {
    const baseText = 'A secret door opened below the oldest glass tree.';
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/providers') return Response.json(providerPayload());
      if (path === '/api/jobs' && init?.method === 'POST') return Response.json(storyJob({ id: 'job:choice' }));
      if (path === '/api/jobs') return Response.json({ jobs: [storyJob({ id: 'job:base', output_refs: [{ kind: 'text', content: baseText }] })] });
      if (path === '/api/assets') return Response.json(assetPayload());
      if (path === '/api/assets/asset%3Astory/content') return Response.json(assetContentPayload());
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderStoryteller();

    expect((await screen.findAllByText(baseText)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Interactive Story Mode/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open the door carefully' }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([input, init]) =>
        requestPath(input as RequestInfo | URL) === '/api/jobs' && init?.method === 'POST');
      const body = JSON.parse(String(createCall?.[1]?.body ?? '{}')) as { input_payload?: Record<string, unknown> };
      expect(body.input_payload?.interaction_mode).toBe('story');
      expect(body.input_payload?.user_response).toBe('Open the door carefully');
      expect(body.input_payload?.suggested_choice).toBe('Open the door carefully');
    });
  });
});
