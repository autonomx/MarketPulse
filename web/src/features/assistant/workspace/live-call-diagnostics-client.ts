import { observeAssistantDiagnostic } from './live-conversation-assistant-summary';
import { emitOmnixEvent, LIVE_CALL_DIAGNOSTIC_EVENT } from '../../../events/bus';
import { api } from '../api/gateway';

const LIVE_CALL_DIAGNOSTICS_PATH = '/api/tts/live-call/diagnostics';
const FLUSH_DELAY_MS = 250;
const MAX_BATCH_EVENTS = 24;
const TRANSCRIPT_LOGGING_KEY = 'omnix.liveCall.transcriptLogging';
const TRANSCRIPT_DETAIL_PATTERN = /(^|_)(transcript|text)(_|$)/i;

export type TranscriptLoggingMode = 'none' | 'lengths_only' | 'redacted' | 'full_local_debug';

type LiveCallDiagnosticEvent = {
  source: string;
  event: string;
  details: Record<string, unknown>;
};

export type LiveCallDiagnosticsReporter = {
  traceId: string;
  record: (event: string, details?: Record<string, unknown>, source?: string) => void;
  flush: () => Promise<void>;
  close: (event?: string, details?: Record<string, unknown>) => Promise<void>;
};

export function createLiveCallDiagnosticsReporter(traceId: string): LiveCallDiagnosticsReporter {
  let queue: LiveCallDiagnosticEvent[] = [];
  let flushTimer: ReturnType<typeof window.setTimeout> | null = null;
  let closed = false;
  let pendingFlush: Promise<void> = Promise.resolve();

  const scheduleFlush = () => {
    if (flushTimer !== null || closed) return;
    flushTimer = window.setTimeout(() => {
      flushTimer = null;
      void flush();
    }, FLUSH_DELAY_MS);
  };

  const flush = async (): Promise<void> => {
    if (flushTimer !== null) {
      window.clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (queue.length === 0) return pendingFlush;
    const events = queue;
    queue = [];
    pendingFlush = pendingFlush.catch(() => undefined).then(async () => {
      try {
        await api.POST(LIVE_CALL_DIAGNOSTICS_PATH, { body: { trace_id: traceId, events }, keepalive: true });
      } catch {
        // Diagnostics must never interrupt live playback.
      }
    });
    return pendingFlush;
  };

  const record = (
    event: string,
    details: Record<string, unknown> = {},
    source = 'browser',
  ): void => {
    if (closed) return;
    observeAssistantDiagnostic(traceId, event, details);
    const mode = readTranscriptLoggingMode();
    const commonDetails = {
      client_wall_time_ms: Date.now(),
      client_monotonic_ms: performance.now(),
      document_visibility: document.visibilityState,
    };
    const persistedDetails = {
      ...commonDetails,
      ...sanitizeDiagnosticDetails(details, mode === 'full_local_debug' ? 'lengths_only' : mode),
    };
    const localDetails = mode === 'full_local_debug'
      ? { ...commonDetails, ...details }
      : persistedDetails;
    queue.push({ source, event, details: persistedDetails });
    emitOmnixEvent(LIVE_CALL_DIAGNOSTIC_EVENT, { traceId, source, event, details: localDetails });
    if (queue.length >= MAX_BATCH_EVENTS) void flush();
    else scheduleFlush();
  };

  const close = async (
    event = 'reporter_closed',
    details: Record<string, unknown> = {},
  ): Promise<void> => {
    if (closed) return pendingFlush;
    record(event, details, 'controller');
    closed = true;
    if (flushTimer !== null) {
      window.clearTimeout(flushTimer);
      flushTimer = null;
    }
    await flush();
  };

  record('reporter_created', {}, 'controller');
  return { traceId, record, flush, close };
}

export function sanitizeDiagnosticDetails(
  details: Record<string, unknown>,
  mode: TranscriptLoggingMode = 'lengths_only',
): Record<string, unknown> {
  if (mode === 'full_local_debug') return { ...details };
  const sanitized: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(details)) {
    if (!TRANSCRIPT_DETAIL_PATTERN.test(key) || typeof value !== 'string') {
      sanitized[key] = value;
      continue;
    }
    if (mode === 'none') continue;
    if (mode === 'redacted') {
      sanitized[key] = '[redacted]';
      continue;
    }
    sanitized[`${key}_chars`] = value.length;
  }
  return sanitized;
}

function readTranscriptLoggingMode(): TranscriptLoggingMode {
  try {
    const value = window.localStorage.getItem(TRANSCRIPT_LOGGING_KEY);
    if (value === 'none' || value === 'redacted' || value === 'full_local_debug') return value;
  } catch {
    // Storage access may be unavailable in hardened browser contexts.
  }
  return 'lengths_only';
}

export function createLiveCallTraceId(sessionId: string): string {
  const cryptoWithUuid = globalThis.crypto as Crypto & { randomUUID?: () => string };
  const suffix = cryptoWithUuid?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const safeSession = sessionId.replace(/[^A-Za-z0-9_.:-]+/g, '-').slice(0, 80) || 'unknown';
  return `live-call:${safeSession}:${suffix}`;
}
