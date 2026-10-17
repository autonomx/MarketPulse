import { z } from 'zod';
import { absentWhenNull as optional } from '../../../api/schemas/streams';
import type { SpeculationEvent } from './live-speculation-controller';
import type { PcmControlEvent } from './live-voice-pcm-session';
import type { StreamingSttMessage } from './live-voice-websocket';

/**
 * Runtime checks for the live voice WebSocket protocols (WP-9.3). These are
 * speech-service protocols, not gateway routes, so the TypeScript types in
 * the feature modules are the contract; each schema is annotated with its
 * type, so the compiler reports any drift between the two.
 */

const metrics = z.record(z.string(), z.number());

const segmentedResult = {
  sessionId: z.string(),
  captureEpoch: z.string(),
  segmentId: z.string(),
  sequence: z.number(),
  resultId: z.string(),
  finalizeRequestId: z.string(),
  startSample: z.number(),
  endSample: z.number(),
  text: z.string(),
};

const segmentedResultAvailable = z.looseObject({
  type: z.literal('result_available'),
  ...segmentedResult,
  acceptedThroughSample: optional(z.number()),
  provider: optional(z.string()),
  providerMetrics: optional(metrics),
});

/** A message on the live STT WebSocket (/ws/transcribe on the speech service). */
export const streamingSttMessageSchema: z.ZodType<StreamingSttMessage> = z.discriminatedUnion('type', [
  z.looseObject({
    type: z.literal('ready'),
    protocol: optional(z.string()),
    provider: optional(z.string()),
    connectionId: optional(z.string()),
    sampleRate: optional(z.number()),
    frameSamples: optional(z.number()),
    encoding: optional(z.string()),
    capabilities: optional(z.array(z.string())),
    configVersion: optional(z.string()),
    maxSegmentAudioMs: optional(z.number()),
    language: optional(z.string()),
  }),
  z.looseObject({
    type: z.literal('session_ready'),
    sessionId: z.string(),
    provider: optional(z.string()),
    results: optional(z.array(segmentedResultAvailable)),
  }),
  z.looseObject({ type: z.literal('text'), text: z.string(), segmentId: optional(z.string()), sequence: optional(z.number()) }),
  z.looseObject({ type: z.literal('partial'), text: z.string(), segmentId: z.string(), sequence: z.number() }),
  z.looseObject({
    type: z.literal('word'),
    provider: optional(z.string()),
    segmentId: z.string(),
    sequence: z.number(),
    text: z.string(),
    startMs: optional(z.number()),
    endMs: optional(z.number()),
  }),
  z.looseObject({
    type: z.literal('endpoint_score'),
    provider: optional(z.string()),
    segmentId: optional(z.string()),
    sequence: optional(z.number()),
    probability: z.number(),
    modelTimeMs: optional(z.number()),
    signal: optional(z.string()),
  }),
  z.looseObject({
    type: z.literal('endpoint_candidate'),
    provider: optional(z.string()),
    segmentId: z.string(),
    sequence: z.number(),
    probability: z.number(),
    modelTimeMs: optional(z.number()),
  }),
  z.looseObject({
    type: z.literal('preview_result'),
    provider: optional(z.string()),
    segmentId: z.string(),
    sequence: z.number(),
    previewRequestId: z.string(),
    snapshotEndSample: z.number(),
    text: z.string(),
    providerMetrics: optional(metrics),
  }),
  z.looseObject({
    type: z.enum(['flush_started', 'flush_completed', 'flush_cancelled']),
    provider: optional(z.string()),
    attemptId: optional(z.string()),
    wall_ms: optional(z.number()),
    model_ms: optional(z.number()),
    realtime_factor: optional(z.number()),
  }),
  z.looseObject({ type: z.literal('done'), ...segmentedResult }),
  z.looseObject({ type: z.literal('audio_buffered'), segmentId: z.string(), sequence: z.number(), acceptedThroughSample: z.number() }),
  z.looseObject({ type: z.literal('finalize_queued'), segmentId: z.string(), sequence: z.number(), queuedSegments: optional(z.number()) }),
  segmentedResultAvailable,
  z.looseObject({
    type: z.literal('segment_error'),
    segmentId: optional(z.string()),
    sequence: optional(z.number()),
    retryable: optional(z.boolean()),
    errorCode: optional(z.string()),
    error: optional(z.string()),
  }),
  z.looseObject({ type: z.literal('error'), errorCode: optional(z.string()), retryable: optional(z.boolean()), error: optional(z.string()) }),
]);

/** A JSON control message on the live voice PCM WebSocket (/api/tts/live-call/websocket). */
export const pcmControlEventSchema: z.ZodType<PcmControlEvent> = z.looseObject({
  type: optional(z.string()),
  message: optional(z.string()),
  sample_rate: optional(z.number()),
  stream_id: optional(z.string()),
  phrase_index: optional(z.number()),
  partial: optional(z.boolean()),
  output_id: optional(z.string()),
  generation_epoch: optional(z.number()),
  output_order: optional(z.number()),
  retry_after: optional(z.union([z.string(), z.number()])),
  capacity_saturated: optional(z.boolean()),
  segment_id: optional(z.string()),
  last_frame_index: optional(z.number()),
  generated_through_frame: optional(z.number()),
  reason: optional(z.string()),
});

/** A `data:` event on POST /api/live/speculation/sessions/{session_id}/stream. */
export const speculationEventSchema: z.ZodType<SpeculationEvent> = z.looseObject({
  type: optional(z.string()),
  generation_id: optional(z.string()),
  text: optional(z.string()),
  content: optional(z.string()),
  message: optional(z.string()),
  provider_id: z.string().nullish(),
  model_id: z.string().nullish(),
});
