 
import { registerFetchMiddleware } from '../api/fetchPipeline';
import type { OmnixModuleId } from './modules';

let removeFirewall: (() => void) | null = null;
let previousWebSocket: typeof window.WebSocket | null = null;
let previousEventSource: typeof window.EventSource | null = null;

/**
 * What the firewall needs from a module manifest. The app passes its own module
 * list (MarketPulse W-1), so this file never imports every feature's manifest.
 */
export interface ViewModuleScope {
  readonly id: string;
  readonly route: string;
  readonly apiPrefixes: readonly string[];
}

// Longest route first, so /voice-cloning is not taken for /voice.
let routeModules: ReadonlyArray<readonly [string, OmnixModuleId]> = [];

// These are the API families each workspace is allowed to use. Keeping this
// list at the browser boundary prevents a globally installed controller from
// silently reaching another workspace's backend while the user is navigating.
let moduleApiPrefixes: Partial<Record<OmnixModuleId, readonly string[]>> = {};

/** Sets the modules whose routes and API families the firewall knows; called by installViewApiFirewall. */
export function configureViewModules(modules: readonly ViewModuleScope[]): void {
  routeModules = [...modules]
    .sort((left, right) => right.route.length - left.route.length)
    .map((module) => [module.route, module.id as OmnixModuleId] as const);
  moduleApiPrefixes = Object.fromEntries(modules.map((module) => [module.id, module.apiPrefixes]));
}

function pathMatchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** The module whose route contains `pathname`; null outside every workspace (no fallback, WP-9.7). */
export function moduleIdFromPathname(pathname: string): OmnixModuleId | null {
  const normalized = pathname.split('?', 1)[0].replace(/\/+$/u, '') || '/';
  return routeModules.find(([route]) => normalized === route || normalized.startsWith(`${route}/`))?.[1] ?? null;
}

export function activeViewModule(): OmnixModuleId | null {
  if (typeof window === 'undefined') return null;
  return moduleIdFromPathname(window.location.pathname);
}

export function setActiveViewModule(moduleId: OmnixModuleId | null): void {
  if (typeof document === 'undefined') return;
  if (moduleId) document.documentElement.dataset.omnixActiveModule = moduleId;
  else delete document.documentElement.dataset.omnixActiveModule;
}

export function isActiveView(moduleId: OmnixModuleId): boolean {
  return activeViewModule() === moduleId;
}

export function apiPath(input: RequestInfo | URL): string {
  const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  try {
    const base = typeof window === 'undefined' ? 'http://localhost/' : window.location.href;
    return new URL(rawUrl, base).pathname;
  } catch {
    return rawUrl.split('?', 1)[0];
  }
}

const CLIENT_ERRORS_PATH = '/api/client-errors';

export function isApiAllowedForView(pathname: string, moduleId: OmnixModuleId | null): boolean {
  if (!pathname.startsWith('/api/')) return true;
  // Sign-in, session state and error reports belong to the shell, not to any workspace.
  if (AUTH_API_PATTERN.test(pathname) || pathMatchesPrefix(pathname, CLIENT_ERRORS_PATH)) return true;
  if (!moduleId) return false;
  return (moduleApiPrefixes[moduleId] ?? []).some((prefix) => pathMatchesPrefix(pathname, prefix));
}

function blockedApiResponse(pathname: string, moduleId: OmnixModuleId | null): Response {
  return new Response(JSON.stringify({
    code: 'VIEW_API_SCOPE_BLOCKED',
    detail: 'The active workspace cannot call this API family.',
    active_view: moduleId,
    request_path: pathname,
  }), {
    status: 403,
    headers: {
      'content-type': 'application/json',
      'x-omnix-view-api-blocked': 'true',
    },
  });
}

// Prefix match for the sign-in API family (/api/auth and below).
const AUTH_API_PATTERN = /^\/api\/auth(?:\/|$)/u;
const CSRF_COOKIE = 'omnix_csrf';
export const LOGIN_PATH = '/login';
let loginRedirectPending = false;

export function readCookie(name: string, source: string = typeof document === 'undefined' ? '' : document.cookie): string | null {
  const matches = source
    .split(';')
    .map((part) => part.trim())
    .filter((part) => part.startsWith(`${name}=`))
    .map((part) => decodeURIComponent(part.slice(name.length + 1)));
  return matches.length === 1 && matches[0] ? matches[0] : null;
}

function isGatewayPath(pathname: string): boolean {
  return ['/api', '/events', '/ready', '/health'].some((prefix) => pathMatchesPrefix(pathname, prefix));
}

