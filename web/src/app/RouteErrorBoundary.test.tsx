import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Suspense, useState, type ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reportClientError, resetClientErrorReportingForTests } from './clientErrorReporter';
import { ChunkLoadError, lazyWithRetry, RouteErrorBoundary } from './RouteErrorBoundary';

const fetchMock = vi.fn(async () => new Response(null, { status: 202 }));

beforeEach(() => {
  resetClientErrorReportingForTests();
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  window.history.replaceState({}, '', '/chatbot?session=private#fragment');
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function reports(): Array<Record<string, unknown>> {
  return fetchMock.mock.calls.map((call) => JSON.parse(String((call as unknown as [string, RequestInit])[1].body)) as Record<string, unknown>);
}

function Exploding({ explode }: { explode: boolean }) {
  if (explode) throw new Error('workspace exploded');
  return <p>workspace content</p>;
}

function Harness() {
  const [explode, setExplode] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setExplode(false)}>fix</button>
      <RouteErrorBoundary resetKey="chatbot"><Exploding explode={explode} /></RouteErrorBoundary>
    </>
  );
}

describe('route error boundary', () => {
  it('shows a fallback instead of a blank app, reports it, and retries', async () => {
    render(<Harness />);

    expect(screen.getByRole('alert')).toHaveTextContent('This workspace stopped working');
    expect(screen.getByText('fix')).toBeInTheDocument();
    await waitFor(() => expect(reports()).toHaveLength(1));
    expect(reports()[0]).toMatchObject({ kind: 'render', route: '/chatbot', message: 'Error: workspace exploded' });

    fireEvent.click(screen.getByText('fix'));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('workspace content')).toBeInTheDocument();
  });

  it('offers a reload when a workspace chunk cannot be loaded, after one retry', async () => {
    const load = vi.fn(async () => {
      throw new TypeError('Failed to fetch dynamically imported module: /assets/Chat.js');
    });
    const Workspace = lazyWithRetry(load as unknown as () => Promise<{ default: ComponentType }>);

    render(<RouteErrorBoundary resetKey="chatbot"><Suspense fallback="loading"><Workspace /></Suspense></RouteErrorBoundary>);

    expect(await screen.findByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(reports()[0]).toMatchObject({ kind: 'chunk_load' }));
    expect(new ChunkLoadError('x').name).toBe('ChunkLoadError');
  });
});

describe('client error reporter', () => {
  it('sends the route without its query and only once for a repeated error', () => {
    reportClientError('error', new Error('same failure'));
    reportClientError('error', new Error('same failure'));
    expect(reports()).toHaveLength(1);
    expect(reports()[0].route).toBe('/chatbot');
    expect(JSON.stringify(reports()[0])).not.toContain('private');
  });

  it('sends at most ten reports a minute', () => {
    for (let index = 0; index < 15; index += 1) reportClientError('unhandledrejection', `failure ${index}`);
    expect(reports()).toHaveLength(10);
  });
});
