import type { AssistantWorkspaceEvent, AssistantWorkspaceEventType } from './events';
import { isAssistantWorkspaceEventType } from './events';

export type AssistantWorkspaceEventStoreFilter = {
  workspaceId?: string;
  projectId?: string;
  sessionId?: string;
  type?: AssistantWorkspaceEventType | AssistantWorkspaceEventType[];
};

export type AssistantWorkspaceEventStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export type AssistantWorkspaceEventStore = {
  append(event: AssistantWorkspaceEvent): AssistantWorkspaceEvent;
  /** Appends several events with one write to storage. */
  appendMany(events: AssistantWorkspaceEvent[]): AssistantWorkspaceEvent[];
  list(filter?: AssistantWorkspaceEventStoreFilter): AssistantWorkspaceEvent[];
  get(eventId: string): AssistantWorkspaceEvent | undefined;
  clear(): void;
};

const DEFAULT_STORAGE_KEY = 'omnix.assistantWorkspace.events';
/** Browser storage keeps the newest events only; older ones are dropped. */
export const MAX_STORED_ASSISTANT_WORKSPACE_EVENTS = 1_000;

function cloneEvent(event: AssistantWorkspaceEvent): AssistantWorkspaceEvent {
  return JSON.parse(JSON.stringify(event)) as AssistantWorkspaceEvent;
}

function matchesFilter(event: AssistantWorkspaceEvent, filter: AssistantWorkspaceEventStoreFilter): boolean {
  const allowedTypes = Array.isArray(filter.type)
    ? filter.type
    : filter.type
      ? [filter.type]
      : undefined;

  return (
    (!filter.workspaceId || event.workspaceId === filter.workspaceId) &&
    (!filter.projectId || event.projectId === filter.projectId) &&
    (!filter.sessionId || event.sessionId === filter.sessionId) &&
    (!allowedTypes || allowedTypes.includes(event.type))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isSerializedEvent(value: unknown): value is AssistantWorkspaceEvent {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'string' &&
    typeof value.type === 'string' &&
    isAssistantWorkspaceEventType(value.type) &&
    typeof value.workspaceId === 'string' &&
    (value.projectId === undefined || typeof value.projectId === 'string') &&
    (value.sessionId === undefined || typeof value.sessionId === 'string') &&
    isRecord(value.payload) &&
    typeof value.createdAt === 'string'
  );
}

export function serializeAssistantWorkspaceEvents(events: AssistantWorkspaceEvent[]): string {
  return JSON.stringify(events.map(cloneEvent));
}

export function parseAssistantWorkspaceEvents(serialized: string | null | undefined): AssistantWorkspaceEvent[] {
  if (!serialized) return [];

  try {
    const parsed = JSON.parse(serialized) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSerializedEvent).map(cloneEvent);
  } catch {
    return [];
  }
}

export function createInMemoryAssistantWorkspaceEventStore(
  initialEvents: AssistantWorkspaceEvent[] = [],
): AssistantWorkspaceEventStore {
  let events = initialEvents.map(cloneEvent);

  return {
    append(event) {
      const nextEvent = cloneEvent(event);
      events = [...events, nextEvent];
      return cloneEvent(nextEvent);
    },
    appendMany(nextEvents) {
      const added = nextEvents.map(cloneEvent);
      events = [...events, ...added];
      return added.map(cloneEvent);
    },
    list(filter = {}) {
      return events.filter((event) => matchesFilter(event, filter)).map(cloneEvent);
    },
    get(eventId) {
      const found = events.find((event) => event.id === eventId);
      return found ? cloneEvent(found) : undefined;
    },
    clear() {
      events = [];
    },
  };
}

export function createStoredAssistantWorkspaceEventStore(
  storage: AssistantWorkspaceEventStorage,
  storageKey = DEFAULT_STORAGE_KEY,
  maxEvents = MAX_STORED_ASSISTANT_WORKSPACE_EVENTS,
): AssistantWorkspaceEventStore {
  let events = parseAssistantWorkspaceEvents(storage.getItem(storageKey)).slice(-maxEvents);

  const persist = () => {
    try {
      storage.setItem(storageKey, serializeAssistantWorkspaceEvents(events));
    } catch {
      // Storage full or unavailable: the events stay in memory for this page.
    }
  };
  const add = (nextEvents: AssistantWorkspaceEvent[]): AssistantWorkspaceEvent[] => {
    const added = nextEvents.map(cloneEvent);
    events = [...events, ...added].slice(-maxEvents);
    persist();
    return added.map(cloneEvent);
  };

  return {
    append(event) {
      return add([event])[0];
    },
    appendMany(nextEvents) {
      return nextEvents.length ? add(nextEvents) : [];
    },
    list(filter = {}) {
      return events.filter((event) => matchesFilter(event, filter)).map(cloneEvent);
    },
    get(eventId) {
      const found = events.find((event) => event.id === eventId);
      return found ? cloneEvent(found) : undefined;
    },
    clear() {
      events = [];
      storage.removeItem(storageKey);
    },
  };
}
