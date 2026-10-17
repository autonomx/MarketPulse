import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthRequestError } from '../api/authClient';
import { desktopGoogleSignIn } from './desktopGoogle';

const START = { nonce: 'n0nce', begin_url: '/api/auth/google/desktop/begin?token=t0ken', expires_in: 600 };

afterEach(() => vi.restoreAllMocks());

describe('desktop Google sign-in (MarketPulse S-5)', () => {
  it('opens the begin link in the system browser and polls with the nonce until signed in', async () => {
    const answers = [Response.json({ status: 'pending', next: '/trading' }, { status: 202 }), Response.json({ status: 'signed_in', next: '/trading' })];
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === '/api/auth/google/desktop/start') return Response.json(START);
      if (url === '/api/auth/google/desktop/handoff') {
        expect(JSON.parse(String(init?.body))).toEqual({ nonce: 'n0nce' });
        return answers.shift() ?? Response.json({ detail: 'desktop_sign_in_expired' }, { status: 410 });
      }
      throw new Error(`unexpected ${url}`);
    });
    const openExternal = vi.fn();

    const handed = await desktopGoogleSignIn({ openExternal }, { next: '/trading', remember: true }, 5);

    expect(handed).toEqual({ status: 'signed_in', next: '/trading' });
    expect(openExternal).toHaveBeenCalledWith(`${window.location.origin}/api/auth/google/desktop/begin?token=t0ken`);
    expect(openExternal.mock.calls[0][0]).not.toContain('n0nce');
    const started = fetchMock.mock.calls.find((call) => String(call[0]) === '/api/auth/google/desktop/start');
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({ next: '/trading', invite: null, remember: true, link: false });
  });

  it('reports a refused sign-in', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => (
      String(input).endsWith('/start') ? Response.json(START) : Response.json({ detail: 'registration_closed' }, { status: 409 })
    ));

    const failure = desktopGoogleSignIn({ openExternal: vi.fn() }, { next: '/' }, 5);

    await expect(failure).rejects.toBeInstanceOf(AuthRequestError);
    await expect(failure).rejects.toMatchObject({ status: 409, detail: 'registration_closed' });
  });
});
