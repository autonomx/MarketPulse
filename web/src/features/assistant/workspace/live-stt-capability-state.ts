import { ASSISTANT_VOICE_PERF_EVENT } from '../../../events/bus';
export type LiveSttCapabilitySnapshot = {
  provider: string | null;
  capabilities: readonly string[];
  negotiated: boolean;
};

const CAP_AUTHORITATIVE_EOU = 'authoritative_eou';
const FINAL_ONLY_CAPABILITIES = new Set([
  'segmented_audio',
  'authoritative_final',
  'result_replay',
  'client_audio_replay',
]);

let snapshot: LiveSttCapabilitySnapshot = {
  provider: null,
  capabilities: [],
  negotiated: false,
};

export function noteLiveSttNegotiation(
  provider: string | undefined,
  capabilities: readonly string[],
): void {
  snapshot = {
    provider: provider?.trim() || null,
    capabilities: [...new Set(capabilities.map((capability) => capability.trim()).filter(Boolean))].sort(),
    negotiated: true,
  };
}

export function resetLiveSttCapabilityState(): void {
  snapshot = {
    provider: null,
    capabilities: [],
    negotiated: false,
  };
}

export function currentLiveSttCapabilities(): LiveSttCapabilitySnapshot {
  return {
    provider: snapshot.provider,
    capabilities: [...snapshot.capabilities],
    negotiated: snapshot.negotiated,
  };
}

export function liveSttUsesAuthoritativeEou(): boolean {
  return snapshot.negotiated && snapshot.capabilities.includes(CAP_AUTHORITATIVE_EOU);
}

export function liveSttUsesFinalOnlyEndpointing(): boolean {
  if (!snapshot.negotiated || snapshot.capabilities.length === 0) return false;
  return snapshot.capabilities.every((capability) => FINAL_ONLY_CAPABILITIES.has(capability));
}

function handleLiveVoicePerfEvent(event: Event): void {
  const detail = (event as CustomEvent<Record<string, unknown>>).detail;
  if (!detail || typeof detail.stage !== 'string') return;
  if (detail.stage === 'stt_authority_selected') {
    resetLiveSttCapabilityState();
    return;
  }
  if (detail.stage !== 'stt_negotiated') return;
  const provider = typeof detail.provider === 'string' ? detail.provider : undefined;
  const capabilities = Array.isArray(detail.capabilities)
    ? detail.capabilities.filter((capability): capability is string => typeof capability === 'string')
    : [];
  noteLiveSttNegotiation(provider, capabilities);
}

if (typeof window !== 'undefined') {
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handleLiveVoicePerfEvent);
}
