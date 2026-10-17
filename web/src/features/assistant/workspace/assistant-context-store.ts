import { useSyncExternalStore } from 'react';
import { createApi } from '../api/gateway';
import { DesktopTemporalCapture } from './desktop-temporal-capture';
import type { components } from '../api/generated';
import { emitOmnixEvent } from '../../../events/bus';

/**
 * The chat composer's context choices (WP-9.4): web research mode and its
 * release availability, agent mode, the deep-research page budget, desktop
 * sharing and a local folder. The composer renders them; the context and
 * release middlewares read them when a chat message is sent.
 */
export type ResearchMode = 'disabled' | 'quick' | 'deep';

export type LocalWorkspaceSelection = { path: string; name: string };

export type ReleaseAvailability = { disabled: boolean; quick: boolean; deep: boolean; hermes_planner: boolean };

export type DesktopShareSession = {
  stream: MediaStream;
  video: HTMLVideoElement;
  capture: DesktopTemporalCapture;
  sourceFingerprint: string;
};

export type AssistantContextState = {
  activeSessionId: string | null;
  profileDefaultMode: ResearchMode;
  researchMode: ResearchMode;
  deepResearchMaxPages: number;
  agentMode: boolean;
  desktopShare: DesktopShareSession | null;
  desktopStatus: string;
  localWorkspace: LocalWorkspaceSelection | null;
  localWorkspaceStatus: string | null;
  availability: ReleaseAvailability;
  allowDowngrade: boolean;
  releaseMessage: string;
};

type LocalWorkspacePickResponse = components['schemas']['LocalWorkspacePickResponse'];
type DisplayMediaDevices = MediaDevices & {
  getDisplayMedia?: (constraints?: { video?: boolean | MediaTrackConstraints; audio?: boolean }) => Promise<MediaStream>;
};

export const DEFAULT_DEEP_RESEARCH_PAGES = 12;
export const MAX_DEEP_RESEARCH_PAGES = 30;
const AGENT_MODE_STORAGE_KEY = 'omnix.chat.mode';
const DEEP_RESEARCH_PAGES_STORAGE_KEY = 'omnix.deepResearch.maxPages';
const LOCAL_WORKSPACES_STORAGE_KEY = 'omnix.chat.localWorkspaces.v1';

function initialState(): AssistantContextState {
  return {
    activeSessionId: null,
    profileDefaultMode: 'disabled',
    researchMode: 'disabled',
    deepResearchMaxPages: DEFAULT_DEEP_RESEARCH_PAGES,
    agentMode: readStoredAgentMode(),
    desktopShare: null,
    desktopStatus: 'Off',
    localWorkspace: null,
    localWorkspaceStatus: null,
    availability: { disabled: true, quick: true, deep: true, hermes_planner: false },
    allowDowngrade: false,
    releaseMessage: 'Research availability is loading.',
  };
}

let state: AssistantContextState = initialState();
const listeners = new Set<() => void>();
const knownResearchModes = new Map<string, ResearchMode>();
const researchModePersistenceQueues = new Map<string, Promise<void>>();
// The store's own gateway calls; set to the context middleware's lower chain when it installs.

function update(change: Partial<AssistantContextState>): void {
  state = { ...state, ...change };
  listeners.forEach((listener) => listener());
}

function client() {
  return createApi();
}

export const assistantContextStore = {
  getState(): AssistantContextState {
    return state;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  update,
  resetForTests(): void {
    knownResearchModes.clear();
    researchModePersistenceQueues.clear();
    state = initialState();
    listeners.forEach((listener) => listener());
  },
};

export function useAssistantContext(): AssistantContextState {
  return useSyncExternalStore(assistantContextStore.subscribe, assistantContextStore.getState, assistantContextStore.getState);
}

export function webResearchModeLabel(mode: ResearchMode): string {
  if (mode === 'quick') return 'Quick search';
  if (mode === 'deep') return 'Deep research';
  return 'Disabled';
}

export function normalizeResearchMode(value: unknown): ResearchMode {
  if (value === 'quick' || value === 'deep' || value === 'disabled') return value;
  return 'disabled';
}

export function normalizeDeepResearchPageLimit(value: unknown, fallback = DEFAULT_DEEP_RESEARCH_PAGES): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  const candidate = Number.isFinite(numeric) ? Math.trunc(numeric) : fallback;
  return Math.max(1, Math.min(MAX_DEEP_RESEARCH_PAGES, candidate));
}

export function normalizeLocalWorkspaceSelection(value: unknown): LocalWorkspaceSelection | null {
  const record = asRecord(value);
  const path = typeof record.path === 'string' ? record.path.trim() : '';
  if (!path) return null;
  const explicitName = typeof record.name === 'string' ? record.name.trim() : '';
  const normalized = path.replace(/[\\/]+$/, '');
  const inferredName = normalized.split(/[\\/]/).filter(Boolean).at(-1) || path;
  return { path, name: explicitName || inferredName };
}

export function localWorkspaceSummary(selection: LocalWorkspaceSelection | null): string {
  return selection ? `Local folder · ${selection.name}` : '';
}

