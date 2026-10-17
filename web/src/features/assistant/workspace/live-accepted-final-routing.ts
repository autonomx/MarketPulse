import type { OverlapIntent } from './live-voice-overlap-classifier';

export type AcceptedFinalSuppressionReason =
  | 'hard_stop'
  | 'backchannel'
  | 'noise'
  | 'empty_transcript'
  | null;

/**
 * Suppress only finals that are provably non-conversational.
 *
 * A non-empty `uncertain` overlap is meaningful user speech until proven
 * otherwise. It must continue through the normal coordinator so the active
 * assistant response can be interrupted or superseded by the new comment.
 */
export function acceptedFinalSuppressionReason(
  text: string,
  overlapIntent: OverlapIntent | null,
): AcceptedFinalSuppressionReason {
  if (!text.trim()) return 'empty_transcript';
  if (
    overlapIntent === 'hard_stop'
    || overlapIntent === 'backchannel'
    || overlapIntent === 'noise'
  ) {
    return overlapIntent;
  }
  return null;
}
