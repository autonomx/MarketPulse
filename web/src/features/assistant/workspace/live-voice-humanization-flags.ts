import { emitOmnixEvent, LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT } from '../../../events/bus';
export const LIVE_VOICE_HUMANIZATION_FLAGS_KEY = 'omnix.liveVoice.humanizationFlags.v1';

export type LiveVoiceHumanizationFlags = {
  master: boolean;
  stableClauses: boolean;
  naturalTiming: boolean;
  performancePlans: boolean;
  responseCues: boolean;
  listenerCues: boolean;
  proceduralCueFallback: boolean;
  vocalContinuity: boolean;
};

export const DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS: Readonly<LiveVoiceHumanizationFlags> = {
  master: true,
  stableClauses: true,
  naturalTiming: true,
  performancePlans: true,
  responseCues: true,
  listenerCues: true,
  proceduralCueFallback: false,
  vocalContinuity: true,
};

export function readLiveVoiceHumanizationFlags(): LiveVoiceHumanizationFlags {
  if (typeof window === 'undefined') return { ...DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS };
  try {
    const raw = window.localStorage.getItem(LIVE_VOICE_HUMANIZATION_FLAGS_KEY);
    if (!raw) return { ...DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS).map(([key, fallback]) => [
        key,
        typeof parsed[key] === 'boolean' ? parsed[key] : fallback,
      ]),
    ) as LiveVoiceHumanizationFlags;
  } catch {
    return { ...DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS };
  }
}

export function writeLiveVoiceHumanizationFlags(
  patch: Partial<LiveVoiceHumanizationFlags>,
): LiveVoiceHumanizationFlags {
  const next = { ...readLiveVoiceHumanizationFlags(), ...booleanPatch(patch) };
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(LIVE_VOICE_HUMANIZATION_FLAGS_KEY, JSON.stringify(next));
    emitOmnixEvent(LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT, next);
  }
  return next;
}

export function resetLiveVoiceHumanizationFlags(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(LIVE_VOICE_HUMANIZATION_FLAGS_KEY);
  emitOmnixEvent(LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT, { ...DEFAULT_LIVE_VOICE_HUMANIZATION_FLAGS });
}

function booleanPatch(
  patch: Partial<LiveVoiceHumanizationFlags>,
): Partial<LiveVoiceHumanizationFlags> {
  return Object.fromEntries(
    Object.entries(patch).filter((entry): entry is [string, boolean] => typeof entry[1] === 'boolean'),
  ) as Partial<LiveVoiceHumanizationFlags>;
}
