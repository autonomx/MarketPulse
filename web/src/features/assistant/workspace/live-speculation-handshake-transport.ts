import { registerFetchMiddleware } from '../../../api/fetchPipeline';
import type { components } from '../api/generated';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

const LEGACY_SPECULATION_STREAM_PATH = /^\/api\/live\/speculation\/sessions\/([^/]+)\/stream$/;
const CLIENT_GENERATION_PREFIX = 'spec-client-';

type SpeculationHandshake = {
  ok?: boolean;
  generation_id?: string;
  segment_id?: string;
  source_sequence?: number;
  provider_id?: string | null;
  model_id?: string | null;
};

// The intercepted request body of /start and /start-stream.
type LiveSpeculationRequest = components['schemas']['LiveSpeculationRequest'];

type PriorityRequestInit = RequestInit & {
  priority?: 'high' | 'low' | 'auto';
};

let removeMiddleware: (() => void) | null = null;

export function initializeLiveSpeculationHandshakeTransport(): () => void {
  if (typeof window === 'undefined' || typeof window.fetch !== 'function') {
    return () => undefined;
  }
  if (removeMiddleware) return () => undefined;
  const remove = registerFetchMiddleware('live-speculation-handshake', async (input, init, next) => {
    const bridged = await bridgeLiveSpeculationHandshakeRequest(next as typeof fetch, input, init);
    return bridged ?? next(input, init);
  });
  removeMiddleware = remove;
  return () => {
    remove();
    if (removeMiddleware === remove) removeMiddleware = null;
  };
}

export async function bridgeLiveSpeculationHandshakeRequest(
  fetchImpl: typeof fetch,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response | null> {
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  if (method !== 'POST') return null;

  const rawUrl = typeof input === 'string' || input instanceof URL
    ? input.toString()
    : input.url;
  const baseUrl = typeof window !== 'undefined'
    ? window.location.origin
    : 'http://localhost';
  const url = new URL(rawUrl, baseUrl);
  const match = LEGACY_SPECULATION_STREAM_PATH.exec(url.pathname);
  if (!match) return null;

  const requestBody = await requestBodyText(input, init);
  const payload = parseStartPayload(requestBody);
  if (!payload) return null;

  const sessionId = decodeURIComponent(match[1]);
  const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
  const generationId = createClientGenerationId();
  return createOptimisticSpeculationResponse(
    fetchImpl,
    sessionId,
    payload,
    generationId,
    signal,
  );
}

function createOptimisticSpeculationResponse(
  fetchImpl: typeof fetch,
  sessionId: string,
  payload: LiveSpeculationRequest,
  clientGenerationId: string,
  sourceSignal?: AbortSignal | null,
): Response {
  const encoder = new TextEncoder();
  const abortController = new AbortController();
  let closed = false;
  let cancellationSent = false;
  let serverGenerationId = clientGenerationId;

  const cancelGeneration = () => {
    if (cancellationSent) return;
    cancellationSent = true;
    const generationIds = new Set([clientGenerationId, serverGenerationId]);
    generationIds.forEach((generationId) => {
      void requestGenerationCancellation(fetchImpl, sessionId, generationId);
    });
  };

  if (sourceSignal) {
    const abort = () => {
      abortController.abort(sourceSignal.reason);
      cancelGeneration();
    };
    if (sourceSignal.aborted) abort();
    else sourceSignal.addEventListener('abort', abort, { once: true });
  }

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const emit = (value: string | Uint8Array): void => {
        if (closed) return;
        controller.enqueue(typeof value === 'string' ? encoder.encode(value) : value);
      };

      // The controller can route an accepted final immediately. The server uses
      // the same collision-checked generation ID once the background request
      // arrives, so no network handshake is required on the final-turn path.
      emit(sse({
        type: 'speculation_started',
        generation_id: clientGenerationId,
        segment_id: payload.segment_id,
        source_sequence: payload.source_sequence,
        provider_id: bodyString(payload.provider_id) ?? null,
        model_id: bodyString(payload.model_id) ?? null,
        optimistic_transport: true,
      }));
      dispatchPerformance('llm_speculation_client_generation_allocated', {
        sessionId,
        generationId: clientGenerationId,
        transport: 'optimistic-inline-v2',
      });

      void openAndPipeSpeculation(
        fetchImpl,
        sessionId,
        payload,
        clientGenerationId,
        abortController.signal,
        (generationId) => { serverGenerationId = generationId; },
        emit,
      ).then(() => {
        if (!closed) {
          closed = true;
          controller.close();
        }
      }).catch((error: unknown) => {
        if (closed) return;
        if (abortController.signal.aborted) {
          closed = true;
          controller.close();
          return;
        }
        emit(sse({
          type: 'error',
          generation_id: serverGenerationId,
          message: error instanceof Error
            ? error.message
            : 'Speculative generation stream failed.',
        }));
        emit(sse({ type: 'done', generation_id: serverGenerationId }));
        closed = true;
        controller.close();
      });
    },
    cancel(reason) {
      closed = true;
      abortController.abort(reason);
      cancelGeneration();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-store',
      'X-Omnix-Speculation-Generation-Id': clientGenerationId,
      'X-Omnix-Speculation-Transport': 'optimistic-inline-v2',
    },
  });
}

