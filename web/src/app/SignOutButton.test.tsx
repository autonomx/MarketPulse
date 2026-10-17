import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SignOutButton } from './SignOutButton';

describe('SignOutButton', () => {
  afterEach(() => vi.restoreAllMocks());

  it('stays hidden when sign-in is not enforced', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValue(
      Response.json({ enforced: false, mode: 'local', authenticated: false }),
    );
    const { container } = render(<SignOutButton />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('revokes the session and returns to the sign-in page', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      if (String(input) === '/api/auth/session') return Response.json({ enforced: true, mode: 'local', authenticated: true });
      return new Response(null, { status: 204 });
    });
    const assign = vi.fn();
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, assign });
    render(<SignOutButton />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out of Omnix' }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/login'));
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/logout', expect.objectContaining({ method: 'POST' }));
  });
});
