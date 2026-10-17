/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { describe, expect, it, vi } from 'vitest';

import { LiveChatSubmissionGateway } from './live-chat-submission-gateway';
import { pipelineFetch } from '../../../api/fetchPipeline';

const firstInput = {
  sessionId: 'chat:test',
  text: 'first',
  source: 'live_coordination' as const,
  interrupted: false,
  segmentId: 'segment-1',
  sourceSequence: 1,
};

describe('live chat submission interruption handoff', () => {
  it('settles the opened prior handler before starting an interrupted replacement', async () => {
    const gateway = new LiveChatSubmissionGateway();
    const originalFetch = window.fetch;
    let fetchCount = 0;
    let priorSettled = false;
    let replacementStarted = false;

    window.fetch = vi.fn(async (_request, init) => {
      fetchCount += 1;
      if (fetchCount !== 1) return new Response('replacement', { status: 200 });

      let bodyController!: ReadableStreamDefaultController<Uint8Array>;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          bodyController = controller;
        },
      });
      init?.signal?.addEventListener('abort', () => {
        bodyController.error(new Error('superseded'));
      }, { once: true });
      return new Response(body, { status: 200 });
    }) as typeof window.fetch;

    gateway.register(async (submission) => {
      if (submission.sourceSequence === 1) {
        try {
          const response = await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', {
            method: 'POST',
          });
          await response.text();
        } finally {
          priorSettled = true;
        }
        return;
      }

      replacementStarted = true;
      expect(priorSettled).toBe(true);
      const response = await pipelineFetch('/api/chat/sessions/chat%3Atest/messages/stream', {
        method: 'POST',
      });
      await response.text();
    });

    try {
      await expect(gateway.submit(firstInput)).resolves.toBeUndefined();
      expect(priorSettled).toBe(false);

      await expect(gateway.submit({
        ...firstInput,
        text: 'replacement',
        interrupted: true,
        segmentId: 'segment-2',
        sourceSequence: 2,
      })).resolves.toBeUndefined();

      expect(priorSettled).toBe(true);
      expect(replacementStarted).toBe(true);
      expect(fetchCount).toBe(2);
    } finally {
      window.fetch = originalFetch;
    }
  });
});
