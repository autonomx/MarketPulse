/**
 * Module runtime lifecycle (WP-9.1).
 *
 * A workspace's runtime is activated when its route is entered and disposed
 * when it is left. Everything a runtime installs (listeners, observers,
 * timers, fetch middleware, client patches) is registered with a
 * DisposableStore, which undoes it in reverse order.
 */
import type { QueryClient } from '@tanstack/react-query';

/** What a workspace runtime receives when it activates. */
export interface ModuleRuntimeContext {
  queryClient: QueryClient;
}

export interface Disposable {
  dispose(): void;
}

/** What an initializer may return: a cleanup function, a Disposable, or nothing. */
export type Cleanup = (() => void) | Disposable | void | undefined | null;

export function toDisposable(cleanup: Cleanup): Disposable {
  if (!cleanup) return { dispose: () => undefined };
  if (typeof cleanup === 'function') {
    let done = false;
    return {
      dispose: () => {
        if (done) return;
        done = true;
        cleanup();
      },
    };
  }
  return cleanup;
}

export class DisposableStore implements Disposable {
  private readonly items: Disposable[] = [];
  private disposed = false;

  get isDisposed(): boolean {
    return this.disposed;
  }

  /** Register a cleanup; after disposal it runs at once (late async activations). */
  add(cleanup: Cleanup): Disposable {
    const item = toDisposable(cleanup);
    if (this.disposed) item.dispose();
    else this.items.push(item);
    return item;
  }

  /** addEventListener that is removed on disposal. */
  listen<K extends keyof WindowEventMap>(
    target: Window, type: K, handler: (event: WindowEventMap[K]) => void, options?: boolean | AddEventListenerOptions,
  ): void;
  listen(target: EventTarget, type: string, handler: EventListener, options?: boolean | AddEventListenerOptions): void;
  listen(target: EventTarget, type: string, handler: EventListener, options?: boolean | AddEventListenerOptions): void {
    target.addEventListener(type, handler, options);
    this.add(() => target.removeEventListener(type, handler, options));
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    const items = this.items.splice(0).reverse();
    for (const item of items) {
      try {
        item.dispose();
      } catch (error) {
        console.error('[Omnix] runtime cleanup failed', error);
      }
    }
  }
}
