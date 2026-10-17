import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CharacterHermesPanel } from './CharacterHermesPanel';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel() {
  const client = createTestQueryClient();
  return render(<QueryClientProvider client={client}><CharacterHermesPanel characterId="maya" /></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe('CharacterHermesPanel', () => {
  it('uses an explicit character owner for review-first import', async () => {
    const requests: Array<{ path: string; method?: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(input.toString(), 'http://localhost').pathname;
      requests.push({ path, method: init?.method });
      return Response.json({
        enabled: true,
        available: true,
        character_id: 'maya',
        memory_dir: '/tmp/hermes/characters/maya',
        imported_candidate_ids: ['candidate:one'],
        exported_memory_ids: [],
        skipped_reasons: [],
      });
    }));

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Import for review' }));

    await waitFor(() => expect(requests).toEqual([
      { path: '/api/characters/maya/hermes/import', method: 'POST' },
    ]));
    expect(await screen.findByRole('status')).toHaveTextContent('1 pending suggestion');
  });

  it('reports disabled deployment policy without claiming an import', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      enabled: false,
      available: false,
      character_id: 'maya',
      memory_dir: '/tmp/hermes/characters/maya',
      imported_candidate_ids: [],
      exported_memory_ids: [],
      skipped_reasons: ['character_sync_disabled'],
    })));

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Export approved character memory' }));

    expect(await screen.findByRole('status')).toHaveTextContent('disabled by deployment policy');
  });
});
