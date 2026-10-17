import type { SpeechPerformancePlan } from './live-speech-performance-contract';
import type { SilenceReason } from './live-voice-playback-contract';

export type NaturalPause = {
  durationMs: number;
  reason: SilenceReason;
};

export type OnsetTimingPlan = {
  desiredPerceivedOnsetMs: number;
  elapsedMs: number;
  extraDelayMs: number;
};

const SERIOUS_PATTERN = /\b(?:sorry|grief|loss|afraid|hurt|serious|difficult|take your time)\b/i;
const REFLECTION_PATTERN = /\b(?:i think|perhaps|maybe|on balance|the tradeoff|looking at this)\b/i;

export function createOnsetTimingPlan(
  elapsedMs: number,
  options: {
    desiredPerceivedOnsetMs?: number;
    maximumAdditionalDelayMs?: number;
    urgent?: boolean;
  } = {},
): OnsetTimingPlan {
  const desired = Math.max(0, options.desiredPerceivedOnsetMs ?? 450);
  const maximum = Math.max(0, options.maximumAdditionalDelayMs ?? 350);
  const elapsed = Math.max(0, Number.isFinite(elapsedMs) ? elapsedMs : 0);
  const extraDelayMs = options.urgent
    ? 0
    : Math.min(maximum, Math.max(0, desired - elapsed));
  return {
    desiredPerceivedOnsetMs: desired,
    elapsedMs: elapsed,
    extraDelayMs: Math.round(extraDelayMs),
  };
}

export function naturalPauseAfterClause(
  text: string,
  sequence: number,
  performancePlan?: SpeechPerformancePlan,
): NaturalPause | null {
  const normalized = text.trim();
  if (!normalized) return null;

  const semanticReflection = SERIOUS_PATTERN.test(normalized) || REFLECTION_PATTERN.test(normalized);
  let reason: SilenceReason | null = semanticReflection
    ? 'reflection'
    : /[:;][\]})"'’”]*$/.test(normalized)
      ? 'thought'
      : /[.!?][\]})"'’”]*$/.test(normalized)
        ? 'clause'
        : null;
  if (!reason && !performancePlan) return null;

  if (performancePlan?.clause_pause === 'long') reason = 'reflection';
  else if (performancePlan?.clause_pause === 'medium' && reason === 'clause') reason = 'thought';
  else if (performancePlan?.clause_pause === 'short' && !semanticReflection) reason = 'clause';
  reason ??= 'clause';

  const [minimum, maximum] = pauseRange(reason, performancePlan?.clause_pause);
  let durationMs = deterministicRange(normalized, sequence, minimum, maximum);
  if (performancePlan?.pace === 'slightly_slow') durationMs = Math.round(durationMs * 1.12);
  if (performancePlan?.pace === 'slightly_fast') durationMs = Math.round(durationMs * 0.85);
  if (performancePlan?.certainty === 'low') durationMs += 25;
  return { durationMs, reason };
}

function pauseRange(
  reason: SilenceReason,
  pauseClass: SpeechPerformancePlan['clause_pause'] | undefined,
): [number, number] {
  if (reason === 'reflection') return pauseClass === 'long' ? [280, 500] : [250, 420];
  if (reason === 'thought') return pauseClass === 'long' ? [220, 360] : [140, 260];
  return pauseClass === 'short' ? [70, 125] : [80, 140];
}

function deterministicRange(text: string, sequence: number, minimum: number, maximum: number): number {
  let hash = sequence + 17;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash * 31) + text.charCodeAt(index)) >>> 0;
  }
  const span = Math.max(0, maximum - minimum);
  return minimum + (span ? hash % (span + 1) : 0);
}
