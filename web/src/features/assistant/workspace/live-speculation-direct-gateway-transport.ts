import { fetchBelow, registerFetchMiddleware, type FetchNext } from '../../../api/fetchPipeline';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

const MIDDLEWARE = 'live-speculation-direct-gateway';
const SPECULATION_PATH = /^\/api\/live\/speculation(?:\/|$)/;
const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages\/stream$/;
const DEFAULT_DIRECT_GATEWAY_ORIGIN = 'http://127.0.0.1:8000';

type LocationLike = Pick<Location, 'hostname' | 'port' | 'origin'>;
type EnvLike = Record<string, string | boolean | number | undefined>;

let removeMiddleware: (() => void) | null = null;

export function initializeLiveSpeculationDirectGatewayTransport(): () => void {
  if (typeof window === 'undefined' || typeof window.fetch !== 'function') {
    return () => undefined;
  }
  if (removeMiddleware) return () => undefined;
  const remove = registerFetchMiddleware(MIDDLEWARE, directLiveGatewayFetch);
  removeMiddleware = remove;
  return () => {
    remove();
    if (removeMiddleware === remove) removeMiddleware = null;
  };
}

export async function directLiveGatewayFetch(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  next: FetchNext = fetchBelow(MIDDLEWARE),
): Promise<Response> {
  const fetchImpl = next;
  const directUrl = resolveDirectLiveGatewayUrl(input, init);
  if (!directUrl) return fetchImpl(input, withLiveVoiceCallAffinity(input, init));

  const startedAt = now();
  try {
    const response = await fetchImpl(directUrl, init);
    dispatchPerformance(stageFor(input, 'response'), {
      directGateway: true,
      directOrigin: new URL(directUrl).origin,
      elapsedMs: now() - startedAt,
      status: response.status,
    });
    return response;
  } catch (error: unknown) {
    if (init?.signal?.aborted) throw error;
    const acceptedLiveChat = isChatStreamPath(input);
    dispatchPerformance(
      acceptedLiveChat ? 'live_chat_direct_gateway_failed' : stageFor(input, 'fallback'),
      {
        directGateway: true,
        elapsedMs: now() - startedAt,
        error: error instanceof Error ? error.name : typeof error,
      },
    );
    // Speculation is private and side-effect free, so a failed direct attempt can
    // safely retry through same-origin. Accepted chat persists the user turn;
    // retrying after an ambiguous network/CORS failure could duplicate it.
    if (acceptedLiveChat) throw error;
    return fetchImpl(input, init);
  }
}

export function withLiveVoiceCallAffinity(input: RequestInfo | URL, init?: RequestInit): RequestInit | undefined {
  if ((init?.method ?? 'GET').toUpperCase() !== 'POST' || !isChatStreamPath(input)
    || !isLiveVoiceChatBody(init?.body)) return init;
  const headers = new Headers(init?.headers);
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : '';
  const path = new URL(rawUrl, window.location.origin).pathname;
  const sessionId = CHAT_STREAM_PATH.exec(path)?.[1];
  const callId = sessionId ? decodeURIComponent(sessionId) : liveVoiceTurnId(init?.body);
  headers.set('X-Omnix-Call-Id', encodeURIComponent(callId));
  return { ...init, headers };
}

// Compatibility alias retained for tests/imports added during the speculation rollout.
export const directSpeculationFetch = directLiveGatewayFetch;

export function resolveDirectLiveGatewayUrl(
  input: RequestInfo | URL,
  init?: RequestInit,
  locationLike: LocationLike = window.location,
  env: EnvLike = importMetaEnv(),
): string | null {
  return resolveDirectSpeculationUrl(input, locationLike, env)
    ?? resolveDirectLiveChatUrl(input, init, locationLike, env);
}

export function resolveDirectSpeculationUrl(
  input: RequestInfo | URL,
  locationLike: LocationLike = window.location,
  env: EnvLike = importMetaEnv(),
): string | null {
  // Request instances may contain a one-shot body stream. Keep them untouched;
  // all latency-critical speculation calls currently use string paths.
  if (input instanceof Request) return null;
  if (!directGatewayEnabled(locationLike, env)) return null;

  const rawUrl = typeof input === 'string' || input instanceof URL
    ? input.toString()
    : '';
  const url = new URL(rawUrl, locationLike.origin);
  if (url.origin !== locationLike.origin || !SPECULATION_PATH.test(url.pathname)) {
    return null;
  }

  const configuredOrigin = stringEnv(env, 'VITE_LIVE_SPECULATION_GATEWAY_ORIGIN');
  const directOrigin = normalizeOrigin(configuredOrigin ?? DEFAULT_DIRECT_GATEWAY_ORIGIN);
  if (!directOrigin || directOrigin === locationLike.origin) return null;
  return `${directOrigin}${url.pathname}${url.search}${url.hash}`;
}

