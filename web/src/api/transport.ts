import { ApiError } from './errors';
import { pipelineFetch } from './fetchPipeline';

/**
 * Gateway calls the typed JSON client (api/http.ts) does not cover: binary
 * uploads, streamed responses (Server-Sent Events and chunked bodies) and
 * asset bytes. Routes and their media types are listed in
 * docs/architecture/api-transport-exceptions.md. Like the typed client, these
 * go through the fetch pipeline, so its middleware applies.
 */

type GatewayPath = `/api/${string}` | `/events${string}`;

function withQuery(path: GatewayPath, query?: Record<string, string | number | boolean | null | undefined>): string {
  const entries = Object.entries(query ?? {}).filter(([, value]) => value !== undefined && value !== null && value !== '');
  if (!entries.length) return path;
  const search = new URLSearchParams(entries.map(([key, value]) => [key, String(value)]));
  return `${path}${path.includes('?') ? '&' : '?'}${search.toString()}`;
}

async function failed(response: Response): Promise<never> {
  const body = await response.text().catch(() => '');
  throw new ApiError(response.status, body, response.headers?.get('x-request-id') ?? undefined);
}

/** POSTs file bytes (or nothing) as the request body and returns the JSON response, if any. */
export async function uploadBinary<T>(
  path: GatewayPath,
  body: Blob | null,
  options: { query?: Record<string, string | number | boolean | null | undefined>; contentType?: string; method?: 'POST' | 'PUT' } = {},
): Promise<T> {
  const response = await pipelineFetch(withQuery(path, options.query), {
    method: options.method ?? 'POST',
    ...(body ? { headers: { 'Content-Type': options.contentType || body.type || 'application/octet-stream' }, body } : {}),
  });
  if (!response.ok) return failed(response);
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** An ApiError from these calls as `<label> failed with status <status>.` */
export function statusError(label: string) {
  return (error: unknown): never => {
    throw error instanceof ApiError ? new Error(`${label} failed with status ${error.status}.`) : error;
  };
}

/**
 * Opens a streamed response (SSE or a chunked body) and returns it once the
 * status is OK; the caller reads `response.body`. A JSON `body` is sent as such.
 */
export async function openStream(
  path: GatewayPath,
  init: { method?: 'GET' | 'POST'; body?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<Response> {
  const hasBody = init.body !== undefined;
  const response = await pipelineFetch(path, {
    method: init.method ?? (hasBody ? 'POST' : 'GET'),
    headers: { ...(hasBody ? { 'Content-Type': 'application/json' } : {}), ...(init.headers ?? {}) },
    ...(hasBody ? { body: JSON.stringify(init.body) } : {}),
    ...(init.signal ? { signal: init.signal } : {}),
  });
  if (!response.ok) return failed(response);
  return response;
}

/** Fetches asset or file bytes (images, audio, Live2D models, preview files); blob: and data: URLs work too. */
export async function fetchBytes(
  path: GatewayPath | `blob:${string}` | `data:${string}` | string,
  init: { signal?: AbortSignal; headers?: Record<string, string>; cache?: RequestCache } = {},
): Promise<Response> {
  const response = await pipelineFetch(path, init);
  if (!response.ok) return failed(response);
  return response;
}

/**
 * fetch for the live speech coordinator, which issues its own streamed
 * speculation requests and reads them incrementally.
 */
export const liveStreamFetch: typeof fetch = (input, init) => pipelineFetch(input, init);
