import { useEffect, useMemo, useState } from 'react';
import { ToolExecutionPanel as BaseToolExecutionPanel, type ToolExecutionPanelProps } from './ToolExecutionPanel';
import type { ToolExecutionRow } from './tool-execution-view';
import type { components } from '../api/generated';
import { api } from '../api/gateway';

type AssistantToolLedgerEntry = components['schemas']['AssistantToolLedgerEntry'];

type AssistantToolLedgerPayload = components['schemas']['AssistantToolLedgerPayload'];

async function loadLedger(): Promise<AssistantToolLedgerPayload> {
  const { data, response } = await api.GET('/api/assistant/tools/ledger');
  return response.ok && data ? data : { entries: [] };
}

function ledgerRows(entries: AssistantToolLedgerEntry[]): ToolExecutionRow[] {
  return entries.map((entry): ToolExecutionRow => ({
    id: entry.execution_id,
    toolCallId: entry.execution_id,
    toolName: entry.tool_id,
    label: entry.action_id,
    description: `Approval: ${entry.approval_source}${entry.state_changed ? ' · changed state' : ''}`,
    status: entry.error ? 'failed' : 'completed',
    statusLabel: entry.error ? 'Failed' : 'Completed',
    createdAt: entry.created_at,
    completedAt: entry.created_at,
    argumentsSummary: entry.input_summary,
    resultSummary: entry.result_summary,
    error: entry.error ?? undefined,
    actions: entry.error ? ['retry'] : [],
  }));
}

export function ToolExecutionPanel(props: ToolExecutionPanelProps) {
  const [entries, setEntries] = useState<AssistantToolLedgerEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    if (typeof fetch !== 'function') return undefined;
    void loadLedger()
      .then((payload) => {
        if (!cancelled) setEntries(payload.entries ?? []);
      })
      .catch(() => {
        if (!cancelled) setEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo(() => {
    const fromLedger = ledgerRows(entries);
    return fromLedger.length ? fromLedger : props.rows;
  }, [entries, props.rows]);

  return <BaseToolExecutionPanel {...props} rows={rows} />;
}

export type { ToolExecutionPanelProps };
