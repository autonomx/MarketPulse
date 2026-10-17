import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { AudiobookPerformancePanel } from './AudiobookPerformancePanel';
import type { ProjectDetail } from './audiobookTypes';

const ANN = '11111111-1111-1111-1111-111111111111';

function project(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  return {
    id: 'ab:project', title: 'Book', current_source_revision_id: 'rev',
    speakers: [{ id: ANN, canonical_name: 'Ann', kind: 'character', status: 'active', aliases: [] }],
    pipeline_jobs: [],
    ...overrides,
  } as unknown as ProjectDetail;
}

function renderPanel(detail: ProjectDetail) {
  const action = vi.fn(async (task: () => Promise<unknown>) => { await task(); });
  render(<MantineProvider theme={omnixTheme} env="test"><AudiobookPerformancePanel project={detail} busy={false} action={action} /></MantineProvider>);
  return action;
}

function stubFetch() {
  const requests: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), 'http://localhost'), init);
    const text = await request.text();
    requests.push({ path: new URL(request.url).pathname, method: request.method, body: text ? JSON.parse(text) : null });
    return Response.json({ job_id: 'ab:direct:1', performance: {}, changed: true });
  }));
  return requests;
}

afterEach(() => vi.unstubAllGlobals());

describe('AudiobookPerformancePanel', () => {
  it('saves the book levels and a character\'s own levels', async () => {
    const requests = stubFetch();
    renderPanel(project());

    expect(screen.getByRole('button', { name: 'Save performance' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Book stability'), { target: { value: '0.5' } });
    fireEvent.click(screen.getByLabelText('Own performance for Ann'));
    fireEvent.change(screen.getByLabelText('Ann style exaggeration'), { target: { value: '0.9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save performance' }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({
      path: '/api/audiobook/projects/ab%3Aproject/performance', method: 'PUT',
      body: { stability: 0.5, style: 0.35, speakers: { [ANN]: { stability: 0.5, style: 0.9 } } },
    });
  });

  it('starts directing and reports the last run', async () => {
    const requests = stubFetch();
    renderPanel(project({ pipeline_jobs: [{ id: 'ab:direct:0', type: 'audiobook.direct-performance', status: 'completed',
      progress: { directed: 12, changed: 9, kept_user_delivery: 2 } }] }));

    expect(screen.getByRole('status')).toHaveTextContent('Last run: 12 lines directed, 9 updated, 2 of your own notes kept.');
    fireEvent.click(screen.getByRole('button', { name: 'Direct again' }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ path: '/api/audiobook/projects/ab%3Aproject/direct-performance', method: 'POST', body: { chapter_ids: [] } });
  });

  it('saves when to direct after classifying', async () => {
    const requests = stubFetch();
    renderPanel(project());

    const select = screen.getByLabelText('Direct performance after classifying') as HTMLSelectElement;
    expect(select.value).toBe('fish');
    fireEvent.change(select, { target: { value: 'always' } });

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({ path: '/api/audiobook/projects/ab%3Aproject/auto-direct', method: 'PUT', body: { policy: 'always' } });
  });

  it('shows progress while directing and why it failed', () => {
    stubFetch();
    const { rerender } = render(<MantineProvider theme={omnixTheme} env="test"><AudiobookPerformancePanel busy={false} action={vi.fn()}
      project={project({ pipeline_jobs: [{ id: 'j', type: 'audiobook.direct-performance', status: 'running', progress: { current: 1, total: 4, message: 'directing Chapter 2' } }] })} /></MantineProvider>);
    expect(screen.getByRole('button', { name: 'Directing…' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('directing Chapter 2 · chapter 2 of 4');

    rerender(<MantineProvider theme={omnixTheme} env="test"><AudiobookPerformancePanel busy={false} action={vi.fn()}
      project={project({ pipeline_jobs: [{ id: 'j', type: 'audiobook.direct-performance', status: 'failed', error: { message: 'no chat provider' } }] })} /></MantineProvider>);
    expect(screen.getByRole('alert')).toHaveTextContent('Directing failed: no chat provider');
  });
});