async function openAndPipeSpeculation(
  fetchImpl: typeof fetch,
  sessionId: string,
  payload: LiveSpeculationRequest,
  clientGenerationId: string,
  signal: AbortSignal,
  setServerGenerationId: (generationId: string) => void,
  emit: (value: Uint8Array | string) => void,
): Promise<void> {
  const inlineStartedAt = now();
  const inlineResponse = await fetchImpl(
    `/api/live/speculation/sessions/${encodeURIComponent(sessionId)}/start-stream`,
    highPriorityRequest({
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ ...payload, generation_id: clientGenerationId }),
      signal,
    }),
  );
  if (
    inlineResponse.ok
    && responseContentType(inlineResponse).startsWith('text/event-stream')
  ) {
    const serverGenerationId = inlineResponse.headers.get(
      'X-Omnix-Speculation-Generation-Id',
    ) ?? clientGenerationId;
    if (serverGenerationId !== clientGenerationId) {
      throw new Error('Inline speculation generation id did not match the client allocation.');
    }
    setServerGenerationId(serverGenerationId);
    dispatchPerformance('llm_speculation_inline_stream_opened', {
      sessionId,
      generationId: serverGenerationId,
      openMs: now() - inlineStartedAt,
      optimistic: true,
      transport: inlineResponse.headers.get('X-Omnix-Speculation-Transport')
        ?? 'inline-v2-client-id',
    });
    await pipeResponseBody(inlineResponse, signal, emit);
    return;
  }
  if (inlineResponse.status !== 404 && inlineResponse.status !== 405) {
    throw new Error(`Inline speculation stream failed with status ${inlineResponse.status}.`);
  }

  const handshakeStartedAt = now();
  const startResponse = await fetchImpl(
    `/api/live/speculation/sessions/${encodeURIComponent(sessionId)}/start`,
    highPriorityRequest({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    }),
  );
  if (!startResponse.ok) {
    throw new Error(`Speculation handshake failed with status ${startResponse.status}.`);
  }

  const handshake = await readHandshake(startResponse);
  if (!validHandshake(handshake)) {
    throw new Error('Speculation handshake response was invalid.');
  }
  setServerGenerationId(handshake.generation_id);
  emit(sse({
    type: 'speculation_started',
    generation_id: handshake.generation_id,
    segment_id: handshake.segment_id,
    source_sequence: handshake.source_sequence,
    provider_id: handshake.provider_id ?? null,
    model_id: handshake.model_id ?? null,
    optimistic_transport: false,
  }));
  dispatchPerformance('llm_speculation_handshake_ready', {
    sessionId,
    generationId: handshake.generation_id,
    handshakeMs: now() - handshakeStartedAt,
    transport: 'two-request-fallback',
  });
  const generationResponse = await fetchImpl(
    `/api/live/speculation/sessions/${encodeURIComponent(sessionId)}/${encodeURIComponent(handshake.generation_id)}/stream`,
    highPriorityRequest({
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      signal,
    }),
  );
  if (!generationResponse.ok || !generationResponse.body) {
    throw new Error(`Speculation generation stream failed with status ${generationResponse.status}.`);
  }
  await pipeResponseBody(generationResponse, signal, emit);
}