export function resolveDirectLiveChatUrl(
  input: RequestInfo | URL,
  init?: RequestInit,
  locationLike: LocationLike = window.location,
  env: EnvLike = importMetaEnv(),
): string | null {
  // ChatbotWorkspace sends the live voice turn as a string URL + JSON body.
  // Avoid cloning Request body streams in this synchronous hot-path resolver.
  if (input instanceof Request) return null;
  if (!liveChatDirectGatewayEnabled(locationLike, env)) return null;

  const method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'POST' || !isLiveVoiceChatBody(init?.body)) return null;

  const rawUrl = typeof input === 'string' || input instanceof URL
    ? input.toString()
    : '';
  const url = new URL(rawUrl, locationLike.origin);
  if (url.origin !== locationLike.origin || !CHAT_STREAM_PATH.test(url.pathname)) {
    return null;
  }

  const configuredOrigin = stringEnv(env, 'VITE_LIVE_CHAT_GATEWAY_ORIGIN')
    ?? stringEnv(env, 'VITE_LIVE_SPECULATION_GATEWAY_ORIGIN');
  const directOrigin = normalizeOrigin(configuredOrigin ?? DEFAULT_DIRECT_GATEWAY_ORIGIN);
  if (!directOrigin || directOrigin === locationLike.origin) return null;
  return `${directOrigin}${url.pathname}${url.search}${url.hash}`;
}

export function directGatewayEnabled(
  locationLike: LocationLike,
  env: EnvLike = importMetaEnv(),
): boolean {
  return localDirectGatewayEnabled(
    locationLike,
    booleanEnv(env, 'VITE_LIVE_SPECULATION_DIRECT_GATEWAY'),
  );
}

export function liveChatDirectGatewayEnabled(
  locationLike: LocationLike,
  env: EnvLike = importMetaEnv(),
): boolean {
  return localDirectGatewayEnabled(
    locationLike,
    booleanEnv(env, 'VITE_LIVE_CHAT_DIRECT_GATEWAY'),
  );
}

function localDirectGatewayEnabled(
  locationLike: LocationLike,
  explicit: boolean | undefined,
): boolean {
  if (explicit === false) return false;
  const hostname = locationLike.hostname.trim().toLowerCase();
  const localHost = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
  if (!localHost) return false;
  if (explicit === true) return true;
  // Default only for the local Vite dev/preview origins. Production and packaged
  // clients continue using the normal same-origin gateway path.
  return locationLike.port === '5173' || locationLike.port === '4173';
}

function isLiveVoiceChatBody(body: BodyInit | null | undefined): boolean {
  return Boolean(liveVoiceTurnId(body));
}

function liveVoiceTurnId(body: BodyInit | null | undefined): string {
  if (typeof body !== 'string' || !body.trim()) return '';
  try {
    const parsed = JSON.parse(body) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '';
    const turnId = (parsed as Record<string, unknown>).live_voice_turn_id;
    return typeof turnId === 'string' ? turnId.trim() : '';
  } catch {
    return '';
  }
}

function isChatStreamPath(input: RequestInfo | URL): boolean {
  if (input instanceof Request) return false;
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : '';
  try {
    return CHAT_STREAM_PATH.test(new URL(rawUrl, window.location.origin).pathname);
  } catch {
    return false;
  }
}

function stageFor(input: RequestInfo | URL, suffix: 'response' | 'fallback'): string {
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : '';
  if (rawUrl.includes('/api/chat/sessions/') && rawUrl.includes('/messages/stream')) {
    return `live_chat_direct_gateway_${suffix}`;
  }
  const tts = rawUrl.includes('/tts-prefetch');
  return `${tts ? 'tts_speculative' : 'llm_speculation'}_direct_gateway_${suffix}`;
}

function normalizeOrigin(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.origin;
  } catch {
    return null;
  }
}

function importMetaEnv(): EnvLike {
  return ((import.meta as unknown as { env?: EnvLike }).env ?? {}) as EnvLike;
}

function stringEnv(env: EnvLike, key: string): string | undefined {
  const value = env[key];
  if (value === undefined || value === false) return undefined;
  const text = String(value).trim();
  return text || undefined;
}

function booleanEnv(env: EnvLike, key: string): boolean | undefined {
  const value = env[key];
  if (value === undefined) return undefined;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  const normalized = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'on', 'enabled'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off', 'disabled'].includes(normalized)) return false;
  return undefined;
}

function dispatchPerformance(stage: string, detail: Record<string, unknown>): void {
  if (typeof window === 'undefined') return;
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...detail });
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}
