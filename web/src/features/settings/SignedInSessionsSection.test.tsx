import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthRequestError } from '../../api/authClient';
import { SignedInSessionsSection } from './SignedInSessionsSection';

const client = vi.hoisted(() => ({
  fetchAuthSession: vi.fn(),
  listAuthSessions: vi.fn(),
  revokeAuthSessions: vi.fn(),
}));

vi.mock('../../api/authClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/authClient')>()),
  ...client,
}));

const session = (id: string, current: boolean) => ({
  id, current, auth_method: 'local', created_at: '2026-10-03T08:00:00Z', last_seen_at: '2026-10-03T09:00:00Z', expires_at: '2026-10-10T08:00:00Z',
});

describe('signed-in sessions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.listAuthSessions.mockResolvedValue([session('aaaaaaaaaaaaaaaa', true), session('bbbbbbbbbbbbbbbb', false)]);
  });

  it('stays hidden while sign-in is off', async () => {
    client.fetchAuthSession.mockResolvedValue({ enforced: false, mode: 'disabled', authenticated: false, roles: [] });
    const { container } = render(<SignedInSessionsSection />);
    await waitFor(() => expect(client.fetchAuthSession).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(client.listAuthSessions).not.toHaveBeenCalled();
  });

  it('signs out the other sessions after the credential is entered again', async () => {
    client.fetchAuthSession.mockResolvedValue({ enforced: true, mode: 'local', authenticated: true, roles: ['owner'] });
    client.revokeAuthSessions.mockResolvedValue(1);
    render(<SignedInSessionsSection />);

    expect(await screen.findByText(/This browser/)).toBeInTheDocument();
    const all = screen.getByRole('button', { name: 'Sign out all other sessions' });
    expect(all).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/install credential/), { target: { value: 'secret' } });
    fireEvent.click(all);

    await waitFor(() => expect(client.revokeAuthSessions).toHaveBeenCalledWith({ session_id: null, credential: 'secret' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Signed out 1 session.');
  });

  it('explains a refused credential', async () => {
    client.fetchAuthSession.mockResolvedValue({ enforced: true, mode: 'local', authenticated: true, roles: ['owner'] });
    client.revokeAuthSessions.mockRejectedValue(new AuthRequestError(401));
    render(<SignedInSessionsSection />);

    fireEvent.change(await screen.findByLabelText(/install credential/), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(client.revokeAuthSessions).toHaveBeenCalledWith({ session_id: 'bbbbbbbbbbbbbbbb', credential: 'wrong' }));
    expect(await screen.findByRole('status')).toHaveTextContent('That password (or install credential) is not correct.');
  });
});
