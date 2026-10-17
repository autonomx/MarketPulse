/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { registerFetchMiddleware, resetFetchPipelineForTests, pipelineFetch } from '../api/fetchPipeline';
import { moduleManifests } from './modules';
import {
  activeViewModule,
  configureViewModules,
  installViewApiFirewall,
  isApiAllowedForView,
  loginLocation,
  moduleIdFromPathname,
  readCookie,
  resetViewApiFirewallForTests,
} from './viewApiScope';

describe('view API scope', () => {
  beforeEach(() => {
    configureViewModules(moduleManifests);
  });

  afterEach(() => {
    resetViewApiFirewallForTests();
    resetFetchPipelineForTests();
    window.history.replaceState({}, '', '/chatbot');
  });

  it('resolves the most specific module route', () => {
    expect(moduleIdFromPathname('/voice-cloning')).toBe('voice-cloning');
    expect(moduleIdFromPathname('/voice-cloning/settings')).toBe('voice-cloning');
    expect(moduleIdFromPathname('/trading')).toBe('trading');
    // No fallback: an unknown route belongs to no workspace (WP-9.7).
    expect(moduleIdFromPathname('/unknown')).toBeNull();
  });

  it('only permits trading API families in the trading view', () => {
    expect(isApiAllowedForView('/api/trading/bars', 'trading')).toBe(true);
    expect(isApiAllowedForView('/api/trading-room/bars', 'trading')).toBe(false);
    expect(isApiAllowedForView('/api/chat/sessions', 'trading')).toBe(false);
    expect(isApiAllowedForView('/api/voice-library', 'trading')).toBe(false);
  });

  it('allows global market-data credentials from the settings view', () => {
    expect(isApiAllowedForView('/api/trading/market-data/providers/coinmarketcap/credentials', 'settings')).toBe(true);
    expect(isApiAllowedForView('/api/trading/bars', 'settings')).toBe(false);
  });

  it('allows the local-folder picker from the chatbot view', () => {
    expect(isApiAllowedForView('/api/agent-runs/workspace-picker', 'chatbot')).toBe(true);
    expect(isApiAllowedForView('/api/agent-runs/workspace-picker', 'trading')).toBe(false);
  });

  it('blocks off-view network calls without invoking the browser fetch', async () => {
    window.history.replaceState({}, '', '/trading');
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);

    const blocked = await pipelineFetch('/api/chat/sessions');
    expect(blocked.status).toBe(403);
    expect(blocked.headers.get('x-omnix-view-api-blocked')).toBe('true');
    expect(delegate).not.toHaveBeenCalled();

    const allowed = await pipelineFetch('/api/trading/bars');
    expect(allowed.ok).toBe(true);
    expect(delegate).toHaveBeenCalledTimes(1);
  });

  it('follows browser navigation without reinstalling the firewall', async () => {
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);

    window.history.replaceState({}, '', '/trading');
    expect(activeViewModule()).toBe('trading');
    expect((await pipelineFetch('/api/rpg/turns')).status).toBe(403);

    window.history.replaceState({}, '', '/rpg');
    expect(activeViewModule()).toBe('rpg');
    expect((await pipelineFetch('/api/rpg/turns')).ok).toBe(true);
  });

  it('scopes requests that feature middleware rewrites to the active workspace', async () => {
    window.history.replaceState({}, '', '/trading');
    const rawFetch = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = rawFetch;
    installViewApiFirewall(moduleManifests);
    // A leftover assistant middleware cannot reach the chat API from the trading workspace.
    const remove = registerFetchMiddleware('assistant', (_input, init, next) => next('/api/chat/sessions', init));

    const blocked = await pipelineFetch('/api/trading/paper/accounts');
    remove();
    const allowed = await pipelineFetch('/api/trading/paper/accounts');

    expect(blocked.status).toBe(403);
    expect(allowed.status).toBe(200);
    expect(rawFetch).toHaveBeenCalledTimes(1);
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('adds the client header to same-origin %s', async (method) => {
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('/api/chat/sessions', { method, headers: { 'X-Existing': 'preserved' } });
    const init = delegate.mock.calls[0][1];
    expect(new Headers(init?.headers).get('X-Omnix-Client')).toBe('web');
    expect(new Headers(init?.headers).get('X-Existing')).toBe('preserved');
  });

  it('preserves Request headers and recognizes its method', async () => {
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    const request = new Request(`${window.location.origin}/api/chat/sessions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    await pipelineFetch(request);
    expect(delegate.mock.calls[0][0]).toBe(request);
    const headers = new Headers(delegate.mock.calls[0][1]?.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-Omnix-Client')).toBe('web');
  });

  it('does not add headers to foreign-origin mutations; safe reads get only a request id', async () => {
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('https://external.example/api/chat/sessions', { method: 'POST' });
    await pipelineFetch('/api/chat/sessions');
    const foreign = new Headers(delegate.mock.calls[0][1]?.headers);
    expect(foreign.has('X-Omnix-Client')).toBe(false);
    expect(foreign.has('X-Request-ID')).toBe(false);
    const read = new Headers(delegate.mock.calls[1][1]?.headers);
    expect(read.has('X-Omnix-Client')).toBe(false);
    expect(read.get('X-Request-ID')).toMatch(/^[0-9a-f]{32}$/);
  });

  it('gives each gateway call its own request id and keeps a caller-supplied one', async () => {
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('/api/chat/sessions', { method: 'POST' });
    await pipelineFetch('/api/chat/sessions', { method: 'POST' });
    await pipelineFetch('/api/chat/sessions', { headers: { 'X-Request-ID': 'caller-request-0001' } });
    const ids = delegate.mock.calls.map((call) => new Headers(call[1]?.headers).get('X-Request-ID'));
    expect(ids[0]).not.toBe(ids[1]);
    expect(ids[2]).toBe('caller-request-0001');
  });

  it('adds headers to trading requests', async () => {
    window.history.replaceState({}, '', '/trading');
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('/api/trading/paper/accounts', { method: 'POST' });
    expect(new Headers(delegate.mock.calls[0][1]?.headers).get('X-Omnix-Client')).toBe('web');
  });
});

describe('view API scope sign-in integration', () => {
  afterEach(() => {
    resetViewApiFirewallForTests();
    document.cookie = 'omnix_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/chatbot');
  });

  it('lets every workspace reach the sign-in API', () => {
    expect(isApiAllowedForView('/api/auth/session', 'trading')).toBe(true);
    expect(isApiAllowedForView('/api/auth/logout', 'audiobook')).toBe(true);
    expect(isApiAllowedForView('/api/authx', 'trading')).toBe(false);
  });

  it('echoes the CSRF cookie on same-origin mutations only', async () => {
    document.cookie = 'omnix_csrf=csrf-123; path=/';
    const delegate = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ok: true }));
    window.fetch = delegate;
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('/api/chat/sessions', { method: 'POST' });
    await pipelineFetch('https://external.example/api/chat/sessions', { method: 'POST' });
    await pipelineFetch('/api/chat/sessions');
    expect(new Headers(delegate.mock.calls[0][1]?.headers).get('X-Omnix-CSRF')).toBe('csrf-123');
    expect(new Headers(delegate.mock.calls[1][1]?.headers).has('X-Omnix-CSRF')).toBe(false);
    expect(new Headers(delegate.mock.calls[2][1]?.headers).has('X-Omnix-CSRF')).toBe(false);
  });

  it('refuses ambiguous duplicate CSRF cookies', () => {
    expect(readCookie('omnix_csrf', 'omnix_csrf=a; other=1')).toBe('a');
    expect(readCookie('omnix_csrf', 'omnix_csrf=a; omnix_csrf=b')).toBeNull();
    expect(readCookie('omnix_csrf', 'x_omnix_csrf=a')).toBeNull();
  });

  it('sends unauthenticated gateway responses to the sign-in page once', async () => {
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      assign,
      pathname: '/chatbot',
      search: '?session=1',
      hash: '',
    });
    window.fetch = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', { status: 401 }));
    installViewApiFirewall(moduleManifests);
    expect((await pipelineFetch('/api/chat/sessions')).status).toBe(401);
    await pipelineFetch('/api/chat/sessions');
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith('/login?next=%2Fchatbot%3Fsession%3D1');
  });

  it('does not redirect for sign-in API responses', async () => {
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign, pathname: '/chatbot' });
    window.fetch = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', { status: 401 }));
    installViewApiFirewall(moduleManifests);
    await pipelineFetch('/api/auth/local/login', { method: 'POST' });
    expect(assign).not.toHaveBeenCalled();
  });

  it('builds the sign-in location with the return path', () => {
    expect(loginLocation({ pathname: '/', search: '', hash: '' })).toBe('/login');
    expect(loginLocation({ pathname: '/trading', search: '?a=1', hash: '#x' })).toBe('/login?next=%2Ftrading%3Fa%3D1%23x');
  });
});

describe('view API scope with an injected module list (MarketPulse W-1)', () => {
  afterEach(() => {
    resetViewApiFirewallForTests();
    resetFetchPipelineForTests();
    configureViewModules(moduleManifests);
  });

  it('knows only the modules it was given', () => {
    configureViewModules([{ id: 'trading', route: '/trading', apiPrefixes: ['/api/trading'] }]);

    expect(moduleIdFromPathname('/trading/chart')).toBe('trading');
    expect(moduleIdFromPathname('/chatbot')).toBeNull();
    expect(isApiAllowedForView('/api/trading/bars', 'trading')).toBe(true);
    expect(isApiAllowedForView('/api/chat/sessions', 'chatbot')).toBe(false);
    expect(isApiAllowedForView('/api/auth/session', null)).toBe(true);
  });
});
