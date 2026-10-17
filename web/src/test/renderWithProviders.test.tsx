import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, stubGateway } from './renderWithProviders';

afterEach(() => {
  vi.unstubAllGlobals();
});

function Profile() {
  const query = useQuery({ queryKey: ['profile'], queryFn: async () => (await fetch('/api/profile')).json() as Promise<{ name: string }> });
  return <p>{query.data?.name ?? 'loading'}</p>;
}

describe('renderWithProviders', () => {
  it('renders with a query client and answers stubbed gateway calls', async () => {
    const fetchMock = stubGateway({ 'GET /api/profile': () => ({ name: 'Ada' }) });
    renderWithProviders(<Profile />);
    expect(await screen.findByText('Ada')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('answers unstubbed calls with 404 so no test reaches a server', async () => {
    stubGateway({});
    const response = await fetch('/api/anything', { method: 'POST' });
    expect(response.status).toBe(404);
  });

  it('renders inside a router when given a route', async () => {
    renderWithProviders(<Link to="/settings">Settings</Link>, { route: '/chatbot' });
    expect(await screen.findByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings');
  });
});
