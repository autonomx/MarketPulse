import { describe, expect, it } from 'vitest';
import { parseJson } from '../../../api/schemas/streams';
import { pcmControlEventSchema, speculationEventSchema, streamingSttMessageSchema } from './live-voice-messages';

// Shaped as stt_live_websocket.py sends it.
const resultAvailable = {
  type: 'result_available',
  sessionId: 'session-1',
  captureEpoch: 'epoch-1',
  segmentId: 'segment-1',
  sequence: 3,
  resultId: 'result-1',
  finalizeRequestId: 'finalize-1',
  startSample: 0,
  endSample: 16000,
  text: 'hello there',
  acceptedThroughSample: 16000,
};

describe('live voice message schemas', () => {
  it('accepts the STT messages the speech service sends', () => {
    expect(parseJson(streamingSttMessageSchema, JSON.stringify(resultAvailable))).toEqual(resultAvailable);
    expect(parseJson(streamingSttMessageSchema, JSON.stringify({ type: 'session_ready', sessionId: 'session-1', provider: 'parakeet', results: [resultAvailable] })))
      .toMatchObject({ type: 'session_ready', results: [resultAvailable] });
    expect(parseJson(streamingSttMessageSchema, '{"type":"finalize_queued","segmentId":"segment-1","sequence":3}'))
      .toEqual({ type: 'finalize_queued', segmentId: 'segment-1', sequence: 3 });
  });

  it('reads None from a Python producer as an absent value', () => {
    const ready = parseJson(streamingSttMessageSchema, '{"type":"ready","provider":"parakeet","language":null,"sampleRate":16000}');

    expect(ready).toEqual({ type: 'ready', provider: 'parakeet', language: undefined, sampleRate: 16000 });
  });

  it('refuses unknown STT message types and messages missing what handlers read', () => {
    expect(parseJson(streamingSttMessageSchema, '{"type":"telemetry"}')).toBeNull();
    expect(parseJson(streamingSttMessageSchema, '{"type":"partial","text":"hi"}')).toBeNull();
    expect(parseJson(streamingSttMessageSchema, JSON.stringify({ ...resultAvailable, endSample: '16000' }))).toBeNull();
  });

  it('checks PCM control and speculation events field by field', () => {
    expect(parseJson(pcmControlEventSchema, '{"type":"phrase_done","phrase_index":2,"retry_after":"1.5"}'))
      .toEqual({ type: 'phrase_done', phrase_index: 2, retry_after: '1.5' });
    expect(parseJson(pcmControlEventSchema, '{"type":"phrase_done","phrase_index":"2"}')).toBeNull();
    expect(parseJson(pcmControlEventSchema, '"text"')).toBeNull();
    expect(parseJson(speculationEventSchema, '{"type":"delta","text":"Hi","provider_id":null}'))
      .toEqual({ type: 'delta', text: 'Hi', provider_id: null });
  });
});
