import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  memoryClient,
  type CompanionRolloutStage,
  type ManagedMemoryRecord,
  type MemoryCategory,
  type MemoryScope,
} from './memoryClient';
import './MemoryManagementPanel.css';
import { downloadJson } from '../../../shared/download';

const scopes: MemoryScope[] = ['global', 'workspace', 'project', 'session'];
const categories: MemoryCategory[] = ['preference', 'fact', 'project', 'relationship', 'instruction'];
const rolloutStages: CompanionRolloutStage[] = [
  'authority_only',
  'shadow',
  'read_only_pilot',
  'explicit_typed',
  'review_required',
  'automatic_assertions',
  'gentle_initiative',
  'active_initiative',
  'paralinguistic_pilot',
];

export function MemoryManagementPanel({ sessionId }: { sessionId: string | null }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState<MemoryScope | ''>('');
  const [category, setCategory] = useState<MemoryCategory | ''>('');
  const [newContent, setNewContent] = useState('');
  const [newScope, setNewScope] = useState<MemoryScope>('session');
  const [newCategory, setNewCategory] = useState<MemoryCategory>('preference');
  const [status, setStatus] = useState<string | null>(null);

  const memoryQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory', sessionId, query, scope, category], queryFn: () => memoryClient.list(sessionId ?? '', query, scope, category), enabled: Boolean(sessionId) });
  const archivedQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-archived', sessionId], queryFn: () => memoryClient.archived(sessionId ?? ''), enabled: Boolean(sessionId) });
  const automaticQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-automatic', sessionId], queryFn: () => memoryClient.recentAutomatic(sessionId ?? ''), enabled: Boolean(sessionId), refetchInterval: 10_000 });
  const usageQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-usage', sessionId], queryFn: () => memoryClient.usage(sessionId ?? ''), enabled: Boolean(sessionId), refetchInterval: 10_000 });
  const candidatesQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-candidates', sessionId], queryFn: () => memoryClient.candidates(sessionId ?? ''), enabled: Boolean(sessionId) });
  const snapshotQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-state', sessionId], queryFn: () => memoryClient.sessionState(sessionId ?? ''), enabled: Boolean(sessionId) });
  const settingsQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-settings'], queryFn: () => memoryClient.settings() });
  const metricsQuery = useQuery({ queryKey: ['feature', 'chatbot', 'memory-metrics'], queryFn: () => memoryClient.metrics(), refetchInterval: 30_000 });

  async function refreshAll(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-archived'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-automatic'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-usage'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-candidates'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-state'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-settings'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'memory-metrics'] }),
    ]);
  }

  const createMutation = useMutation({
    mutationFn: () => memoryClient.create(sessionId ?? '', { scope: newScope, category: newCategory, content: newContent.trim(), pinned: false }),
    onSuccess: async () => { setNewContent(''); setStatus('Memory saved. Refresh the active snapshot to use it in this chat.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory save failed.'),
  });
  const recordMutation = useMutation({
    mutationFn: async (input: { action: 'pin' | 'forget' | 'move' | 'edit' | 'archive' | 'restore'; record: ManagedMemoryRecord; value?: string }) => {
      if (!sessionId) throw new Error('Select a chat session first.');
      if (input.action === 'pin') return memoryClient.pin(sessionId, input.record, !input.record.pinned);
      if (input.action === 'move') return memoryClient.move(sessionId, input.record, input.value as MemoryScope);
      if (input.action === 'edit') return memoryClient.edit(sessionId, input.record, input.value ?? input.record.content);
      if (input.action === 'archive') return memoryClient.archive(sessionId, input.record);
      if (input.action === 'restore') return memoryClient.restore(sessionId, input.record);
      return memoryClient.forget(sessionId, input.record);
    },
    onSuccess: async () => { setStatus('Memory updated.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory update failed.'),
  });
  const undoMutation = useMutation({
    mutationFn: (record: ManagedMemoryRecord) => memoryClient.undo(sessionId ?? '', record),
    onSuccess: async () => { setStatus('Automatically remembered memory undone.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Undo failed.'),
  });
  const candidateMutation = useMutation({
    mutationFn: (input: { id: string; action: 'approve' | 'reject' }) => input.action === 'approve' ? memoryClient.approve(sessionId ?? '', input.id) : memoryClient.reject(sessionId ?? '', input.id),
    onSuccess: async () => { setStatus('Memory suggestion reviewed.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Suggestion review failed.'),
  });
  const refreshMutation = useMutation({
    mutationFn: () => memoryClient.refresh(sessionId ?? '', snapshotQuery.data?.snapshot_revision),
    onSuccess: async () => { setStatus('Active chat memory refreshed.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory refresh failed.'),
  });
  const settingsMutation = useMutation({
    mutationFn: (update: Parameters<typeof memoryClient.updateSettings>[0]) => memoryClient.updateSettings(update),
    onSuccess: async () => { setStatus('Memory settings saved. New server-side behavior is active immediately.'); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory settings update failed.'),
  });
  const resetMutation = useMutation({
    mutationFn: () => memoryClient.reset(sessionId ?? ''),
    onSuccess: async (result) => { setStatus(`Memory reset deleted ${result.record_count} records, ${result.candidate_count} suggestions, and ${result.snapshot_count} snapshots.`); await refreshAll(); },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory reset failed.'),
  });
  const exportMutation = useMutation({
    mutationFn: () => memoryClient.exportMemory(sessionId ?? ''),
    onSuccess: (data) => {
      downloadJson(data, `omnix-memory-${sessionId ?? 'export'}.json`);
      setStatus('Memory export downloaded.');
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Memory export failed.'),
  });

  if (!sessionId) return <section className="assistant-view-panel memory-management-panel" aria-label="Memory view"><p className="eyebrow">Omnix Assistant</p><h2>Memory</h2><p>Create or select a Chat session before managing memory.</p></section>;

  const records = memoryQuery.data?.records ?? [];
  const archived = archivedQuery.data?.records ?? [];
  const automatic = automaticQuery.data?.records ?? [];
  const candidates = candidatesQuery.data?.candidates ?? [];
  const usage = usageQuery.data?.items ?? [];
  const snapshot = snapshotQuery.data;
  const recordById = new Map([...records, ...archived].map((record) => [record.id, record]));

  return (
    <section className="assistant-view-panel memory-management-panel" aria-label="Memory view">
      <header className="memory-page-header">
        <div><p className="eyebrow">Omnix Assistant</p><h2>Memory</h2><p>Review saved memory, approve suggestions, and control what this Chat can use.</p></div>
        <div role="group" className="memory-page-stats" aria-label="Memory totals"><span><strong>{records.length}</strong> saved</span><span><strong>{archived.length}</strong> archived</span><span><strong>{candidates.length}</strong> pending</span><span><strong>{snapshot?.memory_record_count ?? 0}</strong> active</span><span><strong>{metricsQuery.data?.turns ?? 0}</strong> observed turns</span></div>
      </header>
      {status ? <p className="memory-status" role="status">{status}</p> : null}
      {automatic[0] ? <aside className="memory-status" aria-label="Automatically remembered memory"><strong>Remembered —</strong> {automatic[0].content} <button type="button" disabled={undoMutation.isPending} onClick={() => undoMutation.mutate(automatic[0])}>Undo</button></aside> : null}

      <div className="memory-overview-grid">
        <article className="memory-card memory-card-primary">
          <MemoryCardHeading icon="o" title="Active for this Chat" detail="Snapshot currently available to the assistant." />
          {snapshotQuery.isPending ? <p>Loading snapshot...</p> : snapshot ? <><div className="memory-metric-row"><span><strong>{snapshot.memory_enabled ? snapshot.memory_record_count : 0}</strong> records</span><span><strong>{snapshot.snapshot?.token_estimate ?? 0}</strong> tokens</span><span><strong>{snapshot.snapshot_revision ?? 'none'}</strong> revision</span></div><p>{snapshot.memory_enabled ? `${snapshot.memory_record_count} active records` : 'Memory is not active for this session.'}</p><p>Snapshot revision: {snapshot.snapshot_revision ?? 'none'} - Token estimate: {snapshot.snapshot?.token_estimate ?? 0}</p><button type="button" disabled={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>{refreshMutation.isPending ? 'Refreshing...' : 'Refresh active memory'}</button></> : <p>Snapshot state is unavailable.</p>}
        </article>

        <article className="memory-card memory-add-card">
          <MemoryCardHeading icon="+" title="Add explicit memory" detail="Save a deliberate note for future recall." />
          <div className="memory-field-row"><label>Scope<select aria-label="New memory scope" value={newScope} onChange={(event) => setNewScope(event.currentTarget.value as MemoryScope)}>{scopes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><label>Category<select aria-label="New memory category" value={newCategory} onChange={(event) => setNewCategory(event.currentTarget.value as MemoryCategory)}>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select></label></div>
          <label className="memory-textarea-field">Memory<textarea aria-label="New memory content" rows={4} value={newContent} onChange={(event) => setNewContent(event.currentTarget.value)} /></label>
          <div className="memory-card-actions"><button type="button" disabled={!newContent.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>Save memory</button></div>
        </article>

        <article className="memory-card memory-settings-card">
          <MemoryCardHeading icon="*" title="Memory, privacy, and rollout" detail="Controls apply immediately without deleting saved memory." />
          {settingsQuery.isPending ? <p>Loading settings...</p> : settingsQuery.data ? <><div className="memory-settings-grid">{([
            ['companion_master_enabled', 'Enable companion memory runtime'],
            ['curated_memory_enabled', 'Use approved memory in Chat'],
            ['suggestions_enabled', 'Create pending suggestions'],
            ['automatic_direct_assertion_memory', 'Automatically save direct user assertions'],
            ['proactive_memory_enabled', 'Allow proactive memory references'],
            ['paralinguistic_signals_enabled', 'Use ephemeral conversational signals'],
            ['transcript_retention_enabled', 'Allow transcript retention'],
            ['history_recall_enabled', 'Search previous conversations'],
            ['compaction_enabled', 'Compact long conversations'],
            ['hermes_sync_enabled', 'Allow Hermes synchronization'],
            ['show_memory_use_indicator', 'Show memory-use indicators'],
          ] as const).map(([key, label]) => <label className="memory-toggle-row" key={key}><input type="checkbox" checked={settingsQuery.data.settings[key]} disabled={settingsMutation.isPending || settingsQuery.data.environment_overrides.includes(key)} onChange={(event) => settingsMutation.mutate({ [key]: event.currentTarget.checked })} />{label}{settingsQuery.data.environment_overrides.includes(key) ? ' - environment controlled' : ''}</label>)}</div><label>Rollout stage<select aria-label="Companion rollout stage" value={settingsQuery.data.settings.companion_rollout_stage} disabled={settingsMutation.isPending || settingsQuery.data.environment_overrides.includes('companion_rollout_stage')} onChange={(event) => settingsMutation.mutate({ companion_rollout_stage: event.currentTarget.value as CompanionRolloutStage })}>{rolloutStages.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></label><p>Inferred memory approval remains required. Automatic direct assertions apply only to explicit user-authored claims. Diagnostics are content-free.</p></> : <p>Memory settings are unavailable.</p>}
        </article>

        <article className="memory-card">
          <MemoryCardHeading icon="⇩" title="Export and deletion" detail="Download this owner’s memory or permanently reset it." />
          <p>Export includes active, archived, and superseded records plus review candidates.</p>
          <div className="memory-card-actions"><button type="button" disabled={exportMutation.isPending} onClick={() => exportMutation.mutate()}>Export memory JSON</button><button type="button" disabled={resetMutation.isPending} onClick={() => { if (window.confirm('Permanently delete all memory for this owner? This cannot be undone.')) resetMutation.mutate(); }}>Reset all memory</button></div>
        </article>
      </div>

      {settingsQuery.data?.settings.show_memory_use_indicator ? <section className="memory-section" aria-labelledby="memory-usage-heading"><div className="memory-section-header"><div><h3 id="memory-usage-heading">Why memory was used</h3><p>Latest content-free selection explanation for this Chat.</p></div></div>{usage.length ? <div className="memory-record-grid">{usage.map((item) => <article className="memory-record-card" key={`${item.memory_id}:${item.source_revision}`}><header><h4>{recordById.get(item.memory_id)?.category ?? item.section}</h4><strong>score {item.activation_score}</strong></header><p>{recordById.get(item.memory_id)?.content ?? 'Memory content is unavailable in the current filter.'}</p><small>Reason: {item.selection_reason.replaceAll('_', ' ')} - Section: {item.section.replaceAll('_', ' ')}</small></article>)}</div> : <p className="memory-empty-state">No memory was selected for the latest observed turn.</p>}</section> : null}

      <section className="memory-section" aria-labelledby="saved-memory-heading">
        <div className="memory-section-header"><div><h3 id="saved-memory-heading">Saved memory</h3><p>Approved records Omnix can reuse when memory is enabled.</p></div></div>
        <div className="memory-filter-bar"><label>Search<input aria-label="Search saved memory" placeholder="Search content" value={query} onChange={(event) => setQuery(event.currentTarget.value)} /></label><label>Scope<select aria-label="Filter memory scope" value={scope} onChange={(event) => setScope(event.currentTarget.value as MemoryScope | '')}><option value="">All scopes</option>{scopes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><label>Category<select aria-label="Filter memory category" value={category} onChange={(event) => setCategory(event.currentTarget.value as MemoryCategory | '')}><option value="">All categories</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select></label></div>
        {memoryQuery.isPending ? <p>Loading saved memory...</p> : records.length ? <div className="memory-record-grid">{records.map((record) => <article className="memory-record-card" key={record.id}><header><h4>{record.category}</h4><strong>{record.scope}{record.pinned ? ' - pinned' : ''}</strong></header><p>{record.content}</p><small>Source: {record.source} - Trust: {record.trust_level} - Revision {record.revision}</small><div className="memory-record-actions"><button type="button" onClick={() => recordMutation.mutate({ action: 'pin', record })}>{record.pinned ? 'Unpin' : 'Pin'}</button><button type="button" onClick={() => { const value = window.prompt('Edit memory', record.content); if (value?.trim()) recordMutation.mutate({ action: 'edit', record, value }); }}>Edit</button><select aria-label={`Move ${record.content}`} value={record.scope} onChange={(event) => recordMutation.mutate({ action: 'move', record, value: event.currentTarget.value })}>{scopes.map((item) => <option key={item} value={item}>{item}</option>)}</select><button type="button" onClick={() => recordMutation.mutate({ action: 'archive', record })}>Archive</button><button type="button" onClick={() => { if (window.confirm('Forget this memory permanently?')) recordMutation.mutate({ action: 'forget', record }); }}>Forget</button></div></article>)}</div> : <p className="memory-empty-state">No saved memory matches the current filters.</p>}
      </section>

      <section className="memory-section" aria-labelledby="archived-memory-heading">
        <div className="memory-section-header"><div><h3 id="archived-memory-heading">Archived memory</h3><p>Inactive records retained for review or restoration.</p></div></div>
        {archivedQuery.isPending ? <p>Loading archived memory...</p> : archived.length ? <div className="memory-record-grid">{archived.map((record) => <article className="memory-record-card" key={record.id}><header><h4>{record.category}</h4><strong>{record.scope}</strong></header><p>{record.content}</p><small>Archived - Revision {record.revision}</small><div className="memory-record-actions"><button type="button" onClick={() => recordMutation.mutate({ action: 'restore', record })}>Restore</button><button type="button" onClick={() => { if (window.confirm('Delete this archived memory permanently?')) recordMutation.mutate({ action: 'forget', record }); }}>Delete permanently</button></div></article>)}</div> : <p className="memory-empty-state">No archived memory.</p>}
      </section>

      <section className="memory-section" aria-labelledby="pending-memory-heading">
        <div className="memory-section-header"><div><h3 id="pending-memory-heading">Pending suggestions</h3><p>Review inferred memories before they become available.</p></div></div>
        {candidatesQuery.isPending ? <p>Loading suggestions...</p> : candidates.length ? <div className="memory-record-grid">{candidates.map((candidate) => <article className="memory-record-card" key={candidate.id}><header><h4>{candidate.proposed_category}</h4><strong>{candidate.proposed_scope}</strong></header><p>{candidate.proposed_content}</p><small>Source: {candidate.source} - Confidence: {Math.round(candidate.confidence * 100)}%</small><div className="memory-record-actions"><button type="button" onClick={() => candidateMutation.mutate({ id: candidate.id, action: 'approve' })}>Approve</button><button type="button" onClick={() => candidateMutation.mutate({ id: candidate.id, action: 'reject' })}>Reject</button></div></article>)}</div> : <p className="memory-empty-state">No pending memory suggestions.</p>}
      </section>
    </section>
  );
}

function MemoryCardHeading({ detail, icon, title }: { detail: string; icon: string; title: string }) {
  return <div className="memory-card-heading"><span aria-hidden="true">{icon}</span><div><h3>{title}</h3><p>{detail}</p></div></div>;
}
