import type { components } from '../api/generated';
import { unwrapAs } from '../../../api/http';
import { api } from '../api/gateway';
export type MemoryScope = 'global' | 'workspace' | 'project' | 'session';
export type MemoryCategory = 'preference' | 'fact' | 'project' | 'relationship' | 'instruction';
export type CompanionRolloutStage =
  | 'authority_only'
  | 'shadow'
  | 'read_only_pilot'
  | 'explicit_typed'
  | 'review_required'
  | 'automatic_assertions'
  | 'gentle_initiative'
  | 'active_initiative'
  | 'paralinguistic_pilot';

export type ManagedMemoryRecord = components['schemas']['MemoryRecord'];

export type ManagedMemoryCandidate = components['schemas']['app__conversation__memory_contracts__MemoryCandidate'];

export type MemoryCandidateReviewResult = ManagedMemoryRecord | ManagedMemoryCandidate;

export type ManagedMemoryList = components['schemas']['MemoryListResponse'];

export type ManagedMemoryCandidateList = components['schemas']['MemoryCandidateListResponse'];

export interface SessionMemorySnapshotItem {
  memory_record_id: string;
  record_revision: number;
  content: string;
  active: boolean;
  invalidation_reason?: string | null;
}

export type AssistantMemoryRuntimeSettings = components['schemas']['AssistantMemoryRuntimeSettings'];

export type AssistantMemoryRuntimeStatus = components['schemas']['AssistantMemoryRuntimeStatus'];

export type CompanionMemoryMetrics = components['schemas']['CompanionMemoryMetrics'];

export type MemoryUsageItem = components['schemas']['MemoryUsageItem'];

export type MemoryUsageResponse = components['schemas']['MemoryUsageResponse'];

export type MemoryExportResponse = components['schemas']['MemoryExportResponse'];

export type MemoryResetResponse = components['schemas']['MemoryResetResponse'];

export type SessionMemoryState = components['schemas']['SessionMemoryState'];

function memory<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  return unwrapAs(call, (error) => error.body || `Memory request failed with status ${error.status}.`);
}

const memoryPath = (record: ManagedMemoryRecord) => ({ memory_id: record.id });
const revision = (sessionId: string, record: ManagedMemoryRecord) => ({ session_id: sessionId, expected_revision: record.revision });

