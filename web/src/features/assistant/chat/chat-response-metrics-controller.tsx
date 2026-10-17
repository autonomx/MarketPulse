 
import { registerFetchMiddleware, type FetchNext } from '../../../api/fetchPipeline';
import { createLiveCallDiagnosticsReporter } from '../workspace/live-call-diagnostics-client';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

type JsonRecord = Record<string, unknown>;

type ChatResponseMetrics = {
  tokensPerSecond?: number;
  outputTokens?: number;
  generationTimeSeconds?: number;
  timeToFirstTokenSeconds?: number;
  stopReason?: string;
};

const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/[^/]+\/messages\/stream$/;

let removeMiddleware: (() => void) | null = null;

function record(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function nonNegativeInteger(value: unknown): number | undefined {
  const parsed = finiteNumber(value);
  if (parsed === undefined || parsed < 0) return undefined;
  return Math.round(parsed);
}

function nonEmptyText(value: unknown): string | undefined {
  const text = typeof value === 'string' ? value.trim() : '';
  return text || undefined;
}

export function readChatResponseMetrics(metadataValue: unknown): ChatResponseMetrics | null {
  const metadata = record(metadataValue);
  const providerMetrics = record(metadata.provider_metrics);
  const usage = record(metadata.usage);
  const provider = nonEmptyText(providerMetrics.provider)?.toLocaleLowerCase();

  const metrics: ChatResponseMetrics = {
    tokensPerSecond: finiteNumber(providerMetrics.tokens_per_second),
    outputTokens: nonNegativeInteger(
      providerMetrics.output_tokens
      ?? usage.completion_tokens
      ?? usage.output_tokens,
    ),
    generationTimeSeconds: finiteNumber(
      providerMetrics.generation_time_seconds
      ?? providerMetrics.generation_time,
    ),
    timeToFirstTokenSeconds: finiteNumber(
      providerMetrics.time_to_first_token_seconds
      ?? providerMetrics.time_to_first_token,
    ),
    stopReason: nonEmptyText(providerMetrics.stop_reason ?? providerMetrics.finish_reason),
  };

  const hasMetric = Object.values(metrics).some((value) => value !== undefined);
  if (!hasMetric || (provider && provider !== 'lmstudio')) return null;
  return metrics;
}

export function formatLmStudioStopReason(value: string): string {
  const normalized = value.trim();
  const known: Record<string, string> = {
    eosfound: 'EOS Token Found',
    stopstringfound: 'Stop String Found',
    maxpredictedtokensreached: 'Max Tokens Reached',
    userstopped: 'Stopped by User',
    stop: 'Stop',
    length: 'Max Tokens Reached',
    tool_calls: 'Tool Call',
  };
  const compact = normalized.replace(/[^a-z0-9]+/gi, '').toLocaleLowerCase();
  if (known[compact]) return known[compact];
  return normalized
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toLocaleUpperCase());
}

function formatSeconds(value: number): string {
  return `${value < 10 ? value.toFixed(2) : value.toFixed(1)}s`;
}

function MetricChip({ icon, label, title }: { icon: string; label: string; title?: string }) {
  return (
    <span className="assistant-response-metric" title={title}>
      <span className="assistant-response-metric-icon" aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </span>
  );
}

/** LM Studio's speed, token count, time and stop reason for one assistant reply (nothing for other providers). */
export function ChatResponseMetricsRow({ metadata }: { metadata: unknown }) {
  const metrics = readChatResponseMetrics(metadata);
  if (!metrics) return null;
  const firstToken = metrics.timeToFirstTokenSeconds;
  return (
    <div
      className="assistant-response-metrics"
      aria-label="LM Studio response metrics"
      data-time-to-first-token-seconds={firstToken !== undefined ? String(firstToken) : undefined}
      title={firstToken !== undefined ? `Time to first token: ${formatSeconds(firstToken)}` : undefined}
    >
      {metrics.tokensPerSecond !== undefined ? <MetricChip icon="◴" label={`${metrics.tokensPerSecond.toFixed(2)} tok/sec`} title="LM Studio generation speed" /> : null}
      {metrics.outputTokens !== undefined ? <MetricChip icon="▤" label={`${metrics.outputTokens} tokens`} title="LM Studio output tokens" /> : null}
      {metrics.generationTimeSeconds !== undefined ? <MetricChip icon="◷" label={formatSeconds(metrics.generationTimeSeconds)} title="LM Studio generation time" /> : null}
      {metrics.stopReason ? <MetricChip icon="" label={`Stop reason: ${formatLmStudioStopReason(metrics.stopReason)}`} /> : null}
    </div>
  );
}

function requestVoiceTurnId(input: RequestInfo | URL, init?: RequestInit): string | undefined {
  const body = init?.body;
  if (typeof body === 'string') {
    try {
      return nonEmptyText(record(JSON.parse(body)).live_voice_turn_id);
    } catch {
      return undefined;
    }
  }
  if (input instanceof Request) {
    return nonEmptyText(input.headers.get('x-omnix-live-voice-turn-id'));
  }
  return undefined;
}

function dispatchSseTransportObservation(response: Response, turnId?: string): void {
  const detail = {
    stage: 'chat_sse_transport_response_observed',
    timestamp: new Date().toISOString(),
    turnId,
    transportVersion: response.headers.get('x-omnix-sse-transport'),
    contentType: response.headers.get('content-type'),
    responseCloned: false,
  };
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, detail);
  if (!turnId) return;
  const reporter = createLiveCallDiagnosticsReporter(`live-call:${turnId}`);
  reporter.record('chat_sse_transport_response_observed', {
    transport_version: detail.transportVersion,
    content_type: detail.contentType,
    response_cloned: false,
  }, 'chat_response_metrics');
  void reporter.flush();
}

async function interceptChatMetricsFetch(input: RequestInfo | URL, init: RequestInit | undefined, next: FetchNext): Promise<Response> {
  const fetchImpl = next;
  const response = await fetchImpl(input, init);
  if (!response.ok) return response;

  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();

  if (method === 'POST' && CHAT_STREAM_PATH.test(url.pathname)) {
    dispatchSseTransportObservation(response, requestVoiceTurnId(input, init));
  }
  // Never clone or consume a live SSE response here. Metrics come from the
  // persisted messages the session query returns after the stream.
  return response;
}

export function initializeChatResponseMetricsController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (removeMiddleware) return () => undefined;

  removeMiddleware = registerFetchMiddleware('chat-response-metrics', interceptChatMetricsFetch);

  return () => {
    removeMiddleware?.();
    removeMiddleware = null;
  };
}

export function resetChatResponseMetricsForTests(): void {
  removeMiddleware?.();
  removeMiddleware = null;
}
