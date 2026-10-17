import { LIVE_VOICE_PCM_WORKLET_NAME } from './live-voice-pcm-worklet';
import { ASSISTANT_AUDIO_DUCK_EVENT } from '../../../events/bus';

let liveVoiceDuckBridgeInstalled = false;

const activeGains = new Set<GainNode>();
let currentGain = 1;

type DuckBridgeWindow = Window & typeof globalThis & {
  AudioWorkletNode?: typeof AudioWorkletNode;
};

export function initializeLiveVoiceAudioDuckBridge(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const liveWindow = window as DuckBridgeWindow;
  const OriginalCtor = liveWindow.AudioWorkletNode;
  if (!OriginalCtor || liveVoiceDuckBridgeInstalled) return () => undefined;
  liveVoiceDuckBridgeInstalled = true;

  const WrappedCtor = new Proxy(OriginalCtor, {
    construct(target, args, newTarget) {
      const node = Reflect.construct(target, args, newTarget) as AudioWorkletNode;
      const context = args[0] as BaseAudioContext | undefined;
      const name = args[1];
      if (name === LIVE_VOICE_PCM_WORKLET_NAME && context) installGainStage(node, context);
      return node;
    },
  });
  liveWindow.AudioWorkletNode = WrappedCtor as typeof AudioWorkletNode;

  const handleDuck = (event: Event): void => {
    const detail = (event as CustomEvent<{ gain?: number }>).detail;
    currentGain = clampGain(detail?.gain);
    for (const gain of activeGains) applyGain(gain, currentGain);
  };
  window.addEventListener(ASSISTANT_AUDIO_DUCK_EVENT, handleDuck);

  return () => {
    window.removeEventListener(ASSISTANT_AUDIO_DUCK_EVENT, handleDuck);
    if (liveWindow.AudioWorkletNode === WrappedCtor) liveWindow.AudioWorkletNode = OriginalCtor;
    liveVoiceDuckBridgeInstalled = false;
    activeGains.clear();
    currentGain = 1;
  };
}

function installGainStage(node: AudioWorkletNode, context: BaseAudioContext): void {
  const gain = context.createGain();
  gain.gain.value = currentGain;
  activeGains.add(gain);
  const originalConnect = node.connect.bind(node) as (
    destination: AudioNode,
    output?: number,
    input?: number,
  ) => AudioNode;
  const originalDisconnect = node.disconnect.bind(node) as (...args: unknown[]) => void;
  let destinationBridged = false;

  node.connect = ((destination: AudioNode, output?: number, input?: number) => {
    if (destination === context.destination && output === undefined && input === undefined) {
      if (!destinationBridged) {
        originalConnect(gain);
        gain.connect(destination);
        destinationBridged = true;
      }
      return destination;
    }
    return originalConnect(destination, output, input);
  }) as typeof node.connect;

  node.disconnect = ((...args: unknown[]) => {
    activeGains.delete(gain);
    try { gain.disconnect(); } catch { /* already disconnected */ }
    originalDisconnect(...args);
  }) as typeof node.disconnect;
}

function applyGain(gain: GainNode, value: number): void {
  if (typeof gain.gain.setTargetAtTime === 'function') {
    gain.gain.setTargetAtTime(value, gain.context.currentTime, 0.025);
  } else {
    gain.gain.value = value;
  }
}

function clampGain(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(1, Math.max(0.05, value)) : 1;
}
