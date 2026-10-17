import { describe, expect, it } from 'vitest';

import { DesktopCompanionRuntime } from './desktop-companion-runtime';

function runtime(visible = true) {
  let generation = 0;
  return new DesktopCompanionRuntime({
    createGeneration: () => `capture-${++generation}`,
    pageVisible: () => visible,
  });
}

describe('desktop companion runtime', () => {
  it('requires sharing and a successful preflight before watch mode', () => {
    const subject = runtime();
    expect(() => subject.enableWatch()).toThrow(/sharing/i);

    subject.beginSharing({ sessionId: 'chat-1', sourceFingerprint: 'screen-1' });
    expect(() => subject.enableWatch()).toThrow(/preflight/i);

    subject.setPreflight({ ready: true, modelId: 'vision-model', endpoint: 'http://localhost:1234/v1', remote: false, reason: 'ready' });
    subject.enableWatch();
    expect(subject.getSnapshot().phase).toBe('watching_idle');
    expect(subject.getSnapshot().watchEnabled).toBe(true);
  });

  it('invalidates stale results when the session binding changes', () => {
    const subject = runtime();
    const first = subject.beginSharing({ sessionId: 'chat-1', sourceFingerprint: 'screen-1' });
    subject.setPreflight({ ready: true, modelId: 'vision-model', endpoint: 'local', remote: false, reason: 'ready' });
    subject.enableWatch();
    const request = subject.nextSequence();

    subject.rebindSession('chat-2', 'character-2');
    const second = subject.getSnapshot().binding;

    expect(second?.captureGeneration).not.toBe(first.captureGeneration);
    expect(subject.acceptsResult({ captureGeneration: request.binding.captureGeneration, clientSequence: request.clientSequence })).toBe(false);
    expect(subject.getSnapshot().watchEnabled).toBe(false);
  });

  it('pauses when the page becomes hidden and stop-and-forget clears identity', () => {
    const subject = runtime();
    subject.beginSharing({ sessionId: 'chat-1', sourceFingerprint: 'screen-1' });
    subject.setPreflight({ ready: true, modelId: 'vision-model', endpoint: 'local', remote: false, reason: 'ready' });
    subject.enableWatch();

    subject.handleVisibility(false);
    expect(subject.getSnapshot().phase).toBe('paused');
    expect(subject.getSnapshot().lastError).toBe('page_hidden');

    subject.stopAndForget();
    expect(subject.getSnapshot()).toMatchObject({ phase: 'off', binding: null, watchEnabled: false, clientSequence: 0 });
  });
});
