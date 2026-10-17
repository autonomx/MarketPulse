import { Component, lazy, type ComponentType, type LazyExoticComponent, type ReactNode } from 'react';
import { reportClientError } from './clientErrorReporter';

/** A failed dynamic import: usually a deployment replaced the chunk, so a reload fixes it. */
export class ChunkLoadError extends Error {
  constructor(cause: unknown) {
    super(cause instanceof Error ? cause.message : 'A part of the app could not be loaded.');
    this.name = 'ChunkLoadError';
  }
}

export function isChunkLoadError(error: unknown): boolean {
  if (error instanceof ChunkLoadError) return true;
  const message = error instanceof Error ? error.message : String(error ?? '');
  return /dynamically imported module|Importing a module script failed|Loading chunk .* failed/i.test(message);
}

/** React.lazy that retries a failed import once before failing with ChunkLoadError. */
// Same constraint as React.lazy, so it accepts any component.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithRetry<T extends ComponentType<any>>(load: () => Promise<{ default: T }>): LazyExoticComponent<T> {
  return lazy(async () => {
    try {
      return await load();
    } catch {
      try {
        return await load();
      } catch (error) {
        throw new ChunkLoadError(error);
      }
    }
  });
}

type FallbackProps = { error: unknown; onRetry: () => void };

export function RouteErrorFallback({ error, onRetry }: FallbackProps) {
  const chunk = isChunkLoadError(error);
  return (
    <section className="workspace-panel route-error" role="alert">
      <h2>{chunk ? 'This part of Omnix needs a reload' : 'This workspace stopped working'}</h2>
      <p>
        {chunk
          ? 'A newer version may have been deployed. Reloading the page loads it.'
          : 'An unexpected error stopped this workspace. The rest of the app still works.'}
      </p>
      <p className="route-error-detail">{error instanceof Error ? error.message : String(error)}</p>
      <div className="route-error-actions">
        {chunk
          ? <button type="button" onClick={() => window.location.reload()}>Reload</button>
          : <button type="button" onClick={onRetry}>Try again</button>}
      </div>
    </section>
  );
}

type BoundaryProps = { children: ReactNode; resetKey?: string };
type BoundaryState = { error: unknown; failed: boolean };

/** Keeps a failing workspace from blanking the app; reports the error and offers a retry. */
export class RouteErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, failed: false };

  static getDerivedStateFromError(error: unknown): BoundaryState {
    return { error, failed: true };
  }

  componentDidCatch(error: unknown): void {
    reportClientError(isChunkLoadError(error) ? 'chunk_load' : 'render', error);
  }

  componentDidUpdate(previous: BoundaryProps): void {
    // Navigating to another workspace clears the failure.
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ error: null, failed: false });
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return <RouteErrorFallback error={this.state.error} onRetry={() => this.setState({ error: null, failed: false })} />;
  }
}
