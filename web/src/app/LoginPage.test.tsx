import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LoginPage, safeNextPath } from './LoginPage';

const OPEN = { registration: 'open', guests: true, google: true, install_credential: true, min_password_length: 12 };
const signedOut = (options: Record<string, unknown> = OPEN) =>
  Response.json({ enforced: true, mode: 'local', authenticated: false, options });
const signedIn = () => Response.json({ enforced: true, mode: 'local', authenticated: true });

function renderLogin(search = '') {
  window.history.replaceState({}, '', `/login${search}`);
  return render(
    <MantineProvider>
      <LoginPage />
    </MantineProvider>,
  );
}

function mockLocationAssign() {
  const assign = vi.fn();
  vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign });
  return assign;
}

function bodyOf(fetchMock: { mock: { calls: unknown[][] } }, path: string): unknown {
  const call = fetchMock.mock.calls.find((args) => String(args[0]) === path);
  return JSON.parse(String((call?.[1] as RequestInit | undefined)?.body));
}

describe('LoginPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('keeps post-login navigation on this origin', () => {
    expect(safeNextPath('/trading?x=1')).toBe('/trading?x=1');
    expect(safeNextPath('https://evil.example')).toBe('/');
    expect(safeNextPath('//evil.example')).toBe('/');
    expect(safeNextPath('/\\evil.example')).toBe('/');
    expect(safeNextPath(null)).toBe('/');
  });

  it('signs in with email and password, staying signed in by default', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut();
      if (url === '/api/auth/password/login') return signedIn();
      throw new Error(`unexpected ${url}`);
    });
    renderLogin('?next=%2Ftrading');
    const assign = mockLocationAssign();
    fireEvent.change(await screen.findByLabelText(/email/i), { target: { value: ' ada@example.com ' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/trading'));
    expect(bodyOf(fetchMock, '/api/auth/password/login')).toEqual({
      email: 'ada@example.com',
      password: 'a long passphrase',
      remember: true,
    });
  });

  it('reports a wrong password without saying which part was wrong', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/auth/session') return signedOut();
      return new Response('{"detail":"invalid_credentials"}', { status: 401 });
    });
    renderLogin();
    fireEvent.change(await screen.findByLabelText(/email/i), { target: { value: 'ada@example.com' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("don't match an account");
  });

  it('creates an account and explains a taken email', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut();
      if (url === '/api/auth/register') return new Response('{"detail":"email_taken"}', { status: 409 });
      throw new Error(`unexpected ${url}`);
    });
    renderLogin();
    fireEvent.click(await screen.findByRole('button', { name: 'New account' }));
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'ada@example.com' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already exists');
    expect(bodyOf(fetchMock, '/api/auth/register')).toMatchObject({ email: 'ada@example.com', display_name: 'Ada', invite: null });
  });

  it('opens on account creation for an invite and carries it to Google too', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut({ ...OPEN, registration: 'invite', guests: false });
      if (url.startsWith('/api/auth/invites/check')) return Response.json({ usable: true });
      if (url === '/api/auth/register') return signedIn();
      throw new Error(`unexpected ${url}`);
    });
    renderLogin('?invite=abc123');
    const assign = mockLocationAssign();
    expect(await screen.findByText(/you were invited/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Continue with Google' })).toHaveAttribute(
      'href',
      '/api/auth/google/login?next=%2F&invite=abc123',
    );
    expect(screen.queryByRole('button', { name: 'Continue as guest' })).toBeNull();
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'sam@example.com' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/'));
    expect(bodyOf(fetchMock, '/api/auth/register')).toMatchObject({ invite: 'abc123' });
  });

  it('continues as a guest', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut();
      if (url === '/api/auth/guest') return signedIn();
      throw new Error(`unexpected ${url}`);
    });
    renderLogin('?next=%2Fchatbot');
    const assign = mockLocationAssign();
    fireEvent.click(await screen.findByRole('button', { name: 'Continue as guest' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/chatbot'));
  });

  it('hides sign-up, Google and guests when they are off', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(
      signedOut({ registration: 'closed', guests: false, google: false, install_credential: true, min_password_length: 12 }),
    );
    renderLogin();
    expect(await screen.findByText(/doesn't take new accounts/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New account' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Continue with Google' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Continue as guest' })).toBeNull();
  });

  it('still signs the owner in with the install credential', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut();
      if (url === '/api/auth/local/login') return signedIn();
      throw new Error(`unexpected ${url}`);
    });
    renderLogin('?next=%2Ftrading');
    const assign = mockLocationAssign();
    fireEvent.click(await screen.findByRole('button', { name: 'Use the install credential' }));
    fireEvent.change(screen.getByLabelText(/install credential/i), { target: { value: ' secret ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in with the credential' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/trading'));
    expect(bodyOf(fetchMock, '/api/auth/local/login')).toEqual({ credential: 'secret', remember: true });
  });

  it('offers single sign-on in OIDC mode', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(Response.json({ enforced: true, mode: 'oidc', authenticated: false }));
    renderLogin('?next=%2Fchatbot');
    const link = await screen.findByRole('link', { name: /organization/i });
    expect(link).toHaveAttribute('href', '/api/auth/oidc/login?next=%2Fchatbot');
  });

  it('explains an expired launcher link', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(signedOut());
    renderLogin('?error=login_link_expired');
    expect(await screen.findByRole('alert')).toHaveTextContent('expired');
  });

  it('explains a Google account whose email belongs to another account', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(signedOut());
    renderLogin('?error=google_email_in_use');
    expect(await screen.findByRole('alert')).toHaveTextContent('connect Google in Settings');
  });
});

describe('LoginPage in the desktop app (MarketPulse S-5)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.marketPulse;
    window.history.replaceState({}, '', '/');
  });

  it('signs in with Google through the system browser and goes on', async () => {
    const openExternal = vi.fn();
    window.marketPulse = { version: '1.0.0', platform: 'win32', setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal, requestLoginCode: vi.fn() };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return signedOut({ ...OPEN, google_desktop: true });
      if (url === '/api/auth/google/desktop/start') return Response.json({ nonce: 'n', begin_url: '/api/auth/google/desktop/begin?token=t', expires_in: 600 });
      if (url === '/api/auth/google/desktop/handoff') return Response.json({ status: 'signed_in', next: '/trading' });
      throw new Error(`unexpected ${url}`);
    });
    renderLogin('?next=%2Ftrading');
    const assign = mockLocationAssign();

    fireEvent.click(await screen.findByRole('button', { name: 'Continue with Google' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Finish signing in with Google in your browser');
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/trading'), { timeout: 4000 });
    expect(openExternal).toHaveBeenCalledWith(expect.stringContaining('/api/auth/google/desktop/begin?token=t'));
  });
});
