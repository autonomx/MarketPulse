import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { omnixEventClient, RESYNC_EVENT, type OmnixEventConnectionStatus } from './eventClient';

/** Job lifecycle events on `/events`; a resync means events were missed. */
export const JOB_EVENT_NAMES = ['job.created', 'job.updated', 'job.completed', 'job.failed', 'job.canceled', RESYNC_EVENT] as const;

/** The `/events` connection state. */
export function useEventConnectionStatus(): OmnixEventConnectionStatus {
  const [status, setStatus] = useState(() => omnixEventClient.getStatus());
  useEffect(() => omnixEventClient.subscribeStatus(setStatus), []);
  return status;
}

/** Whether job lifecycle events are arriving; when not, job views fall back to polling. */
export function useJobEventsOpen(): boolean {
  return useEventConnectionStatus().state === 'open';
}

/**
 * Refetches `queryKeys` when a job lifecycle event arrives: for any job, or
 * only for `jobId` when given (a resync always refetches).
 */
export function useJobEventRefresh(queryKeys: QueryKey[], { jobId, enabled = true }: { jobId?: string | null; enabled?: boolean } = {}) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled) return undefined;
    const unsubscribes = JOB_EVENT_NAMES.map((eventName) => omnixEventClient.subscribe<{ job_id?: unknown }>(eventName, (payload) => {
      if (jobId && eventName !== RESYNC_EVENT && payload?.job_id !== jobId) return;
      for (const queryKey of queryKeys) void queryClient.invalidateQueries({ queryKey });
    }));
    return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
  }, [enabled, jobId, queryClient, queryKeys]);
}
