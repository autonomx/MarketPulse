import { z } from 'zod';

/**
 * Runtime checks where untyped data enters the app (WP-9.3): Server-Sent Event
 * payloads, WebSocket messages and job output references. The routes and
 * their message shapes are listed in docs/architecture/api-transport-exceptions.md.
 * Objects are loose: the gateway may add fields, and readers ignore them.
 */

/**
 * One `data:` event on POST /api/chat/sessions/{session_id}/messages/stream:
 * user_message, job, text_chunk, complete, session, interrupted, done or error
 * (providers may add others, which readers ignore).
 */
export const chatStreamEventSchema = z.looseObject({
  type: z.string(),
  text: z.string().optional(),
  content: z.string().optional(),
  message: z.unknown().optional(),
  job_id: z.string().optional(),
  session: z.looseObject({ id: z.string(), messages: z.array(z.looseObject({ id: z.string(), role: z.string(), content: z.string() })) }).optional(),
});
export type ChatStreamEvent = z.infer<typeof chatStreamEventSchema>;

/**
 * A job lifecycle event on GET /events (`job.created`, `job.updated`, ...). The
 * gateway sends the event row (id, job_id, event_type, payload, created_at);
 * subscribers rely on the job id.
 */
export const jobEventSchema = z.looseObject({
  job_id: z.string(),
  event_type: z.string().optional(),
  payload: z.record(z.string(), z.unknown()).optional(),
  created_at: z.string().optional(),
});

const decimal = z.string();

/** A message on the /api/trading/stream WebSocket. */
export const tradingStreamMessageSchema = z.discriminatedUnion('type', [
  z.looseObject({
    type: z.literal('bar'),
    bar: z.looseObject({
      instrument_id: z.string(),
      binding_id: z.string(),
      interval: z.string(),
      start_time: z.string(),
      end_time: z.string(),
      open: decimal,
      high: decimal,
      low: decimal,
      close: decimal,
      volume: decimal,
      is_final: z.boolean(),
    }),
  }),
  z.looseObject({ type: z.literal('error'), code: z.string(), message: z.string() }),
]);

/** A JSON control message on the TTS PCM WebSocket (/api/tts/stream/websocket). */
export const ttsControlEventSchema = z.looseObject({
  type: z.string(),
  message: z.string().optional(),
  sample_rate: z.number().optional(),
  stream_id: z.string().optional(),
  diagnostics_log: z.string().optional(),
  partial: z.boolean().optional(),
});

/** A job output reference (JobRecord.output_refs items are untyped objects in the contract). */
export const jobOutputRefSchema = z.looseObject({
  type: z.string().nullish(),
  asset_id: z.string().nullish(),
  data_url: z.string().nullish(),
  audio_url: z.string().nullish(),
  url: z.string().nullish(),
  title: z.string().nullish(),
  mime_type: z.string().nullish(),
  duration: z.unknown().optional(),
  content: z.unknown().optional(),
  provider_fallback: z.unknown().optional(),
  provider_success: z.unknown().optional(),
  segments: z.unknown().optional(),
});
export type JobOutputRef = z.infer<typeof jobOutputRefSchema>;

/**
 * An optional field whose Python producer sends None when the value is
 * absent: null is accepted and read as undefined.
 */
export function absentWhenNull<T extends z.ZodType>(schema: T) {
  return schema.nullish().transform((value) => value ?? undefined).optional();
}

/** Parses JSON and checks it against a schema; null when either fails. */
export function parseJson<T>(schema: z.ZodType<T>, text: string): T | null {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  const parsed = schema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

/** The `data:` line of one SSE event block, parsed and checked. */
export function parseSseData<T>(schema: z.ZodType<T>, block: string): T | null {
  const line = block.split(/\r?\n/).find((entry) => entry.startsWith('data:'));
  return line ? parseJson(schema, line.slice(5).trim()) : null;
}

/** A job's output references that are objects of the expected shape. */
export function jobOutputRefs(job: { output_refs?: unknown } | null | undefined): JobOutputRef[] {
  const refs = Array.isArray(job?.output_refs) ? job.output_refs : [];
  return refs.flatMap((ref) => {
    const parsed = jobOutputRefSchema.safeParse(ref);
    return parsed.success ? [parsed.data] : [];
  });
}

/** True when a speech provider fell back (for the whole output or any segment) instead of producing the audio. */
export function isFallbackOutputRef(ref: JobOutputRef): boolean {
  if (ref.provider_fallback === true || ref.provider_success === false) return true;
  const segments = Array.isArray(ref.segments) ? ref.segments : [];
  return segments.some((segment) => {
    if (!segment || typeof segment !== 'object') return false;
    const row = segment as { provider_fallback?: unknown; provider_success?: unknown };
    return row.provider_fallback === true || row.provider_success === false;
  });
}
