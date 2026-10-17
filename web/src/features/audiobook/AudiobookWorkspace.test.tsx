import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { AudiobookWorkspace } from './AudiobookWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

const project = {
  id: 'book-one', title: 'The Book', author: 'The Author', language: 'en',
  state: 'review_required', current_source_revision_id: 'source-one',
};

function renderWorkspace(client = createTestQueryClient()) {
  const module = omnixModules.find((entry) => entry.id === 'audiobook');
  if (!module) throw new Error('audiobook module missing');
  return render(<QueryClientProvider client={client}><AudiobookWorkspace module={module} /></QueryClientProvider>);
}

describe('AudiobookWorkspace', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it.each(['Voices', 'Characters'])('queues %s auditions before rendering without changing the saved cast', async (section) => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      let body: unknown;
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        body = { job_id: 'audition' };
      } else if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [
        { id: 'voice-cloning:saved', name: 'Saved voice', language: 'en' },
        { id: 'voice-cloning:candidate', name: 'Candidate voice', language: 'en' },
      ] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }],
        review_issues: [], speakers: [{ id: 'speaker-one', canonical_name: 'Ada', kind: 'character',
          status: 'active', aliases: [], casting: { voice_profile_id: 'voice-cloning:saved', revision: 1 } }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else if (url.endsWith('/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'Hello.',
        spans: [{ id: 'span-one', source_text: 'Hello.', structural_kind: 'dialogue',
          annotation: { id: 'annotation-one', role: 'dialogue', speaker_id: 'speaker-one',
            delivery: '', review_status: 'accepted', evidence: {} },
          speech_plan: { tts_input_text: 'Hello.', hash: 'plan', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: section }));
    if (section === 'Voices') {
      fireEvent.change(await screen.findByLabelText('Voice assignment target'), { target: { value: 'speaker-one' } });
    }
    const card = screen.getByRole('heading', { name: section === 'Voices' ? 'Candidate voice' : 'Ada', level: 3 }).closest('article')!;
    const preview = within(card).getByRole('button', { name: section === 'Voices' ? 'Preview' : 'Audition' });
    await waitFor(() => expect(preview).toBeEnabled());
    fireEvent.click(preview);
    await waitFor(() => expect(posts).toEqual([{
      url: '/api/audiobook/projects/book-one/preview',
      body: { chapter_id: 'chapter-one', span_id: 'span-one',
        ...(section === 'Voices' ? { voice_profile_id: 'voice-cloning:candidate' } : {}),
        model_revision: 'sha256:test-model' },
    }]));
  });

  it.each(['classification', 'extraction'])('refreshes cached spans when background %s completes', async (phase) => {
    let completed = false;
    let chapterRequests = 0;
    const client = createTestQueryClient();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        current_source_revision_id: phase === 'extraction' && completed ? 'source-two' : 'source-one',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }],
        review_issues: [], speakers: [{ id: 'ehsan', canonical_name: 'Ehsan', kind: 'character', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 1 },
        pipeline_jobs: [{ id: 'background-job', type: phase === 'classification' ? 'audiobook.analyze' : 'audiobook.ingest', status: completed ? 'completed' : 'running' }],
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) {
        chapterRequests += 1;
        body = {
          id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'Hello.',
          spans: [{ id: 'span-one', source_text: 'Hello.', structural_kind: 'dialogue',
            annotation: completed ? { id: 'annotation-one', role: 'dialogue', speaker_id: 'ehsan', delivery: '', review_status: 'accepted', evidence: {} } : null,
            speech_plan: { tts_input_text: 'Hello.', hash: 'plan', transformations: [] } }],
        };
      } else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace(client);
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const spans = await screen.findByRole('region', { name: 'Characterization spans' });
    expect(within(spans).getByText('dialogue · Unassigned')).toBeInTheDocument();
    const requestsBeforeCompletion = chapterRequests;
    completed = true;
    // Simulate the project poll; do not refresh the chapter or remount the workspace.
    await client.invalidateQueries({ queryKey: ['audiobook', 'project', 'book-one'] });
    expect(await within(spans).findByText('dialogue · Ehsan')).toBeInTheDocument();
    expect(chapterRequests).toBeGreaterThan(requestsBeforeCompletion);
    expect(within(spans).queryByText('dialogue · Unassigned')).not.toBeInTheDocument();
  });

  it('loads projects beyond the first library page', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      ...project, id: `book-${index + 1}`, title: `Project ${index + 1}`,
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: firstPage };
      else if (url.endsWith('/projects?offset=100')) body = { projects: [{ ...project, id: 'book-101', title: 'Project 101' }] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();

    expect(await screen.findByRole('button', { name: /Project 101/ })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects?offset=100', expect.anything());
  });

  it('opens the new-project form from the library rail', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [], speakers: [], render_jobs: [],
        preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Start new project' }));
    expect(await screen.findByRole('heading', { name: 'Create a New Audiobook Project' })).toBeInTheDocument();
    expect(screen.getByLabelText('Project title *')).toBeInTheDocument();
  });

  it('shows canonical chapter text and review state from the backend', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/classification-rules') && init?.method === 'POST') {
        return new Response(JSON.stringify({ classification_rules: 'Character quotes can also be in speaker: quote format.' }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      if (url.includes('/projects/book-one/source/library') && init?.method === 'POST') {
        return new Response(JSON.stringify({ project_id: 'book-one', source_asset_id: 'asset-one', job_id: 'job-one' }),
          { status: 202, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/source-library')) body = { directory: 'resources\\data\\audiobooks', files: [{ name: 'joy.pdf', source_format: 'pdf', size_bytes: 123 }] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        classification_rules: 'Saved speaker instructions.',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 36 }],
        review_issues: [{ id: 'issue-one', reason: 'speaker uncertain',
          source_text: '"Hello," she said.', chapter_id: 'chapter-one',
          chapter_title: 'Opening', speaker_id: null }],
        speakers: [], render_jobs: [], export_jobs: [],
        pipeline_jobs: [
          { id: 'ingest-new', type: 'audiobook.ingest', status: 'completed', progress: {}, error: null, attempts: 1, max_attempts: 3, can_retry: false },
          { id: 'ingest-old', type: 'audiobook.ingest', status: 'failed', progress: {}, error: { code: 'ingest_failed', message: 'old failure', retryable: true }, attempts: 3, max_attempts: 3, can_retry: true },
        ],
        render_progress: { completed: 0, total: 1 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening',
        canonical_text: 'The exact book text.\n\nIt stays here.',
        spans: [{ id: 'span-one', source_text: 'The exact book text.\n\nIt stays here.', structural_kind: 'narration',
          annotation: null, speech_plan: { tts_input_text: 'The exact book text.\n\nIt stays here.', hash: 'plan', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    const firstVisit = renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    expect(await screen.findByLabelText('Canonical chapter text')).toHaveTextContent('The exact book text.');
    const sourcePanel = screen.getByRole('region', { name: 'Source text' });
    const spanPanel = screen.getByRole('region', { name: 'Characterization spans' });
    expect(sourcePanel).toContainElement(screen.getByLabelText('Canonical chapter text'));
    expect(sourcePanel).not.toContainElement(spanPanel);
    expect(sourcePanel.parentElement).toBe(spanPanel.parentElement);
    expect(within(spanPanel).getByRole('heading', { name: 'Span review' })).toBeInTheDocument();
    const classifyPanel = screen.getByRole('region', { name: 'Text classification' });
    expect(classifyPanel.parentElement?.firstElementChild).toBe(classifyPanel);
    expect(within(classifyPanel).getByRole('button', { name: 'Classify text' })).toBeInTheDocument();
    expect(within(spanPanel).queryByRole('button', { name: 'Classify text' })).not.toBeInTheDocument();
    expect(within(spanPanel).getByRole('button', { name: 'Preview' })).toBeDisabled();
    expect(within(spanPanel).getByText('Choose a speaker and save interpretation, or classify text, before previewing audio.')).toBeInTheDocument();
    expect(screen.queryByText(/ingest failed/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit project' }));
    expect(screen.getByLabelText('Upload from computer')).toHaveAttribute(
      'accept', '.pdf,.epub,.docx,.html,.htm,.txt,.text,.md,.markdown',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Choose from local library' }));
    fireEvent.change(await screen.findByLabelText('Local audiobook source'), { target: { value: 'joy.pdf' } });
    fireEvent.change(screen.getByLabelText('Exclude PDF pages'), { target: { value: '1-3, 42-45' } });
    expect(screen.getByLabelText('Custom rules for classification (optional)')).toHaveValue('Saved speaker instructions.');
    fireEvent.change(screen.getByLabelText('Custom rules for classification (optional)'), {
      target: { value: 'Character quotes can also be in speaker: quote format.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Use selected book' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/source/library?filename=joy.pdf&exclude_pages=1-3%2C%2042-45',
      { method: 'POST' },
    ));
    const saveIndex = fetchMock.mock.calls.findIndex(([url, init]) => String(url).endsWith('/classification-rules') && init?.method === 'POST');
    const importIndex = fetchMock.mock.calls.findIndex(([url, init]) => String(url).includes('/source/library?') && init?.method === 'POST');
    expect(saveIndex).toBeGreaterThanOrEqual(0);
    expect(saveIndex).toBeLessThan(importIndex);
    expect(fetchMock.mock.calls[saveIndex][1]?.body).toBe(JSON.stringify({ custom_rules: 'Character quotes can also be in speaker: quote format.' }));
    expect(screen.queryByRole('region', { name: 'Human review queue' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /issues to resolve/ }));
    expect(screen.getByRole('region', { name: 'Human review queue' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open review queue' }));
    expect(within(screen.getByRole('region', { name: 'Human review queue' })).getByText('"Hello," she said.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /Books/ }).at(-1)!);
    expect(screen.getByRole('heading', { name: 'Books & chapters' })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    expect(screen.getByRole('heading', { name: 'Character-to-voice mapping' })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /Pronunciations/ }).at(-1)!);
    expect(screen.getAllByRole('heading', { name: 'Pronunciations' }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: /Chapters/ }).at(-1)!);
    fireEvent.click(screen.getByRole('button', { name: 'Outline and cast' }));
    expect(screen.getByRole('button', { name: 'Outline and cast' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Chapter and cast inspector')).toHaveClass('mobile-open');
    fireEvent.click(screen.getByRole('button', { name: 'Close outline' }));
    expect(screen.getByRole('button', { name: 'Outline and cast' })).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'Render & Export' }));
    expect(screen.getByRole('button', { name: 'Render book' })).toBeDisabled();
    expect(screen.getAllByText('Not started').length).toBeGreaterThan(0);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one', expect.anything()));
    firstVisit.unmount();
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    expect(await screen.findByLabelText('Canonical chapter text')).toHaveTextContent('The exact book text.');
  });


  it('changes reading mode and can override a skipped structural block', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/reading-policy') && init?.method === 'PATCH') {
        return new Response(JSON.stringify({ project_id: 'book-one', audiobook_mode: 'story_only' }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      if (url.endsWith('/projects/book-one/document-overrides') && init?.method === 'POST') {
        return new Response(JSON.stringify({ id: 'override-one', scope: 'BLOCK', scope_key: 'block-page', action: 'READ' }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, audiobook_mode: 'standard',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 24 }],
        review_issues: [], speakers: [], render_jobs: [], preview_jobs: [], export_jobs: [],
        render_progress: { completed: 0, total: 1 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'Page 1 of 3\nStory.',
        spans: [{ id: 'span-one', source_text: 'Page 1 of 3\nStory.', structural_kind: 'narration',
          annotation: null, speech_plan: { tts_input_text: 'Story.', hash: 'plan', transformations: [] } }],
        document_blocks: [{
          id: 'block-page', original_text: 'Page 1 of 3', effective_role: 'page_number',
          confidence: 0.995, render_action: 'SKIP', speaker_analysis_visibility: 'EXCLUDE',
          provenance: [{ source: 'pattern', signal: 'page_number', value: 'Page 1 of 3' }],
        }, {
          id: 'block-heading', original_text: 'Chapter One', effective_role: 'chapter_heading',
          confidence: 0.99, render_action: 'READ', speaker_analysis_visibility: 'CONTEXT_ONLY',
          provenance: [{ source: 'pattern', signal: 'chapter_heading', value: true }],
        }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    const mode = await screen.findByLabelText('Audiobook reading mode');
    expect(mode).toHaveValue('standard');
    fireEvent.change(mode, { target: { value: 'story_only' } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/reading-policy',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ mode: 'story_only' }) }),
    ));

    fireEvent.click(screen.getAllByRole('button', { name: /Chapters/ }).at(-1)!);
    expect(await screen.findByLabelText('Audiobook structural content')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Role for Page 1 of 3'), {
      target: { value: 'story_text' },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/document-overrides',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          scope: 'BLOCK', scope_key: 'block-page', action: 'DEFAULT',
          role_override: 'story_text',
        }),
      }),
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Read block once Page 1 of 3' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/document-overrides',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          scope: 'BLOCK', scope_key: 'block-page', action: 'READ_ONCE', role_override: null,
        }),
      }),
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Skip block Chapter One' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/document-overrides',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          scope: 'BLOCK', scope_key: 'block-heading', action: 'SKIP', role_override: null,
        }),
      }),
    ));
  });

  it('shows detected speaker candidates and lets the operator confirm one', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/speakers/candidate-one/aliases') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          id: 'alias-one', speaker_id: 'candidate-one', alias: 'The Traveller',
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      if (url.endsWith('/projects/book-one/speakers') && init?.method === 'POST') {
        return new Response(JSON.stringify({ id: 'candidate-one', canonical_name: 'Time Traveller', status: 'active', promoted: true }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [],
        review_issues: [{ id: 'issue-one', reason: 'UNSUPPORTED_SPEAKER', source_text: '"Hello."',
          chapter_id: 'chapter-one', chapter_title: 'Opening', speaker_id: 'narrator',
          speaker_candidate: 'Time Traveller', structural_kind: 'dialogue', evidence: {} }],
        speakers: [
          { id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'candidate-one', canonical_name: 'Time Traveller', kind: 'character', status: 'proposed', occurrence_count: 3, casting: null, aliases: [], proposed_aliases: ['The Traveller'] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    expect(await screen.findByRole('heading', { name: 'Character-to-voice mapping' })).toBeInTheDocument();
    const confirmAlias = await screen.findByRole('button', {
      name: 'Confirm alias The Traveller for Time Traveller',
    });
    fireEvent.click(confirmAlias);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speakers/candidate-one/aliases',
      expect.objectContaining({
        method: 'POST', body: JSON.stringify({ alias: 'The Traveller' }),
      }),
    ));
    const confirmCandidate = await screen.findByRole('button', { name: /Confirm Time Traveller/ });
    expect(confirmCandidate).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss detection' })).toBeDisabled();
    fireEvent.click(confirmCandidate);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speakers',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ canonical_name: 'Time Traveller' }) }),
    ));
    expect(screen.getByText(/Human review required/)).toBeInTheDocument();
  });


  it('confirms every detected speaker at once', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/speakers') && init?.method === 'POST') {
        return new Response(JSON.stringify({ id: 'speaker', status: 'active', promoted: true }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [],
        speakers: [
          { id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'candidate-one', canonical_name: 'Time Traveller', kind: 'character', status: 'proposed', occurrence_count: 3, casting: null, aliases: [] },
          { id: 'candidate-two', canonical_name: 'Weena', kind: 'character', status: 'proposed', occurrence_count: 2, casting: null, aliases: [] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm all 2' }));
    await waitFor(() => expect(fetchMock.mock.calls
      .filter(([url, init]) => String(url) === '/api/audiobook/projects/book-one/speakers' && init?.method === 'POST')
      .map(([, init]) => init?.body)).toEqual([
      JSON.stringify({ canonical_name: 'Time Traveller' }),
      JSON.stringify({ canonical_name: 'Weena' }),
    ]));
  });

  it('shows confirmed aliases with an unconfirm control and confirms earlier suggestions in one step', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({ status: 'rejected' }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [],
        speakers: [
          { id: 'knights', canonical_name: 'Temple Knights', kind: 'character', status: 'active', casting: null,
            aliases: ['Keepers of Anastasia'], proposed_aliases: ['The other knights'] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    fireEvent.click(await screen.findByRole('button', { name: 'Unconfirm alias Keepers of Anastasia for Temple Knights' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speakers/knights/aliases/unconfirm',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ alias: 'Keepers of Anastasia' }) }),
    ));
    expect(screen.getByText(/1 suggested alias not yet confirmed/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm all suggested aliases' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Confirm all suggested aliases' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/aliases/confirm-suggested',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('opens every open review issue in the main view, grouped by chapter and filterable', async () => {
    const issue = (id: string, chapter: string, title: string, text: string, extra: Record<string, unknown> = {}) => ({
      id, span_id: `span-${id}`, reason: 'LOW_CONFIDENCE_SPEAKER', evidence: {}, source_text: text, chapter_id: chapter,
      chapter_title: title, speaker_id: null, speaker_candidate: null, structural_kind: 'dialogue', ...extra,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST') return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } });
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }, { id: 'chapter-two', ordinal: 1, title: 'Closing', character_count: 6 }],
        review_issues: [
          issue('issue-one', 'chapter-one', 'Opening', '"Who goes there?"', { reason: 'UNSUPPORTED_SPEAKER', speaker_candidate: 'Gate Guard' }),
          issue('issue-two', 'chapter-two', 'Closing', '"Farewell."'),
        ],
        speakers: [{ id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'ada', canonical_name: 'Ada', kind: 'character', status: 'active', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else if (url.includes('/chapters/')) body = { id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: '', spans: [] };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    fireEvent.click(await screen.findByRole('button', { name: 'Open human review queue' }));
    const queue = await screen.findByRole('region', { name: 'Human review queue' });
    expect(within(queue).getByText(/2 open issues in 2 chapters/)).toBeInTheDocument();
    expect(within(queue).getByRole('region', { name: 'Review issues in Opening' })).toBeInTheDocument();
    expect(within(queue).getByRole('region', { name: 'Review issues in Closing' })).toBeInTheDocument();
    expect(within(queue).getByText('Speaker not in the cast')).toBeInTheDocument();

    fireEvent.change(within(queue).getByLabelText('Filter review issues by chapter'), { target: { value: 'chapter-two' } });
    expect(within(queue).queryByRole('region', { name: 'Review issues in Opening' })).not.toBeInTheDocument();
    expect(within(queue).getByText('1 of 2 issues')).toBeInTheDocument();
    fireEvent.change(within(queue).getByLabelText('Filter review issues by chapter'), { target: { value: 'all' } });

    fireEvent.click(within(queue).getByRole('button', { name: 'Add “Gate Guard” to the cast' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/speakers',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ canonical_name: 'Gate Guard' }) })));
    fireEvent.change(within(queue).getByLabelText('Speaker for review issue-two'), { target: { value: 'ada' } });
    await waitFor(() => expect(within(queue).getAllByRole('button', { name: 'Confirm speaker' })[1]).toBeEnabled());
    fireEvent.click(within(queue).getAllByRole('button', { name: 'Confirm speaker' })[1]);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/review/issue-two',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ speaker_id: 'ada', role: 'dialogue' }) })));
  });

  it('confirms every shown review issue at once, adding detected speakers to the cast', async () => {
    const issue = (id: string, text: string, extra: Record<string, unknown> = {}) => ({
      id, span_id: `span-${id}`, reason: 'LOW_CONFIDENCE_SPEAKER', evidence: {}, source_text: text, chapter_id: 'chapter-one',
      chapter_title: 'Opening', speaker_id: null, speaker_candidate: null, structural_kind: 'dialogue', ...extra,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST') return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } });
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }],
        review_issues: [
          issue('issue-one', '"Okay."', { speaker_id: 'ada' }),
          issue('issue-two', '"I understand."'),
          issue('issue-three', '"Halt."', { speaker_id: 'guard' }),
        ],
        speakers: [{ id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'ada', canonical_name: 'Ada', kind: 'character', status: 'active', casting: null, aliases: [] },
          { id: 'guard', canonical_name: 'Guard', kind: 'character', status: 'proposed', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else if (url.includes('/chapters/')) body = { id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: '', spans: [] };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    fireEvent.click(await screen.findByRole('button', { name: 'Open human review queue' }));
    const queue = await screen.findByRole('region', { name: 'Human review queue' });

    // issue-two has no speaker yet; the detected Guard counts, and confirming adds it to the cast.
    expect(within(queue).getByRole('button', { name: 'Confirm all 2' })).toBeInTheDocument();
    expect(within(queue).getByText(/1 shown issue has no speaker yet/)).toBeInTheDocument();
    expect(within(queue).getByText(/Confirming also adds 1 speaker to the cast: Guard\./)).toBeInTheDocument();
    fireEvent.change(within(queue).getByLabelText('Speaker for review issue-two'), { target: { value: 'narrator' } });
    fireEvent.click(await within(queue).findByRole('button', { name: 'Confirm all 3' }));

    expect(await screen.findByText('3 review decisions saved and 1 speaker added to the cast.')).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => `${String(url).split('/book-one/')[1]} ${String(init?.body)}`);
    // Decisions for cast speakers are saved before Guard is added, then Guard's issue is confirmed.
    expect(posts).toEqual([
      'review/issue-one {"speaker_id":"ada","role":"dialogue"}',
      'review/issue-two {"speaker_id":"narrator","role":"dialogue"}',
      'speakers {"canonical_name":"Guard"}',
      'review/issue-three {"speaker_id":"guard","role":"dialogue"}',
    ]);
  });

  it('confirms suggested speakers missing from the cast by adding them first', async () => {
    const issue = (id: string, candidate: string, extra: Record<string, unknown> = {}) => ({
      id, span_id: `span-${id}`, reason: 'LOW_CONFIDENCE_SPEAKER', evidence: {}, source_text: `"${id}"`, chapter_id: 'chapter-one',
      chapter_title: 'Opening', speaker_id: null, speaker_candidate: candidate, structural_kind: 'dialogue', ...extra,
    });
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
    let guardAdded = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/speakers') && init?.method === 'POST') {
        const name = JSON.parse(String(init.body)).canonical_name;
        if (name === 'Roxy') return json({ detail: 'speaker name is already a confirmed alias of another speaker' }, 422);
        if (name === 'Guard') guardAdded = true;
        return json({ id: name === 'Guard' ? 'guard' : 'zanoba-new', canonical_name: name, status: 'active' });
      }
      if (init?.method === 'POST') return json({ ok: true });
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }],
        review_issues: [
          // Adding the detected Guard resolves both of its issues on the server.
          ...(guardAdded ? [] : [issue('issue-one', 'Guard', { speaker_id: 'guard' }), issue('issue-two', ' guard ', { speaker_id: 'guard' })]),
          issue('issue-three', 'Roxy'),
          issue('issue-four', 'Zanoba'),
          issue('issue-five', 'Ada', { speaker_id: 'ada' }),
        ],
        speakers: [{ id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'ada', canonical_name: 'Ada', kind: 'character', status: 'active', casting: null, aliases: [] },
          { id: 'guard', canonical_name: 'Guard', kind: 'character', status: guardAdded ? 'active' : 'proposed', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else if (url.includes('/chapters/')) body = { id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: '', spans: [] };
      else throw new Error(`unexpected API request ${url}`);
      return json(body);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);
    fireEvent.click(await screen.findByRole('button', { name: 'Open human review queue' }));
    const queue = await screen.findByRole('region', { name: 'Human review queue' });

    expect(within(queue).getByText(/Confirming also adds 3 speakers to the cast: Guard, Roxy, Zanoba\./)).toBeInTheDocument();
    fireEvent.click(within(queue).getByRole('button', { name: 'Confirm all 5' }));

    expect(await screen.findByText(/Could not add Roxy to the cast, so its issues stay open/)).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => `${String(url).split('/book-one/')[1]} ${String(init?.body)}`);
    expect(posts).toEqual([
      'review/issue-five {"speaker_id":"ada","role":"dialogue"}',
      'speakers {"canonical_name":"Guard"}',
      'speakers {"canonical_name":"Roxy"}',
      'speakers {"canonical_name":"Zanoba"}',
      'review/issue-four {"speaker_id":"zanoba-new","role":"dialogue"}',
    ]);
  });

  it('dismisses an unused detected speaker', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/speakers/candidate-unused/reject') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          id: 'candidate-unused', canonical_name: 'Ghost', status: 'rejected',
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [],
        speakers: [
          { id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'candidate-unused', canonical_name: 'Ghost', kind: 'character', status: 'proposed', occurrence_count: 0, casting: null, aliases: [] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);

    const dismiss = await screen.findByRole('button', { name: 'Dismiss detection' });
    expect(dismiss).toBeEnabled();
    fireEvent.click(dismiss);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speakers/candidate-unused/reject',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('lets assigning a voice confirm a detected speaker directly', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/speakers/candidate-one/casting') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          speaker_id: 'candidate-one', voice_profile_id: 'voice-one', revision: 1,
        }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [{ id: 'voice-one', name: 'Voice One', language: 'en' }] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [],
        speakers: [
          { id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: null, aliases: [] },
          { id: 'candidate-one', canonical_name: 'Time Traveller', kind: 'character', status: 'proposed', occurrence_count: 3, casting: null, aliases: [] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(screen.getAllByRole('button', { name: /Characters/ }).at(-1)!);

    const voiceSelect = await screen.findByLabelText('Main voice for Time Traveller');
    expect(voiceSelect).toBeEnabled();
    fireEvent.change(voiceSelect, { target: { value: 'voice-one' } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speakers/candidate-one/casting',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ voice_profile_id: 'voice-one' }) }),
    ));
  });

  it('skips policy-muted front matter when choosing a sample preview', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/preview') && init?.method === 'POST') {
        return new Response(JSON.stringify({ job_id: 'preview-story', span_id: 'story-span' }),
          { status: 202, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = {
        provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS',
        model_revision: 'sha256:test-model',
      };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        state: 'ready_to_render',
        audiobook_mode: 'story_only',
        chapters: [
          { id: 'chapter-front', ordinal: 0, title: 'Contents', character_count: 10 },
          { id: 'chapter-story', ordinal: 1, title: 'Chapter One', character_count: 24 },
        ],
        review_issues: [], speakers: [], render_jobs: [], pipeline_jobs: [],
        preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-front')) body = {
        id: 'chapter-front', ordinal: 0, title: 'Contents', canonical_text: '# Contents',
        spans: [{
          id: 'front-span', source_text: '# Contents', structural_kind: 'narration',
          annotation: null,
          speech_plan: { tts_input_text: '', hash: 'front-plan', transformations: [] },
        }],
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-story')) body = {
        id: 'chapter-story', ordinal: 1, title: 'Chapter One',
        canonical_text: 'Daniel opened the gate.',
        spans: [{
          id: 'story-span', source_text: 'Daniel opened the gate.', structural_kind: 'narration',
          annotation: null,
          speech_plan: {
            tts_input_text: 'Daniel opened the gate.', hash: 'story-plan', transformations: [],
          },
        }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    expect((await screen.findAllByText('# Contents')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Play sample' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/preview',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"span_id":"story-span"'),
      }),
    ));
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/preview',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"span_id":"front-span"'),
      }),
    );
  });

  it('enables preview after saving a manual speaker assignment before classification', async () => {
    let assigned = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/preview') && init?.method === 'POST') {
        return new Response(JSON.stringify({ job_id: 'preview-one', span_id: 'span-one' }),
          { status: 202, headers: { 'content-type': 'application/json' } });
      }
      if (url.endsWith('/spans/span-one/annotation') && init?.method === 'POST') {
        assigned = true;
        return new Response(JSON.stringify({ changed: true, revision: 1 }), { status: 200 });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'extracted',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 }],
        review_issues: [], speakers: [{ id: 'narrator', canonical_name: 'Narrator', kind: 'narrator',
          casting: { voice_profile_id: 'voice-one', voice_revision_hash: 'voice-hash', revision: 1 }, aliases: [] }],
        render_jobs: [], export_jobs: [],
        preview_jobs: [{ id: 'preview-one', span_id: 'span-one', status: 'completed' }],
        render_progress: { completed: 0, total: 1 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'A line.',
        spans: [{ id: 'span-one', source_text: 'A line.', structural_kind: 'narration',
          annotation: assigned ? { id: 'annotation-one', role: 'narration', speaker_id: 'narrator', delivery: '', review_status: 'user_resolved', evidence: {} } : null, speech_plan: { tts_input_text: 'A line.', hash: 'plan', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const preview = await screen.findByRole('button', { name: 'Preview' });
    expect(preview).toBeDisabled();
    // Spans start collapsed; expanding one opens its interpretation controls.
    expect(screen.queryByLabelText('Selected span speaker')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Expand span / }));
    fireEvent.change(screen.getByLabelText('Selected span speaker'), { target: { value: 'narrator' } });
    expect(screen.getByText('Save interpretation to use your selected speaker and delivery in the preview.')).toBeInTheDocument();
    expect(preview).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save interpretation' }));
    await waitFor(() => expect(preview).toBeEnabled());
    expect(screen.getByLabelText('Preview A line.')).toHaveAttribute('src',
      '/api/audiobook/projects/book-one/previews/preview-one/audio');
    fireEvent.click(preview);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/preview',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"span_id":"span-one"') }),
    ));
  });

  it('renders fractional progress reported by an active chapter job', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        state: 'rendering',
        chapters: [
          { id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 },
          { id: 'chapter-two', ordinal: 1, title: 'Middle', character_count: 7 },
        ],
        review_issues: [], speakers: [], export_jobs: [], preview_jobs: [],
        render_jobs: [
          { id: 'render-one', chapter_id: 'chapter-one', status: 'completed', progress: { current: 1, total: 1 } },
          { id: 'render-two', chapter_id: 'chapter-two', status: 'running', progress: { current: 0.37, total: 1, unit_current: 760, unit_total: 2048 } },
        ],
        render_progress: { completed: 1, total: 2 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));

    expect(await screen.findByText('37%')).toBeInTheDocument();
  });

  it('excludes policy-skipped source chapters from render progress', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        state: 'rendering',
        chapters: [
          { id: 'chapter-front-matter', ordinal: 0, title: 'Contents', character_count: 12 },
          { id: 'chapter-one', ordinal: 1, title: 'Chapter One', character_count: 20 },
        ],
        review_issues: [], speakers: [], export_jobs: [], preview_jobs: [], pipeline_jobs: [],
        render_jobs: [
          { id: 'render-one', chapter_id: 'chapter-one', status: 'running',
            progress: { current: 0.5, total: 1 } },
        ],
        render_progress: { completed: 0, total: 2 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));

    expect((await screen.findAllByText('50%')).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0 \/ 1 audiobook chapters/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/1 skipped by policy/).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Skipped/).some((node) =>
        node.textContent?.includes('by reading policy'),
      ),
    ).toBe(true);
    expect(screen.getAllByText('Skipped by policy').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Not applicable').length).toBeGreaterThan(0);
  });

  it('keeps pause and stop controls in the render queue', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one/render/pause') && init?.method === 'POST') body = { project_id: 'book-one', paused: 2 };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'rendering',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 },
          { id: 'chapter-two', ordinal: 1, title: 'Middle', character_count: 7 }],
        review_issues: [], speakers: [], export_jobs: [], preview_jobs: [],
        render_jobs: [
          { id: 'render-one', chapter_id: 'chapter-one', status: 'running', progress: { current: 0, total: 1 } },
          { id: 'render-two', chapter_id: 'chapter-two', status: 'paused', progress: { current: 0, total: 1 } },
        ],
        render_progress: { completed: 0, total: 2 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));

    expect(await screen.findByRole('button', { name: 'Pause all chapters' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Resume all chapters' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Stop all chapters' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Pause chapter' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Resume chapter' })).toBeEnabled();
    expect(screen.getAllByRole('button', { name: 'Stop chapter' })).toHaveLength(2);
    expect(document.querySelector('.audiobook-render-controls')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Pause all chapters' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/render/pause', expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('orders waiting chapters and plays finished ones from the render queue', async () => {
    const played: string[] = [];
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      played.push(this.src);
      this.dispatchEvent(new Event('play'));
      return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
      this.dispatchEvent(new Event('pause'));
    });
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects/book-one/render/order') && init?.method === 'POST') body = { render_run_id: 'run', chapter_ids: [] };
      else if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'rendering',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 },
          { id: 'chapter-two', ordinal: 1, title: 'Middle', character_count: 7 },
          { id: 'chapter-three', ordinal: 2, title: 'Closing', character_count: 7 }],
        review_issues: [], speakers: [], export_jobs: [], preview_jobs: [],
        render_jobs: [
          { id: 'render-one', chapter_id: 'chapter-one', status: 'completed', progress: {}, queue_position: null, audio: { duration_seconds: 125 } },
          { id: 'render-two', chapter_id: 'chapter-two', status: 'queued', progress: {}, queue_position: 2, audio: null },
          { id: 'render-three', chapter_id: 'chapter-three', status: 'queued', progress: {}, queue_position: 1, audio: null },
        ],
        render_progress: { completed: 1, total: 3 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));

    // Closing renders first; it cannot move further up.
    expect(await screen.findByLabelText('Render order 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Move Closing up the render order' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render Closing next' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Render Middle next' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/render/order',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ chapter_ids: ['chapter-two'] }) })));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Render in book order' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Render in book order' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/render/order',
      expect.objectContaining({ body: JSON.stringify({ chapter_ids: ['chapter-two', 'chapter-three'] }) })));

    // Only the finished chapter can be played.
    expect(screen.queryByRole('button', { name: 'Play Middle' })).not.toBeInTheDocument();
    expect(screen.getByText('2:05')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop playback of Opening' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Play Opening' }));
    expect(played[0]).toMatch(/\/api\/audiobook\/projects\/book-one\/chapters\/chapter-one\/audio$/);
    expect(await screen.findByText('0:00 / 2:05')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Pause playback of Opening' }));
    expect(await screen.findByRole('button', { name: 'Play Opening' })).toHaveTextContent('Resume');
    fireEvent.click(screen.getByRole('button', { name: 'Stop playback of Opening' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop playback of Opening' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Play Opening' })).toHaveTextContent('Play');
    expect(screen.getByText('2:05')).toBeInTheDocument();
  });

  it.each([
    ['running', { status: 'running', progress: { current: 42, total: 100, message: 'Encoding M4B' } }],
    ['completed', { status: 'completed', progress: { current: 1, total: 1, message: 'export encoded' } }],
  ])('shows the latest export while it is %s', async (state, exportJob) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'ready_to_export',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 }],
        review_issues: [], speakers: [], preview_jobs: [],
        render_jobs: [{ id: 'render-one', chapter_id: 'chapter-one', status: 'completed', progress: {} }],
        export_jobs: [{ id: 'export-one', format: 'm4b', error: null, ...exportJob }],
        render_progress: { completed: 1, total: 1 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));

    if (state === 'running') {
      const bar = await screen.findByRole('progressbar', { name: 'M4B export progress' });
      expect(bar).toHaveAttribute('value', '42');
      expect(screen.getByText('M4B export · Encoding M4B')).toBeInTheDocument();
      expect(screen.getByText('42%')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Exporting…' })).toBeDisabled();
    } else {
      expect(await screen.findByText('M4B export ready.')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar', { name: 'M4B export progress' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Export Now' })).toBeEnabled();
      fireEvent.click(screen.getByRole('button', { name: 'View in Assets' }));
      expect(await screen.findByRole('button', { name: 'Assets', current: 'page' })).toBeInTheDocument();
    }
  });

  it('shows chapter extraction in the book source panel and blocks a second upload meanwhile', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'imported', current_source_revision_id: null, source_filename: null,
        chapters: [], review_issues: [], speakers: [], render_jobs: [], export_jobs: [], preview_jobs: [],
        // Newest first: a duplicate upload waits behind the extraction that is running.
        pipeline_jobs: [
          { id: 'ingest-duplicate', type: 'audiobook.ingest', status: 'queued', progress: {}, error: null },
          { id: 'ingest-running', type: 'audiobook.ingest', status: 'running', progress: { current: 2, total: 3, message: 'preparing source structure' }, error: null },
        ],
        render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));

    const panel = await screen.findByRole('region', { name: 'Book source' });
    expect(within(panel).getByText('Extracting')).toBeInTheDocument();
    expect(within(panel).getByText('Book received')).toBeInTheDocument();
    expect(within(panel).getByText('Extracting chapters · preparing source structure')).toBeInTheDocument();
    expect(within(panel).getByRole('progressbar', { name: 'Chapter extraction progress' })).toHaveAttribute('value', '67');
    expect(within(panel).getByLabelText('Upload from computer')).toBeDisabled();
    expect(within(panel).getByRole('button', { name: 'Choose from local library' })).toBeDisabled();
  });

  it('saves a selected span interpretation without changing the displayed source', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/spans/span-one/annotation') && init?.method === 'POST') {
        return new Response(JSON.stringify({ span_id: 'span-one', revision: 2, changed: true }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 7 }],
        review_issues: [], speakers: [{ id: 'speaker-one', canonical_name: 'Narrator', kind: 'narrator', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 1 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'A line.',
        spans: [{ id: 'span-one', source_text: 'A line.', structural_kind: 'narration',
          annotation: { id: 'annotation-one', role: 'narration', speaker_id: 'speaker-one',
            speaker_candidate: null, delivery: '', review_status: 'accepted', evidence: {} },
          speech_plan: { tts_input_text: 'A line.', hash: 'plan', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    expect(await screen.findByLabelText('Canonical chapter text')).toHaveTextContent('A line.');
    fireEvent.click(screen.getByRole('button', { name: /^Expand span / }));
    fireEvent.change(screen.getByLabelText('Selected span delivery'), { target: { value: 'softly' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save interpretation' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/spans/span-one/annotation',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"delivery":"softly"') }),
    ));
    expect(screen.getByLabelText('Canonical chapter text')).toHaveTextContent('A line.');
  });

  it('shows the audiobook production path and opens its tools', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [], speakers: [], render_jobs: [],
        preview_jobs: [], export_jobs: [], pronunciations: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const steps = await screen.findByRole('list', { name: 'Audiobook production steps' });
    expect(within(steps).getAllByRole('listitem')).toHaveLength(6);
    expect(within(steps).getByText('Source attached')).toBeInTheDocument();
    fireEvent.click(within(steps).getByRole('button', { name: 'Open source' }));
    expect(screen.getByRole('heading', { name: 'Book source' })).toBeInTheDocument();
    fireEvent.click(within(steps).getByRole('button', { name: 'Manage cast' }));
    expect(await screen.findByRole('heading', { name: 'Character-to-voice mapping' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Classify text' })).toBeEnabled();
    expect(screen.getByText('Review the quote spans first, then classify text to assign each passage to a speaker. Your voice selections remain saved.')).toBeInTheDocument();
  });

  it.each([
    ['extracted', [], ['Add book']],
    ['review_required', [{ id: 'issue-one', reason: 'LOW_CONFIDENCE_SPEAKER', source_text: '"Hi."', chapter_id: 'chapter-one', chapter_title: 'Opening', speaker_id: null }], ['Add book', 'Extract quotes']],
    ['ready_to_render', [], ['Add book', 'Extract quotes', 'Classify text']],
  ])('marks the production steps finished for a %s book', async (state, reviewIssues, finished) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state, chapters: [], review_issues: reviewIssues, speakers: [], render_jobs: [],
        preview_jobs: [], export_jobs: [], pronunciations: [], pipeline_jobs: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const steps = await screen.findByRole('list', { name: 'Audiobook production steps' });
    const complete = within(steps).getAllByRole('listitem')
      .filter((item) => item.classList.contains('complete'))
      .map((item) => item.querySelector('strong')?.textContent);
    expect(complete).toEqual(finished);
  });

  it('lists every production phase in the book source editor with what is left and a way to it', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 6 }],
        review_issues: [{ id: 'issue-one', span_id: 'span-one', reason: 'LOW_CONFIDENCE_SPEAKER', source_text: '"Hi."', chapter_id: 'chapter-one', chapter_title: 'Opening', speaker_id: null }],
        speakers: [{ id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', status: 'active', casting: { voice_profile_id: 'voice-cloning:saved', revision: 1 }, aliases: [] },
          { id: 'ada', canonical_name: 'Ada', kind: 'character', status: 'active', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], pronunciations: [], pipeline_jobs: [],
        render_readiness: { ready: false, blockers: ['Resolve the open review issue.'] }, render_progress: { completed: 0, total: 0 },
      };
      else if (url.includes('/chapters/')) body = { id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: '', spans: [] };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    const phases = screen.getByRole('list', { name: 'Production phases' });
    const rows = within(phases).getAllByRole('listitem').filter((item) => item.parentElement === phases);
    expect(rows.map((row) => row.querySelector('strong')?.textContent)).toEqual([
      'Phase 2: Extract quotes', 'Phase 3: Classify text', 'Phase 4: Cast and direct', 'Phase 5: Preview and render', 'Phase 6: Export',
    ]);
    expect(rows.filter((row) => row.classList.contains('complete')).map((row) => row.querySelector('strong')?.textContent)).toEqual(['Phase 2: Extract quotes']);
    expect(within(rows[0]).getByText('Complete')).toBeInTheDocument();
    expect(within(rows[0]).queryByRole('list')).not.toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Phase 5: Preview and render to do' })).getByRole('button', { name: 'Resolve 1 render requirement →' })).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Phase 6: Export to do' })).getByRole('button', { name: 'Render every chapter first →' })).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole('list', { name: 'Phase 4: Cast and direct to do' })).getByRole('button', { name: 'Assign voices to 1 speaker →' }));
    expect(await screen.findByRole('heading', { name: 'Character-to-voice mapping' })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    fireEvent.click(within(screen.getByRole('list', { name: 'Phase 3: Classify text to do' })).getByRole('button', { name: 'Resolve 1 speaker review issue →' }));
    expect(await screen.findByRole('region', { name: 'Human review queue' })).toBeInTheDocument();
  });

  it('filters spans by character and saves a selected word for the whole book', async () => {
    let removed = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/spans/narration/speech-exclusions') && init?.method === 'POST') {
        removed = true;
        return new Response(JSON.stringify({ id: 'exclusion-one' }), { status: 200 });
      }
      if (url.endsWith('/speech-exclusions/exclusion-one') && init?.method === 'DELETE') {
        removed = false;
        return new Response(JSON.stringify({ restored: true }), { status: 200 });
      }
      if (url.endsWith('/projects/book-one/pronunciations') && init?.method === 'POST') {
        return new Response(JSON.stringify({ source_term: 'Ehsan', spoken_term: 'Eh-sahn', revision: 1 }),
          { status: 200, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 50 }],
        review_issues: [], speakers: [
          { id: 'narrator', canonical_name: 'Narrator', kind: 'narrator', casting: null, aliases: [] },
          { id: 'ehsan', canonical_name: 'Ehsan', kind: 'character', casting: null, aliases: [] },
        ], render_jobs: [], preview_jobs: [], export_jobs: [], pronunciations: [],
        render_progress: { completed: 0, total: 1 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'Ehsan smiled. Hello.',
        spans: [
          { id: 'narration', source_text: 'Ehsan smiled.', structural_kind: 'narration',
            speech_exclusions: removed ? [{ id: 'exclusion-one', start_offset: 0, end_offset: 5, source_text: 'Ehsan' }] : [],
            annotation: { role: 'narration', speaker_id: 'narrator', delivery: '', review_status: 'accepted' },
            speech_plan: { tts_input_text: 'Ehsan smiled.', hash: 'one', transformations: [] } },
          { id: 'dialogue', source_text: 'Hello.', structural_kind: 'dialogue',
            annotation: { role: 'dialogue', speaker_id: 'ehsan', delivery: '', review_status: 'accepted' },
            speech_plan: { tts_input_text: 'Hello.', hash: 'two', transformations: [] } },
        ],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const section = await screen.findByLabelText('Characterization spans');
    expect(section).toHaveTextContent('Ehsan smiled.');
    expect(section).toHaveTextContent('Hello.');
    expect(within(section).getAllByRole('button', { name: 'Preview' }).every((button) => button.hasAttribute('disabled'))).toBe(true);
    expect(within(section).getByText('Assign a voice to Ehsan in Cast & Voice Tools to preview audio.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter spans by character'), { target: { value: 'ehsan' } });
    expect(section).toHaveTextContent('Hello.');
    expect(section).not.toHaveTextContent('Ehsan smiled.');
    fireEvent.change(screen.getByLabelText('Filter spans by character'), { target: { value: 'all' } });
    const canonical = screen.getByLabelText('Canonical chapter text');
    const passage = canonical.querySelector('.audiobook-source-span');
    expect(passage?.firstChild).toBeTruthy();
    const range = document.createRange();
    range.setStart(passage!.firstChild!, 0);
    range.setEnd(passage!.firstChild!, 5);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    fireEvent.mouseUp(passage!);
    fireEvent.click(passage!);
    expect(screen.getByLabelText('Selected written word')).toHaveValue('Ehsan');
    fireEvent.change(screen.getByLabelText('Spoken as'), { target: { value: 'Eh-sahn' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save for whole book' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/pronunciations',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"spoken_term":"Eh-sahn"') }),
    ));
    fireEvent.click(screen.getByRole('button', { name: 'Remove from audio' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/spans/narration/speech-exclusions',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ start_offset: 0, end_offset: 5, source_text: 'Ehsan' }) }),
    ));
    expect(canonical).toHaveTextContent('Ehsan smiled.');
    fireEvent.click(await screen.findByRole('button', { name: 'Restore text' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speech-exclusions/exclusion-one', { method: 'DELETE' },
    ));
  });

  it('removes a whole chapter from the audiobook and restores it', async () => {
    let removed = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/chapters/contents/speech-exclusion') && init?.method === 'POST') {
        removed = true;
        return new Response(JSON.stringify({ id: 'exclusion-contents' }), { status: 200 });
      }
      if (url.endsWith('/speech-exclusions/exclusion-contents') && init?.method === 'DELETE') {
        removed = false;
        return new Response(JSON.stringify({ restored: true }), { status: 200 });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'ready_to_render',
        chapters: [{ id: 'contents', ordinal: 0, title: 'Table of Contents', character_count: 9, audio_removal_id: removed ? 'exclusion-contents' : null },
          { id: 'chapter-one', ordinal: 1, title: 'Chapter 1', character_count: 6, audio_removal_id: null }],
        review_issues: [], speakers: [], render_jobs: [], preview_jobs: [], export_jobs: [], pronunciations: [],
        render_progress: { completed: 0, total: 0 },
      };
      else if (url.endsWith('/projects/book-one/chapters/contents')) body = {
        id: 'contents', ordinal: 0, title: 'Table of Contents', canonical_text: 'Chapter 1',
        spans: [{ id: 'contents-line', source_text: 'Chapter 1', structural_kind: 'narration',
          speech_exclusions: removed ? [{ id: 'exclusion-contents', start_offset: 0, end_offset: 9, source_text: 'Chapter 1' }] : [],
          annotation: { role: 'narration', speaker_id: null, delivery: '', review_status: 'accepted' },
          speech_plan: { tts_input_text: removed ? '' : 'Chapter 1', hash: 'one', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove chapter from audio' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/chapters/contents/speech-exclusion', expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByRole('button', { name: 'Restore chapter' })).toBeEnabled();
    expect(screen.getByText(/This chapter is left out of the audiobook/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /removed from audio\s*Table of Contents/ })).toHaveClass('removed');
    expect(screen.getByRole('button', { name: /^02 · ready\s*Chapter 1$/ })).not.toHaveClass('removed');
    // The chapter-wide removal is not repeated on each of its spans.
    expect(screen.queryByRole('button', { name: 'Restore text' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Restore chapter' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/speech-exclusions/exclusion-contents', expect.objectContaining({ method: 'DELETE' }),
    ));
    expect(await screen.findByRole('button', { name: 'Remove chapter from audio' })).toBeEnabled();
  });

  it.each([true, false])('uses current render readiness for a manually assigned book (ready=%s)', async (ready) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/render') && init?.method === 'POST') {
        return new Response(JSON.stringify({ render_run_id: 'manual-run' }), { status: 202 });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'extracted', chapters: [], review_issues: [], speakers: [],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 1 },
        render_readiness: { ready, blockers: ready ? [] : ['Opening: Assign a voice to Ehsan before rendering.'] },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));
    const renderButton = await screen.findByRole('button', { name: 'Render book' });
    if (ready) {
      expect(renderButton).toBeEnabled();
      fireEvent.click(renderButton);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/render',
        expect.objectContaining({ method: 'POST' })));
    } else {
      expect(renderButton).toBeDisabled();
      expect(screen.getByRole('status', { name: 'Render book requirements' })).toHaveTextContent('Assign a voice to Ehsan');
    }
  });

  it('shows at the render button that a render is starting, then why it was refused', async () => {
    let refuse: () => void = () => undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/render') && init?.method === 'POST') {
        await new Promise<void>((resolve) => { refuse = resolve; });
        return new Response(JSON.stringify({ detail: 'Rendering or chapter assembly is already in progress.' }),
          { status: 409, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'fish-s2-pro', model_id: 'S2-Pro', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'ready_to_render', chapters: [], review_issues: [], speakers: [],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 1 },
        render_readiness: { ready: true, blockers: [] },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render book' }));

    expect(await screen.findByRole('button', { name: 'Starting render…' })).toBeDisabled();
    expect(screen.getByText('Checking the installed model and queuing chapters…')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/audiobook/projects/book-one/render', expect.objectContaining({ method: 'POST' })));
    refuse();
    expect(await screen.findByText(/^Render not started: .*already in progress/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render book' })).toBeEnabled();
  });

  it('offers a retry from persisted failed render state', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/render') && init?.method === 'POST') {
        return new Response(JSON.stringify({ render_run_id: 'second-run' }),
          { status: 202, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS', model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'rendering', chapters: [], review_issues: [], speakers: [],
        render_jobs: [{ id: 'failed-job', status: 'failed', progress: { generated: 1, cache_hits: 2 }, error: { message: 'GPU unavailable' } }],
        preview_jobs: [], export_jobs: [], render_progress: { completed: 3, total: 4 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Render & Export' }));
    const retry = await screen.findByRole('button', { name: 'Retry render' });
    await waitFor(() => expect(retry).toBeEnabled());
    expect(screen.getByText('Cache Hits').closest('article')).toHaveTextContent('2');
    fireEvent.click(retry);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/render',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('sha256:test-model') }),
    ));
  });

  it('locates a cast speaker in another chapter before auditioning', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/preview') && init?.method === 'POST') {
        return new Response(JSON.stringify({ job_id: 'preview-two' }), { status: 202,
          headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [{ id: 'chapter-one', ordinal: 0, title: 'First' },
          { id: 'chapter-two', ordinal: 1, title: 'Second' }], review_issues: [],
        speakers: [{ id: 'speaker-two', canonical_name: 'Ada', kind: 'character', casting: null, aliases: [] }],
        render_jobs: [], preview_jobs: [], export_jobs: [], render_progress: { completed: 0, total: 2 },
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'First', canonical_text: 'Narration.',
        spans: [{ id: 'span-one', source_text: 'Narration.', structural_kind: 'narration', annotation: null,
          speech_plan: { tts_input_text: 'Narration.', hash: 'one', transformations: [] } }],
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-two')) body = {
        id: 'chapter-two', ordinal: 1, title: 'Second', canonical_text: 'Hello.',
        spans: [{ id: 'span-two', source_text: 'Hello.', structural_kind: 'dialogue',
          annotation: { id: 'annotation-two', role: 'dialogue', speaker_id: 'speaker-two',
            speaker_candidate: null, delivery: '', review_status: 'accepted', evidence: {} },
          speech_plan: { tts_input_text: 'Hello.', hash: 'two', transformations: [] } }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200,
        headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    const find = await screen.findByRole('button', { name: 'Find span for Ada' });
    fireEvent.click(find);
    expect(await screen.findByLabelText('Canonical chapter text')).toHaveTextContent('Hello.');
    const audition = screen.getByRole('button', { name: 'Audition voice for Ada' });
    await waitFor(() => expect(audition).toBeEnabled());
    fireEvent.click(audition);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/preview',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('"span_id":"span-two"') }),
    ));
  });

  it('saves project metadata and retries an actionable failed pipeline stage', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one') && init?.method === 'PATCH') {
        return new Response(JSON.stringify({ ...project, title: 'Renamed Book' }), {
          status: 200, headers: { 'content-type': 'application/json' },
        });
      }
      if (url.endsWith('/projects/book-one/jobs/failed-analysis/retry') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          job_id: 'retry-analysis', retry_of: 'failed-analysis', type: 'audiobook.analyze',
        }), { status: 202, headers: { 'content-type': 'application/json' } });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = {
        provider_id: 'faster-qwen3-tts', model_id: 'Qwen3-TTS',
        model_revision: 'sha256:test-model',
      };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'analyzing', chapters: [], review_issues: [], speakers: [],
        render_jobs: [], preview_jobs: [], export_jobs: [], pronunciations: [],
        pipeline_jobs: [{
          id: 'failed-analysis', type: 'audiobook.analyze', status: 'failed',
          attempts: 3, max_attempts: 3, can_retry: true,
          error: { code: 'analysis_failed', message: 'classifier unavailable', retryable: false },
          progress: {},
        }],
        render_progress: { completed: 0, total: 0 },
        word_count: 1200, estimated_runtime_seconds: 480, actual_runtime_seconds: 0,
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));

    expect(await screen.findByText(/1,200 words/)).toBeInTheDocument();
    expect(screen.getByText(/analysis failed/)).toBeInTheDocument();
    expect(screen.getByText(/attempt 3\/3/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Edit project' }));
    const title = screen.getByLabelText('Title');
    fireEvent.change(title, { target: { value: 'Renamed Book' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save details' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.stringContaining('Renamed Book'),
      }),
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Retry stage' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/jobs/failed-analysis/retry',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('queues reclassification for the current manuscript before voice assignment', async () => {
    let reclassifyQueued = false;
    let reclassificationStatus = 'running';
    let cancelRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/reclassify') && init?.method === 'POST') {
        reclassifyQueued = true;
        return new Response(JSON.stringify({ job_id: 'reclassify-one', source_revision_id: 'source-one' }), {
          status: 202, headers: { 'content-type': 'application/json' },
        });
      }
      if (url.endsWith('/jobs/reclassify-one/pause') && init?.method === 'POST') {
        reclassificationStatus = 'paused';
        return new Response(JSON.stringify({ job_id: 'reclassify-one', status: 'paused', paused: true }), { status: 200 });
      }
      if (url.endsWith('/jobs/reclassify-one/resume') && init?.method === 'POST') {
        reclassificationStatus = 'running';
        return new Response(JSON.stringify({ job_id: 'reclassify-one', status: 'running', resumed: true }), { status: 200 });
      }
      if (url.endsWith('/jobs/reclassify-one/cancel') && init?.method === 'POST') {
        cancelRequests += 1;
        reclassificationStatus = cancelRequests > 1 ? 'canceled' : 'cancel_requested';
        return new Response(JSON.stringify({ job_id: 'reclassify-one', cancellation_requested: true }), { status: 200 });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, chapters: [], review_issues: [], speakers: [], render_jobs: [],
        preview_jobs: [], export_jobs: [], pronunciations: [],
        classification_rules: 'Character quotes can also be in speaker: quote format.',
        quote_extraction_rules: 'Character quotes can also be in speaker: quote format.',
        render_progress: { completed: 0, total: 0 },
        pipeline_jobs: reclassifyQueued ? [{
          id: 'reclassify-one', type: 'audiobook.analyze', status: reclassificationStatus,
          progress: { current: 3, total: 17, message: 'analyzing chapters' },
          error: null, attempts: 1, max_attempts: 3, can_retry: false,
        }] : [],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));
    expect(screen.getByRole('button', { name: 'Classify text' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Custom rules for classification (optional)'), {
      target: { value: 'New dialogue syntax.' },
    });
    expect(screen.getByRole('button', { name: 'Classify text' })).toBeDisabled();
    expect(screen.getByText('Rules changed. Extract quotes again and review the new spans before classifying.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Custom rules for classification (optional)'), {
      target: { value: 'Character quotes can also be in speaker: quote format.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Classify text' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/reclassify', expect.objectContaining({
        method: 'POST', body: JSON.stringify({ custom_rules: 'Character quotes can also be in speaker: quote format.' }),
      }),
    ));
    expect(await screen.findByLabelText('Text classification progress')).toHaveValue(18);
    expect(screen.getByText('3 / 17 spans · analyzing chapters')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Classifying…' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/jobs/reclassify-one/pause', expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/jobs/reclassify-one/resume', expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByRole('button', { name: 'Pause' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/jobs/reclassify-one/cancel', expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByRole('button', { name: 'Cancel now' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel now' }));
    await waitFor(() => expect(cancelRequests).toBe(2));
  });

  it('keeps quote extraction separate from text classification', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project,
        state: 'ingesting',
        chapters: [],
        review_issues: [],
        speakers: [],
        render_jobs: [],
        preview_jobs: [],
        export_jobs: [],
        pronunciations: [],
        render_progress: { completed: 0, total: 0 },
        pipeline_jobs: [{
          id: 'reextract-one',
          type: 'audiobook.ingest',
          reason: 'user_requested_quote_extraction',
          status: 'running',
          progress: {},
          error: null,
          attempts: 1,
          max_attempts: 3,
          can_retry: false,
        }],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit project' }));

    expect(await screen.findByRole('button', { name: 'Extracting quotes…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Classify text' })).toBeDisabled();
    expect(screen.getByText('Review the extracted quote spans first, including unwanted text, then classify text to assign speakers.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Text classification progress')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
  });

  it('updates the selected-book inspector when a different library card is selected', async () => {
    const secondBook = {
      id: 'book-two', title: 'Second Book', author: 'Second Author', language: 'en',
      state: 'ready_to_render', current_source_revision_id: null, source_format: 'epub',
      source_filename: null,
    };
    const details = [project, secondBook].map((item) => ({
      ...item, cover_asset_id: null,
      chapters: [{ id: `${item.id}-chapter`, ordinal: 0, title: 'Opening', character_count: 120 }],
      review_issues: [], speakers: [], render_jobs: [], preview_jobs: [], export_jobs: [],
      render_progress: { completed: 0, total: 0 }, pronunciations: [],
    }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith('/projects') ? { projects: [project, secondBook] }
        : details.find((item) => url.endsWith(`/projects/${item.id}`));
      if (!body) throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();

    fireEvent.click(await screen.findByRole('button', { name: 'Library' }));
    const inspector = await screen.findByRole('region', { name: 'Selected audiobook details' });
    expect(await within(inspector).findByText('The Book')).toBeInTheDocument();
    expect(within(inspector).getByText('The Author')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Select Second Book' }));
    await waitFor(() => expect(within(inspector).getByText('Second Book')).toBeInTheDocument());
    expect(within(inspector).getByText('Second Author')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select Second Book' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'List view' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Select The Book' }));
    await waitFor(() => expect(within(inspector).getByText('The Author')).toBeInTheDocument());
  });

  it('exposes the updated project tabs with working controls', async () => {
    let deleted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one') && init?.method === 'DELETE') {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: deleted ? [] : [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = { exports: [] };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'ready_to_export', cover_asset_id: 'cover-one', source_format: 'pdf', source_filename: 'the-book.pdf',
        chapters: [{ id: 'chapter-one', ordinal: 0, title: 'Opening', character_count: 240 }],
        review_issues: [], speakers: [], render_jobs: [{ id: 'render-one', chapter_id: 'chapter-one', status: 'completed', progress: {} }],
        preview_jobs: [], export_jobs: [], pronunciations: [], render_progress: { completed: 1, total: 1 },
        word_count: 48, estimated_runtime_seconds: 30,
      };
      else if (url.endsWith('/projects/book-one/chapters/chapter-one')) body = {
        id: 'chapter-one', ordinal: 0, title: 'Opening', canonical_text: 'A short opening.', spans: [],
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: 'Library' }));
    expect(await screen.findByRole('heading', { name: 'Audiobook Library' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search audiobooks' })).toBeInTheDocument();
    expect((await screen.findAllByText('the-book.pdf')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Fiction')).not.toBeInTheDocument();
    expect(screen.getByText('Source Storage')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by format' }), { target: { value: 'epub' } });
    expect(screen.queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by format' }), { target: { value: 'pdf' } });
    fireEvent.click(screen.getByRole('button', { name: 'List view' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Search audiobooks' }), { target: { value: 'The Book' } });
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument();
    const pdfDownload = await screen.findByRole('link', { name: 'Download The Book source' });
    expect(pdfDownload).toHaveAttribute('href', '/api/audiobook/projects/book-one/source/download');
    expect(pdfDownload).toHaveTextContent('Download PDF');
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    await screen.findByRole('button', { name: 'Edit project' });
    fireEvent.click(screen.getAllByRole('button', { name: /^Chapters$/ }).at(-1)!);
    expect((await screen.findAllByRole('heading', { name: 'Chapters' })).length).toBeGreaterThan(1);
    expect(screen.getByRole('textbox', { name: 'Search chapters' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Assets$/ }));
    expect(await screen.findByRole('heading', { name: 'Project Assets' })).toBeInTheDocument();
    expect(screen.getAllByText('Project cover').length).toBeGreaterThan(0);
    expect(screen.queryByText('chapter_01.jpg')).not.toBeInTheDocument();
    expect(screen.queryByText('Opening-narration.wav')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Documents$/ }));
    expect(await screen.findByRole('heading', { name: 'Project Documents' })).toBeInTheDocument();
    expect(screen.queryByText('Chapter outline (generated)')).not.toBeInTheDocument();
    expect(screen.getAllByText('the-book.pdf').length).toBeGreaterThan(0);
    expect(screen.getByText('Replace manuscript')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /New document/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Render & Export$/ }));
    expect(await screen.findByRole('heading', { name: 'Render & Export' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render book' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete audiobook' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Delete “The Book”?');
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete audiobook' }).at(-1)!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one', expect.objectContaining({ method: 'DELETE' }),
    ));
    expect(await screen.findByRole('heading', { name: 'Audiobook Library' })).toBeInTheDocument();
  });

  it('deletes a generated asset from the asset inspector after confirmation', async () => {
    let deleted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/projects/book-one/assets/asset-export') && init?.method === 'DELETE') {
        deleted = true;
        return new Response(JSON.stringify({ asset_id: 'asset-export', deleted: true, file_deleted: true }), {
          status: 200, headers: { 'content-type': 'application/json' },
        });
      }
      let body: unknown;
      if (url.endsWith('/projects')) body = { projects: [project] };
      else if (url.endsWith('/voices')) body = { voices: [] };
      else if (url.endsWith('/models/current')) body = { model_revision: 'sha256:test-model' };
      else if (url.endsWith('/projects/book-one/exports')) body = deleted ? { exports: [] } : {
        exports: [{ id: 'export-one', format: 'mp3', manifest_hash: 'hash', asset_id: 'asset-export', created_at: '2026-09-21T00:00:00Z', asset_created_at: '2026-09-21T01:00:00Z', byte_size: 3 }],
      };
      else if (url.endsWith('/projects/book-one')) body = {
        ...project, state: 'exported', cover_asset_id: 'cover-one', source_format: 'pdf', source_filename: 'A manuscript.pdf', source_size_bytes: 2048,
        source_created_at: '2026-09-19T12:00:00Z', cover_created_at: '2026-09-20T12:00:00Z',
        chapters: [], review_issues: [], speakers: [], render_jobs: [], preview_jobs: [], export_jobs: [],
        pronunciations: [], render_progress: { completed: 0, total: 0 },
      };
      else throw new Error(`unexpected API request ${url}`);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    fireEvent.click(await screen.findByRole('button', { name: /The Book/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^Assets$/ }));
    expect(await screen.findByText('audiobook.mp3')).toBeInTheDocument();
    const assetsPanel = screen.getByRole('heading', { name: 'Project Assets' }).closest('section')!;
    const cardNames = () => within(assetsPanel).getAllByRole('button')
      .filter((button) => button.classList.contains('asset-name-button'))
      .map((button) => button.textContent);
    expect(cardNames()).toEqual(['audiobook.mp3', 'Project cover', 'A manuscript.pdf']);
    expect(within(assetsPanel).getByText('2.0 KB')).toBeInTheDocument();
    expect(within(assetsPanel).getByText('3 B')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort assets' }), { target: { value: 'name' } });
    expect(cardNames()).toEqual(['A manuscript.pdf', 'audiobook.mp3', 'Project cover']);
    for (const [name, timestamp] of [
      ['Project cover', '2026-09-20T12:00:00Z'],
      ['A manuscript.pdf', '2026-09-19T12:00:00Z'],
      ['audiobook.mp3', '2026-09-21T01:00:00Z'],
    ]) {
      const card = within(assetsPanel).getByText(name).closest('article')!;
      expect(card.querySelector('time')).toHaveAttribute('datetime', timestamp);
      expect(card).toHaveTextContent(new Date(timestamp).toLocaleString());
    }
    fireEvent.click(screen.getByText('audiobook.mp3'));
    const inspector = screen.getByText('Asset details').closest('section')!;
    expect(inspector.querySelector('time')).toHaveAttribute('datetime', '2026-09-21T01:00:00Z');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Delete “audiobook.mp3”?');
    fireEvent.click(screen.getByRole('button', { name: 'Delete asset' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/audiobook/projects/book-one/assets/asset-export', expect.objectContaining({ method: 'DELETE' }),
    ));
    await waitFor(() => expect(screen.queryByText('audiobook.mp3')).not.toBeInTheDocument());
  });

});
