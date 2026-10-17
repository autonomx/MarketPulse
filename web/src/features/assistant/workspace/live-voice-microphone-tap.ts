export type LiveVoiceMicrophoneTap = {
  sampleRate: number;
  read: () => Float32Array;
  close: () => Promise<void>;
};

type AudioWindow = Window & typeof globalThis & {
  webkitAudioContext?: typeof AudioContext;
};

export async function createLiveVoiceMicrophoneTap(stream: MediaStream): Promise<LiveVoiceMicrophoneTap | null> {
  if (typeof window === 'undefined') return null;
  const AudioContextCtor = window.AudioContext || (window as AudioWindow).webkitAudioContext;
  if (!AudioContextCtor) return null;
  const context = new AudioContextCtor({ latencyHint: 'interactive' });
  try {
    if (context.state !== 'running') await context.resume();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    const silent = context.createGain();
    silent.gain.value = 0;
    source.connect(analyser);
    analyser.connect(silent);
    silent.connect(context.destination);
    return {
      sampleRate: context.sampleRate,
      read: () => {
        const frame = new Float32Array(analyser.fftSize);
        analyser.getFloatTimeDomainData(frame);
        return frame;
      },
      close: async () => {
        source.disconnect();
        analyser.disconnect();
        silent.disconnect();
        await context.close().catch(() => undefined);
      },
    };
  } catch {
    await context.close().catch(() => undefined);
    return null;
  }
}
