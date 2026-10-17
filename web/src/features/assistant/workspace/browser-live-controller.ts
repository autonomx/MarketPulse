import { createAudioCaptureState, type AudioCaptureState } from './audio-capture';
import {
  refreshBrowserAudioCaptureDevices,
  requestBrowserAudioCapturePermission,
  startBrowserAudioCapture,
  stopBrowserAudioCapture,
  type BrowserAudioCaptureSession,
  type BrowserAudioMediaDevices,
} from './audio-capture-browser';
import { runLiveAssistantTurn } from './live-orchestrator';
import type { ModelProvider } from './provider';
import type { SpeechAudioInput, SttServiceClient, TtsServiceClient } from './speech-services';
import type { LiveAssistantSessionController } from './useLiveAssistantSession';

export type CapturedAudioReader = (session: BrowserAudioCaptureSession) => Promise<SpeechAudioInput>;

export type BrowserLiveAssistantControllerOptions = {
  sessionId: string;
  mediaDevices: BrowserAudioMediaDevices;
  provider: ModelProvider;
  model: string;
  stt: SttServiceClient;
  tts: TtsServiceClient;
  readCapturedAudio: CapturedAudioReader;
  audioState?: AudioCaptureState;
  systemPrompt?: string;
  voice?: string;
  now?: () => string;
};

export async function createBrowserLiveAssistantController(
  options: BrowserLiveAssistantControllerOptions,
): Promise<LiveAssistantSessionController> {
  const initialAudioState = options.audioState ?? createAudioCaptureState();
  const permittedState = await requestBrowserAudioCapturePermission(options.mediaDevices, initialAudioState);
  const readyState = await refreshBrowserAudioCaptureDevices(options.mediaDevices, permittedState);

  return {
    async startCapture() {
      return startBrowserAudioCapture(options.mediaDevices, readyState);
    },
    stopCapture(session) {
      stopBrowserAudioCapture(session);
    },
    readCapturedAudio(session) {
      return options.readCapturedAudio(session);
    },
    runTurn(audio) {
      return runLiveAssistantTurn({
        sessionId: options.sessionId,
        audio,
        provider: options.provider,
        model: options.model,
        stt: options.stt,
        tts: options.tts,
        systemPrompt: options.systemPrompt,
        voice: options.voice,
        now: options.now,
      });
    },
  };
}
