import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ResearchHandoffSettings } from './ResearchHandoffSettings';

const STATUS = {
  repository: 'autonomx/trading-research-data', ref: 'main', transport: 'http', token_configured: false,
  token_masked: '', token_source: 'missing', token_editable: true,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('research handoff settings (Omnix S-7)', () => {
  it('shows the repository and saves a token without ever showing it', async () => {
    const bodies: unknown[] = [];
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const request = typeof input === 'object' && 'method' in input ? input : null;
      if ((request?.method ?? init?.method) === 'PUT') {
        bodies.push(request ? await request.json() : JSON.parse(String(init?.body)));
        return Response.json({ ...STATUS, token_configured: true, token_masked: '***1234', token_source: 'os_protected_store' });
      }
      return Response.json(STATUS);
    });
    render(<ResearchHandoffSettings />);

    expect(await screen.findByText('autonomx/trading-research-data@main')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Research repository token'), { target: { value: 'github_pat_secret1234' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save token' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Token saved.'));
    expect(bodies).toEqual([{ token: 'github_pat_secret1234' }]);
    expect(screen.getByText('Saved (***1234)')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('github_pat_secret1234')).toBeNull();
  });

  it('tells a non-owner it is owner only', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(Response.json({ detail: 'permission_denied:trading:control' }, { status: 403 }));
    render(<ResearchHandoffSettings />);

    expect(await screen.findByRole('status')).toHaveTextContent('Only the owner');
  });
});
