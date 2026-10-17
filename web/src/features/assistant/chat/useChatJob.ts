import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { omnixApiClient } from '../../../api/client';
import { useJobEventRefresh, useJobEventsOpen } from '../../../events/useJobEvents';
import { POLL_INTERVALS_MS } from '../../../shared/timers';
import { CHAT_JOB_TERMINAL_STATUSES } from './chatbotWorkspaceModel';

/** The job generating the latest reply: tracked until it ends, then the session is refreshed. */
export function useChatJob(selectedSessionId: string | null) {
  const queryClient = useQueryClient();
  const [activeChatJobId, setActiveChatJobId] = useState<string | null>(null);
  const [chatJobError, setChatJobError] = useState<string | null>(null);
  const reconciledChatJobIdRef = useRef<string | null>(null);
  const chatJobQueryKey = useMemo(() => ['feature', 'chatbot', 'generation-job', activeChatJobId], [activeChatJobId]);
  const jobEventsOpen = useJobEventsOpen();
  useJobEventRefresh(useMemo(() => [chatJobQueryKey], [chatJobQueryKey]), { jobId: activeChatJobId, enabled: Boolean(activeChatJobId) });
  const chatJobQuery = useQuery({
    queryKey: chatJobQueryKey,
    queryFn: () => omnixApiClient.getJob(activeChatJobId ?? ''),
    enabled: Boolean(activeChatJobId),
    retry: false,
    // Job lifecycle events refetch the job; it is polled only while they cannot arrive.
    refetchInterval: (query) => (
      CHAT_JOB_TERMINAL_STATUSES.has(String(query.state.data?.status ?? '')) || jobEventsOpen
        ? false
        : POLL_INTERVALS_MS.jobWithoutEvents
    ),
  });
  const chatJobInProgress = Boolean(
    activeChatJobId
    && !chatJobQuery.isError
    && (!chatJobQuery.data || !CHAT_JOB_TERMINAL_STATUSES.has(String(chatJobQuery.data.status))),
  );

  useEffect(() => {
    const job = chatJobQuery.data;
    if (!job) return;
    const status = String(job.status);
    const jobSessionId = typeof job.input_payload?.session_id === 'string'
      ? job.input_payload.session_id
      : selectedSessionId;
    if (status === 'completed') {
      setChatJobError(null);
      if (reconciledChatJobIdRef.current === job.id) return;
      reconciledChatJobIdRef.current = job.id;
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'session', jobSessionId] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
      return;
    }
    if (status === 'failed') {
      const error = job.error as { message?: unknown } | null | undefined;
      setChatJobError(typeof error?.message === 'string' ? error.message : 'Chat generation failed.');
    }
    if (status === 'canceled' || status === 'stale') {
      setChatJobError('Chat generation was canceled.');
    }
    if (CHAT_JOB_TERMINAL_STATUSES.has(status)) {
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'session', jobSessionId] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
    }
  }, [chatJobQuery.data, queryClient, selectedSessionId]);

  return { setActiveChatJobId, chatJobError, setChatJobError, chatJobQuery, chatJobInProgress };
}
