/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { initializeLiveVoiceAudioDuckBridge } from './live-voice-audio-duck-bridge';
import { LIVE_VOICE_PCM_WORKLET_NAME } from './live-voice-pcm-worklet';

class FakeAudioParam {
  value = 1;
  setTargetAtTime = vi.fn((value: number) => { this.value = value; });
}

class FakeGainNode {
  gain = new FakeAudioParam();
  context: FakeAudioContext;
  connect = vi.fn();
  disconnect = vi.fn();

  constructor(context: FakeAudioContext) {
    this.context = context;
  }
}

class FakeAudioContext {
  currentTime = 0;
  destination = {} as AudioDestinationNode;
  gains: FakeGainNode[] = [];

  createGain(): GainNode {
    const gain = new FakeGainNode(this);
    this.gains.push(gain);
    return gain as unknown as GainNode;
  }
}

class FakeAudioWorkletNode {
  context: FakeAudioContext;
  rawConnect = vi.fn((destination: AudioNode) => destination);
  connect = (destination: AudioNode) => this.rawConnect(destination);
  rawDisconnect = vi.fn();
  disconnect = () => this.rawDisconnect();

  constructor(context: FakeAudioContext, _name: string) {
    this.context = context;
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice audio duck bridge', () => {
  it('inserts a gain only for the live PCM worklet and restores volume', () => {
    vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode as unknown as typeof AudioWorkletNode);
    const cleanup = initializeLiveVoiceAudioDuckBridge();
    const context = new FakeAudioContext();
    const liveNode = new window.AudioWorkletNode(
      context as unknown as BaseAudioContext,
      LIVE_VOICE_PCM_WORKLET_NAME,
    );
    liveNode.connect(context.destination);

    expect(context.gains).toHaveLength(1);
    expect((liveNode as unknown as FakeAudioWorkletNode).rawConnect).toHaveBeenCalledWith(context.gains[0]);
    expect(context.gains[0].connect).toHaveBeenCalledWith(context.destination);

    window.dispatchEvent(new CustomEvent('omnix:assistant-audio-duck', { detail: { gain: 0.18 } }));
    expect(context.gains[0].gain.setTargetAtTime).toHaveBeenLastCalledWith(0.18, 0, 0.025);

    window.dispatchEvent(new CustomEvent('omnix:assistant-audio-duck', { detail: { gain: 1 } }));
    expect(context.gains[0].gain.setTargetAtTime).toHaveBeenLastCalledWith(1, 0, 0.025);
    cleanup();
  });

  it('leaves unrelated worklets connected directly', () => {
    vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode as unknown as typeof AudioWorkletNode);
    const cleanup = initializeLiveVoiceAudioDuckBridge();
    const context = new FakeAudioContext();
    const node = new window.AudioWorkletNode(context as unknown as BaseAudioContext, 'other-worklet');
    node.connect(context.destination);

    expect(context.gains).toHaveLength(0);
    expect((node as unknown as FakeAudioWorkletNode).rawConnect).toHaveBeenCalledWith(context.destination);
    cleanup();
  });
});
