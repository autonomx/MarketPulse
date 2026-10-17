import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { omnixApiClient } from '../../../api/client';
import { noteChatSession } from './researchProgressController';
import { visibleChatSessions } from './sessionTools';
import {
  type AssistantView,
  type SessionListEntry,
} from './chatbotWorkspaceModel';
import { adoptActiveSession, applySessionResearchMode } from '../workspace';
import { emitOmnixEvent } from '../../../events/bus';
import { useSearchParam } from '../../../shared/useSearchParam';

type ChatSessionsOptions = {
  setActiveView: (view: AssistantView) => void;
  setAudioStatus: (status: string | null) => void;
};

/**
 * Chat's sessions: the list, the selected session (remembered per browser)
 * and its messages. Sessions created or switched elsewhere (Live Chat, the
 * new-chat button) become the selection.
 */
export function useChatSessions({ setActiveView, setAudioStatus }: ChatSessionsOptions) {
  const queryClient = useQueryClient();
  // The open session is in the URL (?session=), so it can be linked to (WP-9.6).
  const [selectedSessionId, setSelectedSessionId] = useSearchParam('session');
  const pendingCreatedSessionIdRef = useRef<string | null>(null);
  const sessionsQuery = useQuery({ queryKey: ['feature', 'chatbot', 'sessions'], queryFn: async () => visibleChatSessions(await omnixApiClient.listChatSessions()) });
  const selectedSessionSummary = sessionsQuery.data?.sessions.find((session) => session.id === selectedSessionId);
  const sessionQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'session', selectedSessionId],
    queryFn: async () => {
      const session = noteChatSession(await omnixApiClient.getChatSession(selectedSessionId ?? ''));
      applySessionResearchMode(session.id, session.research_mode_override);
      return session;
    },
    enabled: Boolean(selectedSessionId),
  });

  const chatSessions = sessionsQuery.data?.sessions ?? [];
  const sessionsLoading = sessionsQuery.isPending;
  const sessionsError = sessionsQuery.isError;
  const activeSessionLoading = Boolean(selectedSessionId) && sessionQuery.isPending;
  const activeSessionError = Boolean(selectedSessionId) && sessionQuery.isError;

  useEffect(() => {
    const sessions = sessionsQuery.data?.sessions;
    if (!sessions) return;
    if (selectedSessionId && pendingCreatedSessionIdRef.current === selectedSessionId) {
      if (sessions.some((session) => session.id === selectedSessionId)) {
        pendingCreatedSessionIdRef.current = null;
      } else {
        // The create response selects the new session before the invalidated
        // list query can include it. Do not fall back to the previous chat
        // while that authoritative list catches up.
        return;
      }
    }
    // A newly created session can be selected before the invalidated list has
    // finished refetching. Keep it while the list is temporarily empty.
    if (selectedSessionId && (sessions.length === 0 || sessions.some((session) => session.id === selectedSessionId))) return;
    setSelectedSessionId(sessions[0]?.id ?? null);
  }, [selectedSessionId, sessionsQuery.data]);


  useEffect(() => {
    const session = sessionQuery.data
      ?? sessionsQuery.data?.sessions.find((candidate) => candidate.id === selectedSessionId);
    adoptActiveSession(selectedSessionId);
    emitOmnixEvent('omnix:chat-session-selected', {
        sessionId: selectedSessionId,
        session,
      });
  }, [selectedSessionId, sessionQuery.data, sessionsQuery.data]);

  useEffect(() => {
    const syncLiveChatSession = (event: Event) => {
      const sessionId = (event as CustomEvent<{ sessionId?: string }>).detail?.sessionId;
      if (!sessionId) return;
      if (pendingCreatedSessionIdRef.current && pendingCreatedSessionIdRef.current !== sessionId) {
        pendingCreatedSessionIdRef.current = null;
      }
      setSelectedSessionId(sessionId);
    };
    const syncCreatedChatSession = (event: Event) => {
      const session = (event as CustomEvent<{ session?: { id?: unknown; [key: string]: unknown } }>).detail?.session;
      const sessionId = typeof session?.id === 'string' ? session.id.trim() : '';
      if (!sessionId) return;

      pendingCreatedSessionIdRef.current = sessionId;
      queryClient.setQueryData(['feature', 'chatbot', 'session', sessionId], session);
      setSelectedSessionId(sessionId);
      setActiveView('chats');
    };
    window.addEventListener('omnix:live-chat-session-changed', syncLiveChatSession);
    window.addEventListener('omnix:chat-session-created', syncCreatedChatSession);
    return () => {
      window.removeEventListener('omnix:live-chat-session-changed', syncLiveChatSession);
      window.removeEventListener('omnix:chat-session-created', syncCreatedChatSession);
    };
  }, [queryClient, setActiveView]);

  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) => omnixApiClient.deleteChatSession(sessionId),
    onSuccess: async (_result, sessionId) => {
      queryClient.removeQueries({ queryKey: ['feature', 'chatbot', 'session', sessionId] });
      if (selectedSessionId === sessionId) {
        const remaining = chatSessions.filter((session) => session.id !== sessionId);
        setSelectedSessionId(remaining[0]?.id ?? null);
      }
      setAudioStatus('Chat session deleted.');
      await queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'sessions'] });
    },
    onError: (error) => {
      setAudioStatus(error instanceof Error ? error.message : 'Chat session delete failed.');
    },
  });

  function selectSidebarSession(session: SessionListEntry): void {
    setSelectedSessionId(session.id);
    setActiveView('chats');
    emitOmnixEvent('omnix:live-chat-session-changed', { sessionId: session.id });
  }

  return {
    selectedSessionId, setSelectedSessionId,
    sessionsQuery, sessionQuery,
    selectedSessionSummary,
    chatSessions, sessionsLoading, sessionsError,
    activeSessionLoading, activeSessionError,
    deleteSessionMutation,
    selectSidebarSession,
  };
}

export type ChatSessionListEntry = NonNullable<ReturnType<typeof useChatSessions>['selectedSessionSummary']>;
