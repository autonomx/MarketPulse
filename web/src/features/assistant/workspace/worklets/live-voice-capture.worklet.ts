// AudioWorkletGlobalScope: forwards each microphone render quantum to the main thread.
import { LIVE_VOICE_CAPTURE_WORKLET_NAME } from './names';

declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: new () => AudioWorkletProcessor): void;

class OmnixLiveVoiceProcessor extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0];
    if (channel && channel.length) {
      const audio = new Float32Array(channel);
      this.port.postMessage(audio, [audio.buffer]);
    }
    return true;
  }
}

registerProcessor(LIVE_VOICE_CAPTURE_WORKLET_NAME, OmnixLiveVoiceProcessor);
