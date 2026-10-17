// The live voice PCM stream processor runs in the AudioWorkletGlobalScope from its own
// module file (worklets/live-voice-pcm-stream.worklet.ts); Vite bundles it and gives its URL.
export {
  LIVE_VOICE_PCM_WORKLET_NAME,
  liveVoiceAvatarMouthFrameForRms,
  type LiveVoiceAvatarMouthFrame,
} from './worklets/names';
export { default as LIVE_VOICE_PCM_WORKLET_URL } from './worklets/live-voice-pcm-stream.worklet?worker&url';
