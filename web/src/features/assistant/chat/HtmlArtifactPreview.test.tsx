import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HtmlArtifactPreviews } from './HtmlArtifactPreview';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('HtmlArtifactPreviews', () => {
  it('renders only HTML files in a sandboxed iframe with download and fullscreen controls', () => {
    render(<HtmlArtifactPreviews runId="run 1" paths={['quiz/index.html', 'src/app.tsx']} />);

    const frame = screen.getByTitle('Preview of index.html');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(frame.getAttribute('src')).toContain('/api/agent-runs/run%201/workspace-preview/quiz/index.html');
    expect(screen.getByRole('link', { name: 'Download index.html' }).getAttribute('href')).toContain('download=true');
    expect(screen.queryByText('app.tsx')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Open index.html fullscreen' }));
    expect(screen.getByRole('dialog', { name: 'Fullscreen preview of index.html' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close fullscreen preview' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('loads source on demand without executing it in the Omnix DOM', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => '<button id="answer">Answer</button>',
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<HtmlArtifactPreviews runId="run-source" paths={['quiz.html']} />);

    fireEvent.click(screen.getByRole('button', { name: 'Show code' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('<button id="answer">Answer</button>')).toBeTruthy();
    expect(screen.queryByTitle('Preview of quiz.html')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(screen.getByTitle('Preview of quiz.html')).toBeTruthy();
  });
});
