// Shared by the main thread and the audio worklet processors; keep it free of DOM access.

export const LIVE_VOICE_PCM_WORKLET_NAME = 'omnix-live-voice-pcm-stream';
export const ASSISTANT_PCM_STREAM_WORKLET_NAME = 'omnix-assistant-pcm-stream';
export const LIVE_VOICE_CAPTURE_WORKLET_NAME = 'omnix-live-voice-processor';

export type LiveVoiceAvatarMouthFrame = 'closed' | 'small' | 'medium' | 'wide';

export function liveVoiceAvatarMouthFrameForRms(rms: number): LiveVoiceAvatarMouthFrame {
  if (!Number.isFinite(rms) || rms < 0.015) return 'closed';
  if (rms < 0.035) return 'small';
  if (rms < 0.075) return 'medium';
  return 'wide';
}
