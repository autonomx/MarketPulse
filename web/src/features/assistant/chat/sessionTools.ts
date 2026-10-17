import { omnixApiClient } from '../../../api/client';
import type { ChatSession, CreateChatSessionRequest } from '../../../api/client';
import './chat-response-metrics-controller.css';
import { characterClient, type SessionInteraction } from './characterClient';
import type { components } from '../api/generated';
import { ASSISTANT_LIVE_VOICE_STOP_EVENT, CHAT_SESSION_CREATED_EVENT, CHAT_SESSION_SELECTED_EVENT, emitOmnixEvent, LIVE_CHAT_SESSION_CHANGED_EVENT } from '../../../events/bus';

let chatSessionToolsInstalled = false;


// A new session keeps the interaction settings of the one it replaces, so they are required here.
type PreservedChatSessionRequest = components['schemas']['CreateChatSessionRequest']
  & Required<Pick<CreateChatSessionRequest, 'interaction_mode' | 'read_memory' | 'write_memory' | 'shared_memory_access' | 'transcript_policy'>>;

type SessionSelectionSnapshot = Pick<ChatSession,
  | 'id'
  | 'provider_id'
  | 'model_id'
  | 'interaction_mode'
  | 'character_id'
  | 'voice_asset_id'
  | 'read_memory'
  | 'write_memory'
  | 'shared_memory_access'
  | 'transcript_policy'
>;

type SessionSelectionEventDetail = {
  sessionId?: unknown;
  session?: Partial<SessionSelectionSnapshot>;
};

let selectedSessionId: string | null = null;
let selectedSessionSnapshot: SessionSelectionSnapshot | null = null;

function shouldShowSession(session: { title?: string | null }): boolean {
  return !String(session.title ?? '').trim().startsWith('Podcast script:');
}

/** The chat list leaves out the sessions Podcast creates for its scripts. */
export function visibleChatSessions<T extends { sessions: Array<{ title?: string | null }> }>(payload: T): T {
  return { ...payload, sessions: payload.sessions.filter(shouldShowSession) };
}

export function preservedNewChatRequest(
  session: Pick<ChatSession, 'provider_id' | 'model_id'>,
  interaction: Pick<SessionInteraction, 'interaction_mode' | 'character_id' | 'voice_asset_id' | 'read_memory' | 'write_memory' | 'shared_memory_access' | 'transcript_policy'>,
): PreservedChatSessionRequest {
  const providerId = session.provider_id ?? undefined;
  const modelId = compatibleSessionModelId(providerId, session.model_id);
  return {
    title: 'New chat',
    provider_id: providerId,
    ...(modelId ? { model_id: modelId } : {}),
    interaction_mode: interaction.interaction_mode,
    character_id: interaction.character_id ?? null,
    voice_asset_id: interaction.voice_asset_id ?? null,
    read_memory: interaction.read_memory,
    write_memory: interaction.write_memory,
    shared_memory_access: interaction.shared_memory_access,
    transcript_policy: interaction.transcript_policy,
  };
}

function compatibleSessionModelId(providerId: string | null | undefined, modelId: string | null | undefined): string | undefined {
  const candidate = modelId?.trim();
  if (!candidate) return undefined;
  if (!candidate.startsWith('llm:')) return candidate;

  const [, modelProvider] = candidate.split(':', 3);
  const selectedProvider = (providerId ?? '').trim().replace(/^llm:/, '');
  if (!modelProvider || !selectedProvider || modelProvider === selectedProvider) return candidate;

  // A prior session can retain a fully-qualified model from a provider that is
  // no longer selected. Let the server apply the selected provider's central
  // default instead of silently routing the new chat through the old provider.
  return undefined;
}

function clearMessageComposer(): void {
  const textarea = document.querySelector<HTMLTextAreaElement>(
    '.assistant-message-input textarea[name="content"], textarea[placeholder^="Message Omnix"]',
  );
  if (!textarea) return;
  const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (valueSetter) valueSetter.call(textarea, '');
  else textarea.value = '';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  textarea.dispatchEvent(new Event('change', { bubbles: true }));
}

