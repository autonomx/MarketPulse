/**
 * One fetch pipeline for the browser (WP-9.2).
 *
 * Features used to replace `window.fetch` and restore a reference captured at
 * install time, so disposing one wrapper could drop the wrappers installed
 * after it. Now a middleware registers with `registerFetchMiddleware` and gets
 * a function that removes it, in any order. Gateway calls go through
 * `pipelineFetch` (the typed client, the transport helpers and the feature
 * calls use it), which runs
 *
 *   feature middlewares (newest first) -> transport middlewares -> fetch
 *
 * Feature middleware may rewrite a request (for example to a direct gateway
 * origin) before the transport layer (the view firewall: workspace scope,
 * client and CSRF headers, request ids, 401 handling) sees it. `window.fetch`
 * itself is not replaced, so other code (libraries loading assets) uses the
 * browser's fetch.
 */

export type FetchNext = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export type FetchMiddleware = (input: RequestInfo | URL, init: RequestInit | undefined, next: FetchNext) => Promise<Response>;

type Layer = 'feature' | 'transport';

interface Entry {
  readonly name: string;
  readonly layer: Layer;
  readonly middleware: FetchMiddleware;
}

let entries: Entry[] = [];

function chain(): Entry[] {
  const features = entries.filter((entry) => entry.layer === 'feature').reverse();
  const transport = entries.filter((entry) => entry.layer === 'transport').reverse();
  return [...features, ...transport];
}

// Resolved per call, so a fetch stubbed after this module loads (tests) is used.
function terminal(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return globalThis.fetch(input, init);
}

function run(ordered: readonly Entry[], input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const step = (index: number): FetchNext => (nextInput, nextInit) => (
    index < ordered.length
      ? ordered[index].middleware(nextInput, nextInit, step(index + 1))
      : terminal(nextInput, nextInit)
  );
  return step(0)(input, init);
}

/** Fetch through every registered middleware. */
export const pipelineFetch: typeof fetch = (input, init) => run(chain(), input, init);

/** Add a middleware; the returned function removes it (idempotent). */
export function registerFetchMiddleware(
  name: string,
  middleware: FetchMiddleware,
  options: { layer?: Layer } = {},
): () => void {
  const entry: Entry = { name, layer: options.layer ?? 'feature', middleware };
  entries = [...entries, entry];
  return () => {
    entries = entries.filter((candidate) => candidate !== entry);
  };
}

/**
 * The chain below a named middleware: for a feature's own requests, which must
 * not pass through itself (or newer middleware) but still get the transport
 * layer. Before the middleware is registered, or after it is removed, this is
 * the whole chain.
 */
export function fetchBelow(name: string): FetchNext {
  return (input, init) => {
    const ordered = chain();
    const position = ordered.findIndex((entry) => entry.name === name);
    return run(position < 0 ? ordered : ordered.slice(position + 1), input, init);
  };
}

/** The fetch underneath every middleware (for calls that must skip them). */
export function baseFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return terminal(input, init);
}

export function activeFetchMiddlewares(): readonly string[] {
  return chain().map((entry) => entry.name);
}

/** Remove every middleware (tests). */
export function resetFetchPipelineForTests(): void {
  entries = [];
}
