import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ResearchCredentialSettings } from './ResearchCredentialSettings';

const credentialStatus = {
  providers: [
    {
      provider: 'brave' as const,
      configured: false,
      source: 'missing' as const,
      editable: true,
      key_suffix: null,
    },
    {
      provider: 'tavily' as const,
      configured: false,
      source: 'missing' as const,
      editable: true,
      key_suffix: null,
    },
  ],
  legacy_environment_key: false,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('ResearchCredentialSettings', () => {
  it('captures the API key before React clears the event currentTarget', async () => {
    const fetchMock = vi.fn(async () => Response.json(credentialStatus));
    vi.stubGlobal('fetch', fetchMock);

    render(<ResearchCredentialSettings />);

    const [input] = await screen.findAllByPlaceholderText('Enter API key');
    fireEvent.change(input, { target: { value: 'brave-test-key' } });

    expect(input).toHaveValue('brave-test-key');
    fireEvent.click(screen.getAllByRole('button', { name: 'Save key' })[0]);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/assistant/research/credentials', expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ provider: 'brave', api_key: 'brave-test-key' }),
      }));
    });
  });
});
