import { fetchBelow, registerFetchMiddleware } from '../../../api/fetchPipeline';
import type { components } from '../api/generated';
import { createApi } from '../api/gateway';
import { assistantContextStore, type ReleaseAvailability, type ResearchMode } from './assistant-context-store';

type ResearchRuntimeStatus = components['schemas']['ResearchRuntimeStatus'];

type ResearchUnavailableDetail = {
  code?: string;
  requested_mode?: string;
  reason?: string;
  available_modes?: string[];
  downgrade_available?: boolean;
};

const MESSAGE_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages(\/stream)?$/;
const ENHANCED_MESSAGE_PATH = /^\/api\/assistant\/context\/chat\/sessions\/([^/]+)\/messages(\/stream)?$/;
const SESSION_PATH = /^\/api\/chat\/sessions\/([^/]+)$/;
const MIDDLEWARE = 'research-release';
const ownClient = createApi({ fetchImpl: fetchBelow(MIDDLEWARE) });

let activeSessionId: string | null = null;
let disposeController: (() => void) | null = null;

/**
 * Keeps the research release availability in assistant-context-store (the
 * composer renders it) and adds the Quick Search fallback consent to chat
 * messages, reporting a refused research mode.
 */
export function initializeResearchReleaseController(): () => void {
  if (disposeController) return () => undefined;
  const removeMiddleware = installFetchWrapper();
  void loadReleaseStatus();
  const dispose = () => {
    removeMiddleware();
    if (disposeController === dispose) disposeController = null;
  };
  disposeController = dispose;
  return dispose;
}

export function shouldOfferResearchDowngrade(mode: ResearchMode, current: ReleaseAvailability): boolean {
  return mode === 'deep' && !current.deep && current.quick;
}

export function addResearchDowngradeConsent(
  payload: Record<string, unknown>,
  consent: boolean,
): Record<string, unknown> {
  return { ...payload, allow_research_downgrade: consent };
}

export function researchReleaseMessage(detail: ResearchUnavailableDetail): string {
  const reason = humanize(detail.reason || 'research mode unavailable');
  if (detail.downgrade_available) return `${reason}. Quick Search is available when fallback is explicitly allowed.`;
  const available = detail.available_modes?.length ? ` Available: ${detail.available_modes.join(', ')}.` : '';
  return `${reason}.${available}`;
}

function installFetchWrapper(): () => void {
  return registerFetchMiddleware(MIDDLEWARE, async (input, init, next) => {
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const inputUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
    const parsed = new URL(inputUrl, window.location.origin);
    const sessionMatch = parsed.pathname.match(SESSION_PATH);
    if (method === 'GET' && sessionMatch) {
      const response = await next(input, init);
      if (response.ok) {
        activeSessionId = sessionMatch[1] ? decodePathSegment(sessionMatch[1]) : null;
        void loadReleaseStatus(activeSessionId);
      }
      return response;
    }

    const messageMatch = parsed.pathname.match(MESSAGE_PATH) ?? parsed.pathname.match(ENHANCED_MESSAGE_PATH);
    if (method !== 'POST' || !messageMatch) return next(input, init);
    activeSessionId = messageMatch[1] ? decodePathSegment(messageMatch[1]) : activeSessionId;
    const body = await requestBodyText(input, init);
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(body) as Record<string, unknown>;
    } catch {
      return next(input, init);
    }
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set('Content-Type', 'application/json');
    const response = await next(input, {
      ...init,
      method: 'POST',
      headers,
      body: JSON.stringify(addResearchDowngradeConsent(payload, assistantContextStore.getState().allowDowngrade)),
    });
    if (response.status === 409) void showUnavailableResponse(response.clone());
    else if (response.ok) assistantContextStore.update({ releaseMessage: 'Research mode accepted for this turn.' });
    return response;
  });
}

async function loadReleaseStatus(sessionId: string | null = activeSessionId): Promise<void> {
  try {
    const { data: payload, response } = await ownClient.GET('/api/assistant/research/status', {
      params: { query: sessionId ? { session_id: sessionId } : {} },
    });
    if (!response.ok || !payload) throw new Error('Research availability could not be loaded.');
    const released = payload.release.availability;
    const availability: ReleaseAvailability = {
      disabled: released.disabled !== false,
      quick: released.quick !== false,
      deep: released.deep === true,
      hermes_planner: released.hermes_planner === true,
    };
    assistantContextStore.update({ availability, releaseMessage: releaseSummary(payload, availability) });
  } catch (error) {
    assistantContextStore.update({ releaseMessage: error instanceof Error ? error.message : 'Research availability is unavailable.' });
  }
}

async function showUnavailableResponse(response: Response): Promise<void> {
  try {
    const payload = await response.json() as { detail?: ResearchUnavailableDetail };
    assistantContextStore.update({ releaseMessage: researchReleaseMessage(payload.detail ?? {}) });
  } catch {
    assistantContextStore.update({ releaseMessage: 'The selected research mode is unavailable.' });
  }
}

function releaseSummary(payload: ResearchRuntimeStatus, current: ReleaseAvailability): string {
  if (payload.release.master_enabled === false) return 'Research rollback is active.';
  if (current.deep) return current.hermes_planner ? 'Deep Research and Hermes planning are available.' : 'Deep Research is available with the local planner.';
  if (current.quick) return 'Quick Search is available. Deep Research is not released for this session.';
  return 'External research is unavailable for this session.';
}

async function requestBodyText(input: RequestInfo | URL, init?: RequestInit): Promise<string> {
  if (typeof init?.body === 'string') return init.body;
  if (input instanceof Request) return input.clone().text();
  return '';
}

function humanize(value: string): string {
  const text = value.replaceAll('_', ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Research mode unavailable';
}

function decodePathSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
