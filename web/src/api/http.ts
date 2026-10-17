import createClient, { type Client } from 'openapi-fetch';
import { ApiError, ApiTimeoutError } from './errors';
import { pipelineFetch } from './fetchPipeline';
import type { paths as CorePaths } from './generated/core';

/**
 * The typed gateway client (WP-9.3): paths, parameters, bodies and responses
 * are checked against the generated OpenAPI types. `api` knows the kernel's
 * operations; a feature makes its own client from its generated paths (PA-2.4):
 *
 *   const jobs = await unwrap(api.GET('/api/jobs', { params: { query: { limit: 50 } } }));
 *   export const tradingApi = createGatewayClient<paths>();  // features/trading/api/gateway.ts
 */
export type GatewayClient<Paths extends object = CorePaths> = Client<Paths>;

/** A Request that remembers its init, so the original body (a string or FormData) is sent unchanged. */
class GatewayRequest extends Request {
  readonly gatewayInit: RequestInit;

  constructor(input: RequestInfo | URL, init: RequestInit = {}) {
    super(input, init);
    this.gatewayInit = init;
  }
}

/** `if-match` -> `If-Match`: Headers lowercases names; calls keep their usual spelling. */
function canonicalHeaderName(name: string): string {
  return name.split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('-');
}

/**
 * openapi-fetch hands over a Request; the call is re-issued as (path, init) so the
 * fetch pipeline's middleware and the tests see the same calls as handwritten ones.
 */
function gatewayFetch(fetchImpl: () => typeof fetch) {
  return (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const sameOrigin = typeof window !== 'undefined' && url.origin === window.location.origin;
    const target = sameOrigin ? `${url.pathname}${url.search}` : request.url;
    const init = request instanceof GatewayRequest ? request.gatewayInit : {};
    const headers: Record<string, string> = {};
    new Headers(init.headers).forEach((value, name) => {
      headers[canonicalHeaderName(name)] = value;
    });
    return fetchImpl()(target, {
      method: request.method,
      ...(Object.keys(headers).length ? { headers } : {}),
      ...(init.body !== undefined ? { body: init.body } : {}),
      ...(init.signal ? { signal: init.signal } : {}),
      ...(init.cache ? { cache: init.cache } : {}),
      ...(init.keepalive ? { keepalive: init.keepalive } : {}),
    });
  };
}

export function createGatewayClient<Paths extends object = CorePaths>(options: { baseUrl?: string; fetchImpl?: typeof fetch } = {}): GatewayClient<Paths> {
  const fetchImpl = () => options.fetchImpl ?? pipelineFetch;
  return createClient<Paths>({
    baseUrl: options.baseUrl ?? (typeof window !== 'undefined' ? window.location.origin : 'http://localhost'),
    fetch: gatewayFetch(fetchImpl),
    Request: GatewayRequest,
  });
}

export const api = createGatewayClient();

type GatewayResult<T> = { data?: T; error?: unknown; response: Response };

/** The response body of a successful call; a failed call throws ApiError with the gateway's request id. */
export async function unwrap<T>(call: Promise<GatewayResult<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) {
    const body = typeof error === 'string' ? error : error === undefined ? '' : JSON.stringify(error);
    throw new ApiError(response.status, body, response.headers?.get('x-request-id') ?? undefined);
  }
  return data as T;
}

/** Sends one typed call, optionally with a timeout, and returns its body (WP-9.3). */
export async function sendGatewayCall<T>(
  send: (signal?: AbortSignal) => Promise<GatewayResult<T>>,
  options: { timeoutMs?: number; timeoutMessage?: string } = {},
): Promise<T> {
  if (!options.timeoutMs) return unwrap(send());
  const timeout = requestTimeout(options.timeoutMs, options.timeoutMessage);
  try {
    return await unwrap(send(timeout.signal));
  } catch (error) {
    throw timeout.timedOut(error);
  } finally {
    timeout.clear();
  }
}

/** unwrap() that turns a failed call into an Error with the caller's message. */
export async function unwrapAs<T>(call: Promise<GatewayResult<T>>, message: (error: ApiError) => string): Promise<T> {
  try {
    return await unwrap(call);
  } catch (error) {
    if (error instanceof ApiError) throw new Error(message(error));
    throw error;
  }
}

/**
 * unwrap() for features whose messages users and code read as
 * `<label> request failed (<status>): <detail>`.
 */
export async function unwrapLabelled<T>(call: Promise<GatewayResult<T>>, label: string): Promise<T> {
  try {
    return await unwrap(call);
  } catch (error) {
    if (error instanceof ApiError) throw new Error(`${label} request failed (${error.status}): ${error.detail}`);
    throw error;
  }
}

/** An AbortSignal that fires after `timeoutMs`, and the error to raise when it did. */
export function requestTimeout(timeoutMs: number, message?: string): { signal: AbortSignal; timedOut: (error: unknown) => unknown; clear: () => void } {
  const controller = new AbortController();
  let fired = false;
  const timer = setTimeout(() => {
    fired = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: (error) => (fired ? new ApiTimeoutError(timeoutMs, message) : error),
    clear: () => clearTimeout(timer),
  };
}
