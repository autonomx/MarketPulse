/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { describe, expect, it, vi } from 'vitest';

import {
  initializeLiveSpeculationRuntime,
  liveSubmissionRequestMatches,
} from './live-speculation-runtime';
import { pipelineFetch, registerFetchMiddleware } from '../../../api/fetchPipeline';
import { LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

const submission = {
  sessionId: 'chat:one',
  text: 'Hello',
  source: 'live_coordination' as const,
  interrupted: false,
  segmentId: 'segment-1',
  sourceSequence: 1,
};

describe('live speculation runtime routing', () => {
  it('matches only the active live submission chat stream', () => {
    expect(liveSubmissionRequestMatches(
      submission,
      '/api/chat/sessions/chat%3Aone/messages/stream',
      { method: 'POST' },
    )).toBe(true);
    expect(liveSubmissionRequestMatches(
      submission,
      '/api/chat/sessions/chat%3Atwo/messages/stream',
      { method: 'POST' },
    )).toBe(false);
    expect(liveSubmissionRequestMatches(
      submission,
      '/api/chat/sessions/chat%3Aone/messages/stream',
      { method: 'GET' },
    )).toBe(false);
  });

  it('keeps speculation inside a later live-audio middleware', async () => {
    const priorFetch = window.fetch;
    const order: string[] = [];
    const applicationFetch = vi.fn(async () => {
      order.push('application');
      return new Response('ok', { status: 200 });
    });
    window.fetch = applicationFetch as typeof window.fetch;

    const cleanup = initializeLiveSpeculationRuntime();
    const removeAudio = registerFetchMiddleware('test-live-audio', (input, init, next) => {
      order.push('audio');
      return next(input, init);
    });

    try {
      const response = await pipelineFetch(
        '/api/chat/sessions/chat%3Aone/messages/stream',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: 'Hello' }),
        },
      );
      expect(await response.text()).toBe('ok');
      expect(order).toEqual(['audio', 'application']);
      expect(applicationFetch).toHaveBeenCalledOnce();
    } finally {
      removeAudio();
      cleanup();
      window.fetch = priorFetch;
    }
  });

  it('suppresses stale partial and candidate events after authoritative final', () => {
    const priorFetch = window.fetch;
    window.fetch = vi.fn(async () => new Response('ok')) as typeof window.fetch;
    const cleanup = initializeLiveSpeculationRuntime();
    const partialListener = vi.fn();
    const candidateListener = vi.fn();
    window.addEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, partialListener);
    window.addEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, candidateListener);
    const finalized = {
      chatSessionId: 'chat:one',
      segmentId: 'segment-final',
      sourceSequence: 7,
      text: 'Final transcript',
    };

    try {
      window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_FINAL_EVENT, {
        detail: finalized,
      }));
      window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, {
        detail: { ...finalized, text: 'Stale corrected partial' },
      }));
      window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_CANDIDATE_EVENT, {
        detail: { ...finalized, text: 'Stale corrected partial', probability: 0.99 },
      }));
      window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, {
        detail: {
          chatSessionId: 'chat:one',
          segmentId: 'segment-next',
          sourceSequence: 8,
          text: 'Fresh partial',
        },
      }));

      expect(partialListener).toHaveBeenCalledOnce();
      expect(candidateListener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, partialListener);
      window.removeEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, candidateListener);
      cleanup();
      window.fetch = priorFetch;
    }
  });
});