export function desktopStatusLabel(isSharing: boolean, status: string): string {
  return isSharing || status !== 'Off' ? status : 'Off';
}

/** Whether a mode can be chosen now (the release may withhold quick or deep research). */
export function researchModeAvailable(mode: ResearchMode, availability: ReleaseAvailability): boolean {
  return mode === 'disabled' || availability[mode];
}

/** The active context tools, as the composer's summary lists them. */
export function activeContextTools(current: AssistantContextState): string[] {
  return [
    current.agentMode ? 'Agent mode' : '',
    current.researchMode !== 'disabled' ? webResearchModeLabel(current.researchMode) : '',
    current.desktopShare !== null ? 'Desktop sharing' : '',
    localWorkspaceSummary(current.localWorkspace),
  ].filter(Boolean);
}

/** A choice of web research for the current session, persisted to it. */
export function setResearchMode(value: unknown): void {
  const mode = normalizeResearchMode(value);
  if (!researchModeAvailable(mode, state.availability)) return;
  update({ researchMode: mode });
  if (state.activeSessionId) scheduleConversationResearchModePersistence(state.activeSessionId, mode);
}

export function setDeepResearchMaxPages(value: unknown): void {
  const pages = normalizeDeepResearchPageLimit(value, state.deepResearchMaxPages);
  writeStorage(DEEP_RESEARCH_PAGES_STORAGE_KEY, String(pages));
  update({ deepResearchMaxPages: pages });
}

export function toggleAgentMode(): void {
  const agentMode = !state.agentMode;
  writeStorage(AGENT_MODE_STORAGE_KEY, agentMode ? 'agent' : 'normal');
  update({ agentMode });
}

export function setAllowResearchDowngrade(allow: boolean): void {
  update({ allowDowngrade: allow });
}

/** Switches to a session; its local folder comes from this browser's storage. */
export function adoptActiveSession(nextSessionId: string | null): void {
  if (nextSessionId === state.activeSessionId) return;
  let localWorkspace = state.localWorkspace;
  if (nextSessionId && state.activeSessionId === null && localWorkspace) storeLocalWorkspace(nextSessionId, localWorkspace);
  else localWorkspace = nextSessionId ? readStoredLocalWorkspace(nextSessionId) : null;
  update({ activeSessionId: nextSessionId, localWorkspace, localWorkspaceStatus: null });
}

/** The session's own research mode, or the profile default when it has none. */
export function applySessionResearchMode(sessionId: string, override: unknown): void {
  adoptActiveSession(sessionId);
  const mode = override == null ? state.profileDefaultMode : normalizeResearchMode(override);
  knownResearchModes.set(sessionId, mode);
  update({ researchMode: mode });
}

export async function loadProfileResearchDefault(): Promise<void> {
  try {
    const { data } = await client().GET('/api/settings/profile');
    const profile = asRecord(asRecord(data?.settings).settings_control_center);
    const assistant = asRecord(profile.assistant);
    const profileDefaultMode = normalizeResearchMode(assistant.researchDefaultMode);
    const deepResearchMaxPages = normalizeDeepResearchPageLimit(
      readStoredDeepResearchPageLimit() ?? assistant.researchMaxSources,
      state.deepResearchMaxPages,
    );
    update({ profileDefaultMode, deepResearchMaxPages, ...(state.activeSessionId ? {} : { researchMode: profileDefaultMode }) });
  } catch {
    // Settings availability must not block chat.
  }
}

/** Persists the turn's research mode to its session after the request settles. */
export function deferResearchModePersistence(responsePromise: Promise<unknown>, sessionId: string | null, mode: ResearchMode): void {
  if (!sessionId || knownResearchModes.get(sessionId) === mode) return;
  const persist = () => scheduleConversationResearchModePersistence(sessionId, mode);
  void responsePromise.then(persist, persist);
}

function scheduleConversationResearchModePersistence(sessionId: string, mode: ResearchMode): void {
  if (knownResearchModes.get(sessionId) === mode) return;
  const previous = researchModePersistenceQueues.get(sessionId) ?? Promise.resolve();
  const next = previous
    .catch(() => undefined)
    .then(async () => {
      if (knownResearchModes.get(sessionId) === mode) return;
      if (await persistConversationResearchMode(sessionId, mode)) {
        knownResearchModes.set(sessionId, mode);
        dispatchPerformance('assistant_context_research_mode_persisted', { sessionId, researchMode: mode });
      }
    });
  researchModePersistenceQueues.set(sessionId, next);
  const cleanup = (): void => {
    if (researchModePersistenceQueues.get(sessionId) === next) researchModePersistenceQueues.delete(sessionId);
  };
  void next.then(cleanup, cleanup);
}

async function persistConversationResearchMode(sessionId: string, mode: ResearchMode): Promise<boolean> {
  try {
    const { response } = await client().POST('/api/chat/sessions/{session_id}/research-mode', {
      params: { path: { session_id: sessionId } },
      body: { research_mode_override: mode },
    });
    return response.ok;
  } catch {
    // The turn still carries its explicit mode if session persistence is unavailable.
    return false;
  }
}