async function pipeResponseBody(
  response: Response,
  signal: AbortSignal,
  emit: (chunk: Uint8Array) => void,
): Promise<void> {
  if (!response.body) throw new Error('Speculation stream response body was empty.');
  const reader = response.body.getReader();
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value?.byteLength) emit(value);
    }
  } finally {
    reader.releaseLock();
  }
}

async function requestGenerationCancellation(
  fetchImpl: typeof fetch,
  sessionId: string,
  generationId: string,
): Promise<void> {
  try {
    await fetchImpl(
      `/api/live/speculation/sessions/${encodeURIComponent(sessionId)}/${encodeURIComponent(generationId)}/cancel`,
      {
        method: 'POST',
        headers: { Accept: 'application/json' },
        keepalive: true,
      },
    );
  } catch {
    // Cancellation is best effort; the server also cancels on stream detach/TTL.
  }
}

function highPriorityRequest(init: RequestInit): PriorityRequestInit {
  return { ...init, priority: 'high' };
}

async function requestBodyText(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<string | null> {
  if (typeof init?.body === 'string') return init.body;
  if (input instanceof Request) {
    try {
      return await input.clone().text();
    } catch {
      return null;
    }
  }
  return null;
}

function parseStartPayload(body: string | null): LiveSpeculationRequest | null {
  if (!body) return null;
  try {
    const value = JSON.parse(body) as unknown;
    if (!value || typeof value !== 'object') return null;
    const payload = value as Record<string, unknown>;
    if (
      typeof payload.content !== 'string'
      || !payload.content.trim()
      || typeof payload.segment_id !== 'string'
      || !payload.segment_id.trim()
      || typeof payload.source_sequence !== 'number'
      || !Number.isInteger(payload.source_sequence)
      || payload.source_sequence < 0
    ) {
      return null;
    }
    return {
      ...payload,
      content: payload.content,
      segment_id: payload.segment_id,
      source_sequence: payload.source_sequence,
      provider_id: bodyString(payload.provider_id) ?? null,
      model_id: bodyString(payload.model_id) ?? null,
    };
  } catch {
    return null;
  }
}

async function readHandshake(response: Response): Promise<SpeculationHandshake | null> {
  try {
    const payload = await response.json() as unknown;
    return payload && typeof payload === 'object'
      ? payload as SpeculationHandshake
      : null;
  } catch {
    return null;
  }
}

function validHandshake(
  handshake: SpeculationHandshake | null,
): handshake is Required<Pick<SpeculationHandshake, 'generation_id' | 'segment_id' | 'source_sequence'>>
  & SpeculationHandshake {
  return Boolean(
    handshake
    && handshake.ok !== false
    && typeof handshake.generation_id === 'string'
    && handshake.generation_id.length > 0
    && typeof handshake.segment_id === 'string'
    && handshake.segment_id.length > 0
    && typeof handshake.source_sequence === 'number',
  );
}

function createClientGenerationId(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID().replaceAll('-', '')
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${CLIENT_GENERATION_PREFIX}${random}`;
}

function responseContentType(response: Response): string {
  return (response.headers.get('Content-Type') ?? '').toLowerCase();
}

function bodyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function sse(payload: Record<string, unknown>): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function dispatchPerformance(stage: string, detail: Record<string, unknown>): void {
  if (typeof window === 'undefined') return;
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...detail });
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}
