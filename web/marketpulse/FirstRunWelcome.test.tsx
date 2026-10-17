import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FirstRunWelcome } from './FirstRunWelcome';

const OWNER = { enforced: true, mode: 'local', authenticated: true, account: { has_password: false } };

function mockLocation() {
  const assign = vi.fn();
  vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign });
  return assign;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.marketPulse;
});

describe('first-run welcome (roadmap §4.7)', () => {
  it('saves the email, then the first password without a credential, then opens the charts', async () => {
    const calls: Array<[string, unknown]> = [];
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.body) calls.push([url, JSON.parse(String(init.body))]);
      if (url === '/api/auth/session') return Response.json(OWNER);
      if (url === '/api/auth/email') return new Response(null, { status: 204 });
      if (url === '/api/auth/password') return Response.json({ signed_out_sessions: 0 });
      throw new Error(`unexpected ${url}`);
    });
    const assign = mockLocation();
    render(<MantineProvider><FirstRunWelcome /></MantineProvider>);

    fireEvent.change(screen.getByLabelText(/Your name/), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: ' ada@example.com ' } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a long first passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/trading'));
    expect(calls).toEqual([
      ['/api/auth/email', { email: 'ada@example.com', display_name: 'Ada' }],
      ['/api/auth/password', { current: null, new_password: 'a long first passphrase' }],
    ]);
  });

  it('offers a new sign-in code when the launcher session is too old', async () => {
    const requestLoginCode = vi.fn().mockResolvedValue(true);
    window.marketPulse = { version: '1', platform: 'win32', setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal: vi.fn(), requestLoginCode };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/auth/session') return Response.json(OWNER);
      if (url === '/api/auth/email') return new Response(null, { status: 204 });
      return Response.json({ detail: 'recent_sign_in_required' }, { status: 401 });
    });
    mockLocation();
    render(<MantineProvider><FirstRunWelcome /></MantineProvider>);

    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: 'ada@example.com' } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'a long first passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Get a new sign-in code below');
    fireEvent.click(screen.getByRole('button', { name: 'Get a new sign-in code' }));
    expect(requestLoginCode).toHaveBeenCalledTimes(1);
  });

  it('sends an owner who already has a password to the charts', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(Response.json({ ...OWNER, account: { has_password: true } }));
    const assign = mockLocation();
    render(<MantineProvider><FirstRunWelcome /></MantineProvider>);

    await waitFor(() => expect(assign).toHaveBeenCalledWith('/trading'));
  });
});
