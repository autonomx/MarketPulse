export type PlaybackSegmentKind = 'speech' | 'silence' | 'cue';
export type SilenceReason = 'clause' | 'thought' | 'reflection';

export type SpeechSegmentDescriptor = {
  kind: 'speech';
  segmentId: string;
  phraseIndex: number;
  transcriptStart: number;
  transcriptEnd: number;
};

export type SilenceSegmentDescriptor = {
  kind: 'silence';
  segmentId: string;
  durationSamples: number;
  reason: SilenceReason;
  minimumFollowingSpeechSamples?: number;
};

export type CueSegmentDescriptor = {
  kind: 'cue';
  segmentId: string;
  cueId: string;
  variantId: string;
};

export type PlaybackSegmentDescriptor =
  | SpeechSegmentDescriptor
  | SilenceSegmentDescriptor
  | CueSegmentDescriptor;

export type PlaybackCounters = {
  sampleRate: number;
  renderClockSamples: number;
  segmentTimelineSamples: number;
  semanticSpeechSamples: number;
};

export type PlaybackStartPolicy = {
  notBeforeRenderSample: number;
  minimumBufferedSpeechSamples: number;
};

export type PlaybackStartPolicyMs = {
  notBeforeMs: number;
  minimumBufferedSpeechMs: number;
};

export type PlaybackSegmentEvent = PlaybackCounters & {
  type: 'segment_started' | 'segment_completed' | 'segment_interrupted' | 'segment_cancelled';
  segment_id: string;
  segment_kind: PlaybackSegmentKind;
  phrase_index?: number;
  reason?: string;
};

export function createSpeechSegmentId(traceId: string, phraseIndex: number): string {
  const safeTrace = traceId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-48);
  return `speech-${safeTrace}-p${phraseIndex}`.slice(0, 96);
}

export function createSilenceSegmentId(traceId: string, sequence: number): string {
  const safeTrace = traceId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-48);
  return `silence-${safeTrace}-s${sequence}`.slice(0, 96);
}

export function createCueSegmentId(traceId: string, cueId: string, sequence: number): string {
  const safeTrace = traceId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-40);
  const safeCue = cueId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(0, 24);
  return `cue-${safeTrace}-${safeCue}-c${sequence}`.slice(0, 96);
}

export function millisecondsToPlaybackSamples(milliseconds: number, sampleRate: number): number {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return 0;
  return Math.max(0, Math.round(milliseconds * Math.max(1, sampleRate) / 1000));
}
