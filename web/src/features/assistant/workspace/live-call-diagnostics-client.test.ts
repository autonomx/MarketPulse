/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createLiveCallDiagnosticsReporter,
  sanitizeDiagnosticDetails,
} from './live-call-diagnostics-client';

function successfulFetch(_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> {
  return Promise.resolve(new Response(null, { status: 200 }));
}

describe('live call diagnostics client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('emits a sanitized local diagnostic event before nonblocking upload', async () => {
    const fetchMock = vi.fn(successfulFetch);
    vi.stubGlobal('fetch', fetchMock);
    const listener = vi.fn();
    window.addEventListener('omnix:live-call-diagnostic', listener);
    const reporter = createLiveCallDiagnosticsReporter('live-call:test');

    reporter.record('llm_text_chunk_received', {
      transcript: 'private text',
      text: 'private assistant response',
      text_length: 12,
    }, 'controller');
    await reporter.close();

    const event = listener.mock.calls
      .map((call) => call[0] as CustomEvent)
      .find((candidate) => candidate.detail.event === 'llm_text_chunk_received');
    expect(event?.detail).toEqual(expect.objectContaining({
      traceId: 'live-call:test',
      source: 'controller',
      event: 'llm_text_chunk_received',
      details: expect.objectContaining({
        transcript_chars: 12,
        text_chars: 26,
        text_length: 12,
      }),
    }));
    expect(event?.detail.details.transcript).toBeUndefined();
    expect(event?.detail.details.text).toBeUndefined();
    expect(fetchMock).toHaveBeenCalled();
    window.removeEventListener('omnix:live-call-diagnostic', listener);
  });

  it('keeps full debug content in the local event but never uploads it', async () => {
    window.localStorage.setItem('omnix.liveCall.transcriptLogging', 'full_local_debug');
    const fetchMock = vi.fn(successfulFetch);
    vi.stubGlobal('fetch', fetchMock);
    const listener = vi.fn();
    window.addEventListener('omnix:live-call-diagnostic', listener);
    const reporter = createLiveCallDiagnosticsReporter('live-call:debug');

    reporter.record('llm_text_chunk_received', { text: 'local secret' }, 'controller');
    await reporter.close();

    const local = listener.mock.calls
      .map((call) => call[0] as CustomEvent)
      .find((candidate) => candidate.detail.event === 'llm_text_chunk_received');
    expect(local?.detail.details.text).toBe('local secret');

    const lastCall = fetchMock.mock.calls.at(-1);
    const uploadedBody = JSON.parse(String(lastCall?.[1]?.body)) as {
      events: Array<{ event: string; details: Record<string, unknown> }>;
    };
    const uploaded = uploadedBody.events.find((event) => event.event === 'llm_text_chunk_received');
    expect(uploaded?.details.text).toBeUndefined();
    expect(uploaded?.details.text_chars).toBe(12);
    window.removeEventListener('omnix:live-call-diagnostic', listener);
  });

  it('keeps transcript sanitization policy unchanged', () => {
    expect(sanitizeDiagnosticDetails({ transcript: 'secret' }, 'none')).toEqual({});
    expect(sanitizeDiagnosticDetails({ transcript: 'secret' }, 'redacted')).toEqual({ transcript: '[redacted]' });
    expect(sanitizeDiagnosticDetails({ transcript: 'secret' }, 'lengths_only')).toEqual({ transcript_chars: 6 });
    expect(sanitizeDiagnosticDetails({ text: 'secret', text_length: 6, text_chunk_index: 2 }, 'lengths_only')).toEqual({
      text_chars: 6,
      text_length: 6,
      text_chunk_index: 2,
    });
  });
});