export const memoryClient = {
  list(sessionId: string, query = '', scope: MemoryScope | '' = '', category: MemoryCategory | '' = ''): Promise<ManagedMemoryList> {
    return memory(api.GET('/api/assistant/memory', {
      params: { query: { session_id: sessionId, ...(query ? { query } : {}), ...(scope ? { scope } : {}), ...(category ? { category } : {}) } },
    }));
  },
  archived(sessionId: string): Promise<ManagedMemoryList> {
    return memory(api.GET('/api/assistant/memory/archived', { params: { query: { session_id: sessionId } } }));
  },
  recentAutomatic(sessionId: string): Promise<components['schemas']['RecentAutomaticMemoryResponse']> {
    return memory(api.GET('/api/assistant/memory/recent-automatic', { params: { query: { session_id: sessionId } } }));
  },
  usage(sessionId: string): Promise<MemoryUsageResponse> {
    return memory(api.GET('/api/assistant/memory/usage', { params: { query: { session_id: sessionId } } }));
  },
  exportMemory(sessionId: string): Promise<MemoryExportResponse> {
    return memory(api.GET('/api/assistant/memory/export', { params: { query: { session_id: sessionId } } }));
  },
  reset(sessionId: string): Promise<MemoryResetResponse> {
    return memory(api.POST('/api/assistant/memory/reset', { params: { query: { session_id: sessionId } } }));
  },
  create(sessionId: string, input: { scope: MemoryScope; category: MemoryCategory; content: string; pinned: boolean }): Promise<ManagedMemoryRecord> {
    return memory(api.POST('/api/assistant/memory', { body: { session_id: sessionId, ...input } }));
  },
  edit(sessionId: string, record: ManagedMemoryRecord, content: string): Promise<ManagedMemoryRecord> {
    return memory(api.PATCH('/api/assistant/memory/{memory_id}', { params: { path: memoryPath(record) }, body: { ...revision(sessionId, record), content } }));
  },
  pin(sessionId: string, record: ManagedMemoryRecord, pinned: boolean): Promise<ManagedMemoryRecord> {
    const request = { params: { path: memoryPath(record) }, body: revision(sessionId, record) };
    return memory(pinned
      ? api.POST('/api/assistant/memory/{memory_id}/pin', request)
      : api.POST('/api/assistant/memory/{memory_id}/unpin', request));
  },
  move(sessionId: string, record: ManagedMemoryRecord, targetScope: MemoryScope): Promise<ManagedMemoryRecord> {
    return memory(api.POST('/api/assistant/memory/{memory_id}/move', {
      params: { path: memoryPath(record) },
      body: { ...revision(sessionId, record), target_scope: targetScope },
    }));
  },
  archive(sessionId: string, record: ManagedMemoryRecord): Promise<ManagedMemoryRecord> {
    return memory(api.POST('/api/assistant/memory/{memory_id}/archive', { params: { path: memoryPath(record) }, body: revision(sessionId, record) }));
  },
  restore(sessionId: string, record: ManagedMemoryRecord): Promise<ManagedMemoryRecord> {
    return memory(api.POST('/api/assistant/memory/{memory_id}/restore', { params: { path: memoryPath(record) }, body: revision(sessionId, record) }));
  },
  undo(sessionId: string, record: ManagedMemoryRecord): Promise<components['schemas']['ForgetMemoryResponse']> {
    return memory(api.POST('/api/assistant/memory/{memory_id}/undo', { params: { path: memoryPath(record) }, body: revision(sessionId, record) }));
  },
  forget(sessionId: string, record: ManagedMemoryRecord): Promise<components['schemas']['ForgetMemoryResponse']> {
    return memory(api.DELETE('/api/assistant/memory/{memory_id}', {
      params: { path: memoryPath(record), query: revision(sessionId, record) },
    }));
  },
  candidates(sessionId: string): Promise<ManagedMemoryCandidateList> {
    return memory(api.GET('/api/assistant/memory/candidates/pending', { params: { query: { session_id: sessionId } } }));
  },
  approve(sessionId: string, candidateId: string): Promise<MemoryCandidateReviewResult> {
    return memory(api.POST('/api/assistant/memory/candidates/{candidate_id}/approve', {
      params: { path: { candidate_id: candidateId } },
      body: { session_id: sessionId, pinned: false },
    }));
  },
  reject(sessionId: string, candidateId: string): Promise<MemoryCandidateReviewResult> {
    return memory(api.POST('/api/assistant/memory/candidates/{candidate_id}/reject', {
      params: { path: { candidate_id: candidateId } },
      body: { session_id: sessionId, pinned: false },
    }));
  },
  sessionState(sessionId: string): Promise<SessionMemoryState> {
    return memory(api.GET('/api/chat/sessions/{session_id}/memory', { params: { path: { session_id: sessionId } } }));
  },
  refresh(sessionId: string, expectedRevision?: number | null): Promise<SessionMemoryState> {
    return memory(api.POST('/api/chat/sessions/{session_id}/memory/refresh', {
      params: { path: { session_id: sessionId } },
      body: { expected_snapshot_revision: expectedRevision ?? null, token_budget: 4000 },
    }));
  },
  settings(): Promise<AssistantMemoryRuntimeStatus> {
    return memory(api.GET('/api/assistant/memory/settings'));
  },
  updateSettings(update: Partial<AssistantMemoryRuntimeSettings>): Promise<AssistantMemoryRuntimeStatus> {
    return memory(api.POST('/api/assistant/memory/settings', { body: update }));
  },
  metrics(): Promise<CompanionMemoryMetrics> {
    return memory(api.GET('/api/assistant/memory/metrics'));
  },
};