import { useEffect, useMemo, useState } from 'react';
import {
  createChatbotActivityEvents,
  createToolExecutionRows,
  type AssistantWorkspaceEvent,
  type AssistantWorkspaceEventStoreFilter,
  type AssistantWorkspaceRuntimeConfig,
  type ChatbotActivitySession,
} from '../workspace';
import { appendWorkspaceEventIfMissing, createChatbotWorkspaceEventStore, createWorkspaceEventFilter } from './chatbotWorkspaceModel';

/** The workspace activity of the active session (messages, tool runs, failures), kept in the browser's event store. */
export function useChatActivity(runtimeConfig: AssistantWorkspaceRuntimeConfig, activeSession: ChatbotActivitySession | null | undefined) {
  const eventStore = useMemo(() => createChatbotWorkspaceEventStore(runtimeConfig), [runtimeConfig]);
  const [activityEvents, setActivityEvents] = useState<AssistantWorkspaceEvent[]>(() =>
    eventStore.list(createWorkspaceEventFilter(runtimeConfig)),
  );
  const toolExecutionRows = useMemo(() => createToolExecutionRows(activityEvents), [activityEvents]);

  useEffect(() => {
    const filter = createWorkspaceEventFilter(runtimeConfig, activeSession?.id);
    const currentEvents = eventStore.list(filter);
    const currentEventIds = new Set(currentEvents.map((event) => event.id));
    const sessionEvents = createChatbotActivityEvents(activeSession ?? undefined, { workspaceId: runtimeConfig.workspaceId, projectId: runtimeConfig.projectId });
    eventStore.appendMany(sessionEvents.filter((event) => !currentEventIds.has(event.id)));
    setActivityEvents(eventStore.list(filter));
  }, [activeSession, eventStore, runtimeConfig]);

  function refreshActivityPanel(): void {
    setActivityEvents(eventStore.list(createWorkspaceEventFilter(runtimeConfig, activeSession?.id)));
  }

  function recordActivityEvent(event: AssistantWorkspaceEvent, filter: AssistantWorkspaceEventStoreFilter): void {
    appendWorkspaceEventIfMissing(eventStore, event, filter);
    setActivityEvents(eventStore.list(filter));
  }

  return { toolExecutionRows, refreshActivityPanel, recordActivityEvent };
}
