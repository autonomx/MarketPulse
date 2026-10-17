import type { STTProxyResponse } from '../../voice';
import { speechOrigin, streamingSttUrl, type SpeechLocation } from './stt-url';


const DEFAULT_ENDPOINT_THRESHOLD = 0.75;

export type AuthorityMode = 'observational' | 'test' | 'auto';

// The speech service's authority probe; the gateway proxies it as /api/stt/authorityz.

export type AuthoritySelection = {
  websocketUrl: string;
  authorityEnabled: boolean;
  mode: AuthorityMode;
  endpointThreshold: number;
  fallbackUsed: boolean;
  reasons: string[];
};

export async function resolveAuthoritySelection(
  configuredUrl: string,
  locationLike: SpeechLocation,
  fetchImpl: typeof fetch,
): Promise<AuthoritySelection> {
  const configured = new URL(
    configuredUrl,
    speechOrigin(locationLike),
  );
  const mode = normalizeMode(configured.searchParams.get('authority'));
  const endpointThreshold = boundedProbability(
    configured.searchParams.get('endpoint_threshold'),
  );
  const primary = streamingSttUrl(configured.toString(), locationLike);
  const primaryUrl = primary.toString();
  if (mode === 'observational') {
    return {
      websocketUrl: primaryUrl,
      authorityEnabled: false,
      mode,
      endpointThreshold,
      fallbackUsed: false,
      reasons: ['observational_mode'],
    };
  }

  const language = configured.searchParams.get('language')?.trim() || 'en';
  const authorityUrl = new URL(primary);
  authorityUrl.pathname = `${primary.pathname.slice(0, -'/ws/transcribe'.length)}/authorityz`;
  authorityUrl.protocol = authorityUrl.protocol === 'wss:'
    ? 'https:'
    : authorityUrl.protocol === 'ws:'
      ? 'http:'
      : authorityUrl.protocol;
  authorityUrl.search = '';
  authorityUrl.searchParams.set('language', language);
  authorityUrl.searchParams.set('mode', mode);

  let response: Partial<STTProxyResponse> = {};
  let probeSucceeded = false;
  let reasons: string[] = [];
  try {
    const authorityResponse = await fetchImpl(authorityUrl.toString(), {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    response = await authorityResponse.json() as STTProxyResponse;
    probeSucceeded = authorityResponse.ok;
    if (!authorityResponse.ok) {
      reasons.push(`authority_http_${authorityResponse.status}`);
    }
  } catch (error) {
    reasons.push(
      error instanceof Error ? error.message : 'authority_probe_failed',
    );
  }
  reasons = [...reasons, ...(response.reasons ?? [])];
  if (probeSucceeded && response.eligible === true && response.ok !== false) {
    return {
      websocketUrl: primaryUrl,
      authorityEnabled: true,
      mode,
      endpointThreshold,
      fallbackUsed: false,
      reasons,
    };
  }

  const fallback = configured.searchParams.get('fallback')?.trim();
  if (!fallback) {
    throw new Error(
      `STT authority gate failed: ${reasons.join(', ') || 'not eligible'}`,
    );
  }
  return {
    websocketUrl: streamingSttUrl(fallback, locationLike).toString(),
    authorityEnabled: false,
    mode,
    endpointThreshold,
    fallbackUsed: true,
    reasons: reasons.length ? reasons : ['authority_not_eligible'],
  };
}

/**
 * Compatibility no-op for older bootstrap imports. Authority is resolved
 * before microphone capture and committed by the live voice controller.
 */
export function initializeLiveSttAuthorityController(): () => void {
  return () => undefined;
}

function normalizeMode(value: string | null): AuthorityMode {
  return value === 'test' || value === 'auto' ? value : 'observational';
}

function boundedProbability(value: string | null): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_ENDPOINT_THRESHOLD;
  return Math.max(0.5, Math.min(0.99, parsed));
}
