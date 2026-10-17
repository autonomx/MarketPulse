import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountSection } from './AccountSection';

const OPTIONS = { registration: 'open', guests: true, google: true, install_credential: true, min_password_length: 12 };

function session(account: Record<string, unknown>, roles: string[] = ['owner', 'admin', 'member']) {
  return Response.json({ enforced: true, mode: 'local', authenticated: true, roles, options: OPTIONS, account });
}

const OWNER = { display_name: 'Local Omnix User', email: null, kind: 'standard', has_password: false, google_linked: false, is_owner: true };

describe('AccountSection', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('stays hidden while sign-in is off', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValue(Response.json({ enforced: false, mode: 'disabled', authenticated: false }));
    const { container } = render(<AccountSection />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('turns a guest into a full account', async () => {
    let upgraded = false;
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') {
        return session(upgraded
          ? { ...OWNER, display_name: 'Grace', email: 'grace@example.com', has_password: true, is_owner: false }
          : { display_name: 'Guest', email: null, kind: 'guest', has_password: false, google_linked: false, is_owner: false }, ['guest']);
      }
      if (url === '/api/auth/guest/upgrade') {
        upgraded = true;
        return session({});
      }
      throw new Error(`unexpected ${url}`);
    });
    render(<AccountSection />);
    const form = await screen.findByRole('form', { name: 'Create your account' });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'grace@example.com' } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByRole('status')).toHaveTextContent('still here');
    expect(form).not.toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => String(url) === '/api/auth/guest/upgrade');
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({ email: 'grace@example.com', remember: true });
  });

  it('lets the owner add an email and a first password with the install credential', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return session(OWNER);
      if (url === '/api/auth/invites') return Response.json({ invites: [] });
      if (url === '/api/auth/password') return new Response('{"detail":"invalid_credential"}', { status: 401 });
      throw new Error(`unexpected ${url}`);
    });
    render(<AccountSection />);
    expect(await screen.findByRole('form', { name: 'Add an email' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Install credential'), { target: { value: 'wrong' } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set password' }));
    expect(await screen.findByRole('status')).toHaveTextContent('not correct');
    const call = fetchMock.mock.calls.find(([url]) => String(url) === '/api/auth/password');
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ current: 'wrong', new_password: 'a long passphrase' });
  });

  it('connects Google by sending the browser to Google', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return session({ ...OWNER, email: 'me@example.com', has_password: true });
      if (url === '/api/auth/invites') return Response.json({ invites: [] });
      if (url === '/api/auth/google/link?next=%2Fsettings') return Response.json({ url: 'https://accounts.google.com/o/oauth2/auth?x' });
      throw new Error(`unexpected ${url}`);
    });
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign });
    render(<AccountSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Google' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://accounts.google.com/o/oauth2/auth?x'));
  });

  it('gives an admin a single-use invite link', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/auth/session') return session({ ...OWNER, email: 'me@example.com', has_password: true, google_linked: true });
      if (url === '/api/auth/invites' && init?.method === 'POST') {
        return Response.json({ invite: 'tok', path: '/login?invite=tok', expires_at: '2026-10-13T00:00:00Z' }, { status: 201 });
      }
      if (url === '/api/auth/invites') return Response.json({ invites: [] });
      throw new Error(`unexpected ${url}`);
    });
    render(<AccountSection />);
    expect(await screen.findByText(/Google is connected/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create invite link' }));
    expect(await screen.findByText(`${window.location.origin}/login?invite=tok`)).toBeInTheDocument();
  });
});
