import { afterEach, describe, expect, it, vi } from 'vitest';

import { prewarmLiveCall } from './live-call-prewarm-controller';
import { ASSISTANT_VOICE_PERF_EVENT } from '../../../events/bus';


describe('live call prewarm controller', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not cache a partial warm-up and retries after the cooldown', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-06T00:00:00Z'));
    const stages: string[] = [];
    const recordStage = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail;
      if (typeof detail?.stage === 'string') stages.push(detail.stage);
    };
    window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, recordStage);

    let prewarmCalls = 0;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = input.toString();
      if (url.includes('/live-call/runtime')) {
        return new Response(JSON.stringify({ voice_speaker_id: 'Jinx' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/prewarm')) {
        prewarmCalls += 1;
        const fullyWarmed = prewarmCalls > 1;
        return new Response(JSON.stringify({
          ok: fullyWarmed,
          fully_warmed: fullyWarmed,
          status: fullyWarmed ? 'completed' : 'partial',
          cached: false,
          llm: { status: fullyWarmed ? 'warmed' : 'failed' },
          tts: { status: 'warmed' },
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }) as unknown as typeof fetch;

    try {
      await prewarmLiveCall('session-partial', fetchImpl);
      await prewarmLiveCall('session-partial', fetchImpl);
      expect(prewarmCalls).toBe(1);
      expect(stages).toContain('live_call_prewarm_partial');

      vi.advanceTimersByTime(5_001);
      await prewarmLiveCall('session-partial', fetchImpl);
      expect(prewarmCalls).toBe(2);
      expect(stages).toContain('live_call_prewarm_completed');
    } finally {
      window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, recordStage);
    }
  });
});