function requestSessionListRefresh(): void {
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
}

export async function startBlankChat(): Promise<ChatSession> {
  let request: CreateChatSessionRequest = {
    title: 'New chat',
    interaction_mode: 'system',
    read_memory: false,
    write_memory: false,
    shared_memory_access: 'none',
    transcript_policy: 'persistent',
  };
  if (selectedSessionId) {
    const snapshot = selectedSessionSnapshot?.id === selectedSessionId ? selectedSessionSnapshot : null;
    if (snapshot && hasInteractionSettings(snapshot)) {
      request = preservedNewChatRequest(snapshot, snapshot);
    } else {
      try {
        const [session, interaction] = await Promise.all([
          omnixApiClient.getChatSession(selectedSessionId),
          characterClient.session(selectedSessionId),
        ]);
        request = preservedNewChatRequest(session, interaction);
      } catch {
        // Creating a blank chat must not depend on optional settings from a
        // stale or partially loaded current session. The new session can use
        // the safe system defaults when that preservation read is unavailable.
        request = {
          title: 'New chat',
          interaction_mode: 'system',
          read_memory: false,
          write_memory: false,
          shared_memory_access: 'none',
          transcript_policy: 'persistent',
        };
      }
    }
  }

  const session = await omnixApiClient.createChatSession(request);
  const sessionId = String(session.id ?? '').trim();
  if (!sessionId) throw new Error('New chat response did not include a session id.');

  selectedSessionId = sessionId;
  selectedSessionSnapshot = session;
  emitOmnixEvent(ASSISTANT_LIVE_VOICE_STOP_EVENT);
  clearMessageComposer();
  emitOmnixEvent(CHAT_SESSION_CREATED_EVENT, { session });
  emitOmnixEvent(LIVE_CHAT_SESSION_CHANGED_EVENT, { sessionId });
  requestSessionListRefresh();
  return session;
}

export function installSessionTools(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (chatSessionToolsInstalled) return () => undefined;
  chatSessionToolsInstalled = true;
  const handleSessionSelected = (event: Event) => {
    const detail = (event as CustomEvent<SessionSelectionEventDetail>).detail;
    const nextSessionId = typeof detail?.sessionId === 'string' ? detail.sessionId.trim() : '';
    selectedSessionId = nextSessionId || null;
    const snapshot = detail?.session;
    selectedSessionSnapshot = snapshot && snapshot.id === nextSessionId && hasInteractionSettings(snapshot)
      ? snapshot as SessionSelectionSnapshot
      : selectedSessionSnapshot?.id === nextSessionId ? selectedSessionSnapshot : null;
  };
  window.addEventListener(CHAT_SESSION_SELECTED_EVENT, handleSessionSelected);
  return () => {
    window.removeEventListener(CHAT_SESSION_SELECTED_EVENT, handleSessionSelected);
    chatSessionToolsInstalled = false;
  };
}

function hasInteractionSettings(
  session: Partial<SessionSelectionSnapshot>,
): session is SessionSelectionSnapshot {
  const interactionModeValid = session.interaction_mode === 'system' || session.interaction_mode === 'character';
  const characterSelectionValid = session.interaction_mode === 'system'
    ? !session.character_id
    : typeof session.character_id === 'string' && session.character_id.trim().length > 0;
  return Boolean(
    typeof session.id === 'string'
      && interactionModeValid
      && characterSelectionValid
      && typeof session.read_memory === 'boolean'
      && typeof session.write_memory === 'boolean'
      && (session.shared_memory_access === 'none' || session.shared_memory_access === 'read_only')
      && (session.transcript_policy === 'persistent' || session.transcript_policy === 'temporary' || session.transcript_policy === 'none'),
  );
}
