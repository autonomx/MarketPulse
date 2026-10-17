import { useRouter, type AnyRouter } from '@tanstack/react-router';
import { useCallback, useSyncExternalStore } from 'react';

/**
 * A selection kept in the URL's search parameters, so it can be linked to
 * and survives a reload (WP-9.6): the open chat session, audiobook
 * project, trading instrument. Preferences stay in localStorage.
 *
 * Inside the app it navigates through the router (replacing the history
 * entry); rendered without a router (tests, embedded views) it edits the
 * browser history directly.
 */
export function useSearchParam(name: string): [string | null, (value: string | null) => void] {
  const router = useRouter({ warn: false }) as AnyRouter | undefined;
  const subscribe = useCallback((onChange: () => void) => {
    if (router) return router.history.subscribe(onChange);
    window.addEventListener('popstate', onChange);
    localListeners.add(onChange);
    return () => {
      window.removeEventListener('popstate', onChange);
      localListeners.delete(onChange);
    };
  }, [router]);
  const value = useSyncExternalStore(subscribe, () => readSearchParam(name), () => null);
  const setValue = useCallback((next: string | null) => {
    if (readSearchParam(name) === next) return;
    if (router) {
      void router.navigate({
        to: '.',
        search: (previous: Record<string, unknown>) => ({ ...previous, [name]: next ?? undefined }),
        replace: true,
      } as never);
      return;
    }
    const url = new URL(window.location.href);
    if (next) url.searchParams.set(name, next);
    else url.searchParams.delete(name);
    window.history.replaceState(window.history.state, '', url);
    localListeners.forEach((listener) => listener());
  }, [name, router]);
  return [value, setValue];
}

const localListeners = new Set<() => void>();

/** The router writes strings that look like JSON (`"123"`) quoted; this reads them back as written. */
export function readSearchParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  const raw = new URLSearchParams(window.location.search).get(name);
  if (!raw) return null;
  if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed === 'string') return parsed || null;
    } catch {
      // Not JSON: the raw value stands.
    }
  }
  return raw;
}
