import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { OmnixRunCard } from './OmnixRunCardCore';
import { fixture } from '../../../test/fixture';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { assistantApiClient } from '../api/assistantClient';

function renderCard(metadata: Record<string, unknown>) {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <OmnixRunCard metadata={metadata} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('OmnixRunCard activity history', () => {
  it('shows the true tool-call total while rendering only the latest 40 until expanded', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-long-history',
      status: 'running',
      desired_state: 'running',
      revision: 1,
      usage: {
        input_tokens: 0,
        output_tokens: 0,
        input_tokens_reported: false,
        output_tokens_reported: false,
      },
      last_error: null,
      spec: {
        profile: 'coding',
        task: 'Inspect many files',
        evidence_policy: { requirements: [] },
      },
    } as never);

    const events = Array.from({ length: 45 }, (_, index) => ({
      event_id: `tool-event-${index}`,
      run_id: 'run-long-history',
      sequence: index + 1,
      event_type: 'tool.started',
      payload: {
        tool_call_id: `tool-${index}`,
        tool: 'read',
        args: { path: `src/file-${index}.ts` },
      },
      created_at: `2026-09-10T04:10:${String(index).padStart(2, '0')}Z`,
    }));

    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue(events as never);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-long-history',
      evaluated_at: '2026-09-10T04:11:00Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-long-history',
        status: 'running',
        profile: 'coding',
        task: 'Inspect many files',
        revision: 1,
      },
    });

    expect(await screen.findByText('45 total tool calls')).toBeTruthy();
    expect(screen.getByText('40 tool calls')).toBeTruthy();
    expect(screen.queryByText('src/file-0.ts')).toBeNull();
    expect(screen.getByText('src/file-44.ts')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Show earlier activity (5 tool calls)' }));

    expect(screen.getByText('45 total tool calls')).toBeTruthy();
    expect(screen.getByText('45 tool calls')).toBeTruthy();
    expect(screen.getByText('src/file-0.ts')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show latest 40 activity items' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Show latest 40 activity items' }));

    expect(screen.getByText('40 tool calls')).toBeTruthy();
    expect(screen.queryByText('src/file-0.ts')).toBeNull();
  });

  it('follows event pages so the total includes calls after the first 500 events', async () => {
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockImplementation(async (_runId, afterSequence = 0) => {
      const start = afterSequence === 0 ? 0 : 500;
      const count = start === 0 ? 500 : 7;
      return fixture(Array.from({ length: count }, (_, index) => ({
        event_id: `paged-tool-event-${start + index}`,
        run_id: 'run-paged-history',
        sequence: start + index + 1,
        event_type: 'tool.started',
        payload: {
          tool_call_id: `paged-tool-${start + index}`,
          tool: 'read',
          args: { path: `src/paged-file-${start + index}.ts` },
        },
        created_at: '2026-09-10T04:10:00Z',
      })));
    });

    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-paged-history',
      status: 'running',
      desired_state: 'running',
      revision: 1,
      usage: {
        input_tokens: 0,
        output_tokens: 0,
        input_tokens_reported: false,
        output_tokens_reported: false,
      },
      last_error: null,
      spec: {
        profile: 'coding',
        task: 'Inspect paged history',
        evidence_policy: { requirements: [] },
      },
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-paged-history',
      evaluated_at: '2026-09-10T04:11:00Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-paged-history',
        status: 'running',
        profile: 'coding',
        task: 'Inspect paged history',
        revision: 1,
      },
    });

    expect(await screen.findByText('507 total tool calls')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show earlier activity (467 tool calls)' })).toBeTruthy();
  });
});