function requestUrl(input: RequestInfo | URL): URL {
  const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
  return new URL(rawUrl, window.location.href);
}

export function loginLocation(current: Pick<Location, 'pathname' | 'search' | 'hash'> = window.location): string {
  const next = `${current.pathname}${current.search}${current.hash}`;
  return next && next !== '/' ? `${LOGIN_PATH}?next=${encodeURIComponent(next)}` : LOGIN_PATH;
}

// A 401 from the gateway means the browser session ended or was never
// established; send the user to sign in and come back afterwards.
function redirectWhenUnauthenticated(input: RequestInfo | URL, response: Response): Response {
  if (response.status !== 401 || typeof window === 'undefined') return response;
  const url = requestUrl(input);
  if (url.origin !== window.location.origin || !isGatewayPath(url.pathname)) return response;
  if (AUTH_API_PATTERN.test(url.pathname)) return response;
  if (pathMatchesPrefix(window.location.pathname, LOGIN_PATH) || loginRedirectPending) return response;
  loginRedirectPending = true;
  window.location.assign(loginLocation());
  return response;
}

// 32 hex characters. getRandomValues, unlike randomUUID, also exists on
// plain-HTTP LAN origins.
function newRequestId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function withClientHeader(input: RequestInfo | URL, init?: RequestInit): RequestInit | undefined {
  const request = typeof Request !== 'undefined' && input instanceof Request ? input : undefined;
  const url = requestUrl(input);
  if (url.origin !== window.location.origin || !isGatewayPath(url.pathname)) return init;
  const headers = new Headers(init?.headers ?? request?.headers);
  // The gateway logs the call under this id and returns it (WP-10.2).
  if (!headers.has('X-Request-ID')) headers.set('X-Request-ID', newRequestId());
  const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers.set('X-Omnix-Client', 'web');
    // Double-submit CSRF token for cookie-authenticated sessions (WP-4.1).
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers.set('X-Omnix-CSRF', csrf);
  }
  return { ...init, headers };
}

/**
 * The browser's API boundary: one transport middleware in the fetch pipeline
 * (scope to the active workspace, client/CSRF headers and request ids, sign-in
 * redirect on 401), plus scoped WebSocket and EventSource constructors, for
 * the given modules.
 */
export function installViewApiFirewall(modules: readonly ViewModuleScope[]): void {
  configureViewModules(modules);
  if (typeof window === 'undefined' || typeof window.fetch !== 'function') return;
  if (!removeFirewall) {
    removeFirewall = registerFetchMiddleware('view-api-firewall', async (input, init, next) => {
      const pathname = apiPath(input);
      const moduleId = activeViewModule();
      if (!isApiAllowedForView(pathname, moduleId)) return blockedApiResponse(pathname, moduleId);
      const response = await next(input, withClientHeader(input, init));
      return redirectWhenUnauthenticated(input, response);
    }, { layer: 'transport' });
  }

  if (!previousWebSocket) {
    const NativeWebSocket = window.WebSocket;
    if (typeof NativeWebSocket === 'function') {
      previousWebSocket = NativeWebSocket;
      class ScopedWebSocket extends NativeWebSocket {
        constructor(url: string | URL, protocols?: string | string[]) {
          const pathname = apiPath(String(url));
          if (!isApiAllowedForView(pathname, activeViewModule())) {
            throw new DOMException('The active workspace cannot open this API socket.', 'SecurityError');
          }
          super(url, protocols);
        }
      }
      window.WebSocket = ScopedWebSocket;
    }

    const NativeEventSource = window.EventSource;
    if (typeof NativeEventSource === 'function') {
      previousEventSource = NativeEventSource;
      class ScopedEventSource extends NativeEventSource {
        constructor(url: string | URL, eventSourceInitDict?: EventSourceInit) {
          const pathname = apiPath(String(url));
          if (!isApiAllowedForView(pathname, activeViewModule())) {
            throw new DOMException('The active workspace cannot open this API stream.', 'SecurityError');
          }
          super(url, eventSourceInitDict);
        }
      }
      window.EventSource = ScopedEventSource;
    }
  }
}

export function resetViewApiFirewallForTests(): void {
  removeFirewall?.();
  removeFirewall = null;
  if (typeof window !== 'undefined' && previousWebSocket) window.WebSocket = previousWebSocket;
  if (typeof window !== 'undefined' && previousEventSource) window.EventSource = previousEventSource;
  previousWebSocket = null;
  previousEventSource = null;
  loginRedirectPending = false;
}