export async function toggleLocalWorkspace(): Promise<void> {
  if (state.localWorkspace) {
    if (state.activeSessionId) storeLocalWorkspace(state.activeSessionId, null);
    update({ localWorkspace: null, localWorkspaceStatus: null });
    return;
  }
  update({ localWorkspaceStatus: 'Choose a folder…' });
  try {
    const { data, error, response } = await client().POST('/api/agent-runs/workspace-picker');
    if (!response.ok) {
      const detail = asRecord(error).detail;
      update({ localWorkspaceStatus: typeof detail === 'string' && detail.trim() ? detail.trim() : 'Folder picker unavailable' });
      return;
    }
    const selection = normalizeLocalWorkspaceSelection(data as LocalWorkspacePickResponse);
    if (selection && state.activeSessionId) storeLocalWorkspace(state.activeSessionId, selection);
    update({ localWorkspace: selection, localWorkspaceStatus: null });
  } catch (error) {
    update({ localWorkspaceStatus: error instanceof Error ? error.message : 'Folder picker unavailable' });
  }
}

export async function toggleDesktopShare(): Promise<void> {
  if (state.desktopShare) {
    stopDesktopShare();
    return;
  }
  const mediaDevices = navigator.mediaDevices as DisplayMediaDevices | undefined;
  if (!mediaDevices?.getDisplayMedia) {
    update({ desktopStatus: 'Screen capture unavailable' });
    return;
  }
  try {
    update({ desktopStatus: 'Choose a screen or window' });
    const stream = await mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 5, max: 10 } }, audio: false });
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play();
    await waitForVideoDimensions(video);
    const capture = new DesktopTemporalCapture(video);
    capture.start();
    update({
      desktopShare: { stream, video, capture, sourceFingerprint: desktopSourceFingerprint(stream) },
      desktopStatus: 'Buffering recent frames',
    });
    emitOmnixEvent('omnix:desktop-share-changed', { sharing: true });
    stream.getVideoTracks()[0]?.addEventListener('ended', () => stopDesktopShare(), { once: true });
  } catch (error) {
    const desktopStatus = error instanceof Error && error.name === 'NotAllowedError'
      ? 'Sharing cancelled'
      : error instanceof Error ? error.message : 'Could not share desktop';
    stopDesktopShare({ resetStatus: false });
    update({ desktopStatus });
  }
}

export function stopDesktopShare(options: { resetStatus?: boolean } = {}): void {
  const current = state.desktopShare;
  current?.capture.stop();
  current?.stream.getTracks().forEach((track) => track.stop());
  if (current) current.video.srcObject = null;
  update({ desktopShare: null, ...(options.resetStatus === false ? {} : { desktopStatus: 'Off' }) });
  if (current) emitOmnixEvent('omnix:desktop-share-changed', { sharing: false });
}

export function readStoredAgentMode(): boolean {
  try {
    return window.localStorage.getItem(AGENT_MODE_STORAGE_KEY) === 'agent';
  } catch {
    return false;
  }
}

export function storeLocalWorkspace(sessionId: string, selection: LocalWorkspaceSelection | null): void {
  if (!sessionId) return;
  try {
    const raw = window.localStorage.getItem(LOCAL_WORKSPACES_STORAGE_KEY);
    const payload = raw ? JSON.parse(raw) as Record<string, unknown> : {};
    if (selection) payload[sessionId] = selection;
    else delete payload[sessionId];
    window.localStorage.setItem(LOCAL_WORKSPACES_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Browser storage is optional; the selection remains active for this page.
  }
}

function readStoredLocalWorkspace(sessionId: string): LocalWorkspaceSelection | null {
  try {
    const raw = window.localStorage.getItem(LOCAL_WORKSPACES_STORAGE_KEY);
    if (!raw) return null;
    return normalizeLocalWorkspaceSelection((JSON.parse(raw) as Record<string, unknown>)[sessionId]);
  } catch {
    return null;
  }
}

function readStoredDeepResearchPageLimit(): number | null {
  try {
    const value = window.localStorage.getItem(DEEP_RESEARCH_PAGES_STORAGE_KEY);
    return value === null ? null : normalizeDeepResearchPageLimit(value);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage is optional; the choice still applies to this page.
  }
}

function desktopSourceFingerprint(stream: MediaStream): string {
  const track = stream.getVideoTracks()[0];
  const settings = track?.getSettings() as MediaTrackSettings & { displaySurface?: string };
  const source = `${settings.displaySurface ?? 'unknown'}:${track?.label ?? 'desktop'}`;
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `desktop-source:${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

function waitForVideoDimensions(video: HTMLVideoElement): Promise<void> {
  if (video.videoWidth > 0 && video.videoHeight > 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(() => reject(new Error('Desktop preview did not become ready')), 5_000);
    video.addEventListener('loadedmetadata', () => {
      window.clearTimeout(timeoutId);
      resolve();
    }, { once: true });
  });
}

export function dispatchPerformance(stage: string, detail: Record<string, unknown>): void {
  emitOmnixEvent('omnix:assistant-voice-perf', { stage, timestamp: new Date().toISOString(), ...detail });
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
