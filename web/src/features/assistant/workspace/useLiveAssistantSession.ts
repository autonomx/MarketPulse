import { useCallback, useState } from 'react';
import type { BrowserAudioCaptureSession } from './audio-capture-browser';
import type { LiveAssistantTurnResult } from './live-orchestrator';
import type { SpeechAudioInput } from './speech-services';

export type LiveAssistantSessionStatus = 'idle' | 'capturing' | 'processing' | 'ready' | 'error';

export type LiveAssistantSessionState = {
  status: LiveAssistantSessionStatus;
  captureSession?: BrowserAudioCaptureSession;
  result?: LiveAssistantTurnResult;
  error?: string;
};

export type LiveAssistantSessionController = {
  startCapture(): Promise<BrowserAudioCaptureSession>;
  stopCapture(session: BrowserAudioCaptureSession): void;
  readCapturedAudio(session: BrowserAudioCaptureSession): Promise<SpeechAudioInput>;
  runTurn(audio: SpeechAudioInput): Promise<LiveAssistantTurnResult>;
};

export type LiveAssistantSessionApi = LiveAssistantSessionState & {
  start(): Promise<void>;
  submitCapturedTurn(): Promise<void>;
  stop(): void;
  reset(): void;
};

const initialState: LiveAssistantSessionState = { status: 'idle' };

export function useLiveAssistantSession(controller: LiveAssistantSessionController): LiveAssistantSessionApi {
  const [state, setState] = useState<LiveAssistantSessionState>(initialState);

  const start = useCallback(async () => {
    try {
      const captureSession = await controller.startCapture();
      setState({ status: 'capturing', captureSession });
    } catch (error) {
      setState({ status: 'error', error: errorMessage(error) });
    }
  }, [controller]);

  const submitCapturedTurn = useCallback(async () => {
    const captureSession = state.captureSession;

    if (!captureSession) {
      setState({ status: 'error', error: 'No active capture session.' });
      return;
    }

    setState((current) => ({ ...current, status: 'processing', error: undefined }));

    try {
      const audio = await controller.readCapturedAudio(captureSession);
      const result = await controller.runTurn(audio);
      controller.stopCapture(captureSession);
      setState({ status: 'ready', result });
    } catch (error) {
      controller.stopCapture(captureSession);
      setState({ status: 'error', error: errorMessage(error) });
    }
  }, [controller, state.captureSession]);

  const stop = useCallback(() => {
    if (state.captureSession) {
      controller.stopCapture(state.captureSession);
    }

    setState(initialState);
  }, [controller, state.captureSession]);

  const reset = useCallback(() => setState(initialState), []);

  return {
    ...state,
    start,
    submitCapturedTurn,
    stop,
    reset,
  };
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }

  return 'Live assistant session failed.';
}
