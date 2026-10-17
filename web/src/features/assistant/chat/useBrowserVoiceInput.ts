import { useRef, useState } from 'react';
import type { UseFormSetValue } from 'react-hook-form';
import { createFetchSpeechServiceTransport, createSttServiceClient, type AssistantWorkspaceRuntimeConfig } from '../workspace';
import {
  DEFAULT_SPEECH_LANGUAGE,
  getSpeechRecognitionConstructor,
  mergeTranscript,
  type BrowserSpeechRecognition,
  type ChatbotFormValues,
  type UtilityPanel,
  type VoiceCaptureMode,
} from './chatbotWorkspaceModel';

type BrowserVoiceInputOptions = {
  runtimeConfig: AssistantWorkspaceRuntimeConfig;
  setValue: UseFormSetValue<ChatbotFormValues>;
  setAudioStatus: (status: string | null) => void;
  setActiveUtilityPanel: (panel: UtilityPanel) => void;
  /** Called with the whole transcript each time speech is finalized. */
  scheduleLiveVoiceAutoSend: (content: string) => void;
  clearLiveVoiceAutoSendTimer: () => void;
};

/**
 * Voice input without the streaming capture controller: browser speech
 * recognition, or a recording sent to the STT service. Speech goes into the
 * message composer.
 */
export function useBrowserVoiceInput({
  runtimeConfig,
  setValue,
  setAudioStatus,
  setActiveUtilityPanel,
  scheduleLiveVoiceAutoSend,
  clearLiveVoiceAutoSendTimer,
}: BrowserVoiceInputOptions) {
  const [voiceCaptureMode, setVoiceCaptureMode] = useState<VoiceCaptureMode>('idle');
  const [liveTranscript, setLiveTranscript] = useState('');
  const [liveInterimTranscript, setLiveInterimTranscript] = useState('');
  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);

  async function startVoiceInput(): Promise<void> {
    setActiveUtilityPanel('voice');
    setLiveTranscript('');
    setLiveInterimTranscript('');
    if (runtimeConfig.sttServiceUrl) {
      await startSttRecordingFallback();
      return;
    }
    const Recognition = getSpeechRecognitionConstructor();
    if (Recognition) {
      try {
        const recognition = new Recognition();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = DEFAULT_SPEECH_LANGUAGE;
        recognition.onresult = (event) => {
          let finalText = '';
          let interimText = '';
          for (let index = event.resultIndex; index < event.results.length; index += 1) {
            const result = event.results[index];
            const transcript = result?.[0]?.transcript?.trim() ?? '';
            if (!transcript) continue;
            if (result.isFinal) finalText = mergeTranscript(finalText, transcript);
            else interimText = mergeTranscript(interimText, transcript);
          }
          if (finalText) {
            setLiveTranscript((current) => {
              const next = mergeTranscript(current, finalText);
              setValue('content', next, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
              scheduleLiveVoiceAutoSend(next);
              return next;
            });
          }
          setLiveInterimTranscript(interimText);
        };
        recognition.onerror = (event) => {
          setVoiceCaptureMode('error');
          setAudioStatus(`Speech recognition failed${event.error ? `: ${event.error}` : ''}.`);
        };
        recognition.onend = () => {
          if (speechRecognitionRef.current === recognition) {
            setVoiceCaptureMode((current) => current === 'listening' ? 'idle' : current);
          }
        };
        speechRecognitionRef.current = recognition;
        recognition.start();
        setVoiceCaptureMode('listening');
        setAudioStatus('Listening. Speak and your words will appear in the message composer.');
      } catch (error) {
        setVoiceCaptureMode('error');
        setAudioStatus(error instanceof Error ? error.message : 'Speech recognition could not start.');
      }
      return;
    }
    await startSttRecordingFallback();
  }

  async function startSttRecordingFallback(): Promise<void> {
    if (!runtimeConfig.sttServiceUrl) {
      setVoiceCaptureMode('error');
      setAudioStatus('Browser speech recognition is unavailable and VITE_ASSISTANT_STT_URL is not configured.');
      return;
    }
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceCaptureMode('error');
      setAudioStatus('Browser audio recording is not available.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordingChunksRef.current = [];
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordingChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const mimeType = recordingChunksRef.current[0]?.type || 'audio/webm';
        const audio = new Blob(recordingChunksRef.current, { type: mimeType });
        recordingChunksRef.current = [];
        stopMediaStream();
        void transcribeRecordedAudio(audio, mimeType);
      };
      recorder.start();
      setVoiceCaptureMode('recording');
      setAudioStatus('Recording voice input. End the call to transcribe it.');
    } catch (error) {
      setVoiceCaptureMode('error');
      setAudioStatus(error instanceof Error ? error.message : 'Could not start voice recording.');
      stopMediaStream();
    }
  }

  async function transcribeRecordedAudio(audio: Blob, mimeType: string): Promise<void> {
    if (!runtimeConfig.sttServiceUrl) return;
    try {
      setVoiceCaptureMode('transcribing');
      setAudioStatus('Transcribing recorded voice input…');
      const sttClient = createSttServiceClient({ baseUrl: runtimeConfig.sttServiceUrl, transport: createFetchSpeechServiceTransport() });
      const response = await sttClient.transcribeAudio({ audio, filename: 'chatbot-live-voice.webm', mimeType });
      const text = response.text.trim();
      if (!text) {
        setAudioStatus('No speech was detected in the recording.');
        setVoiceCaptureMode('idle');
        return;
      }
      setLiveTranscript((current) => {
        const next = mergeTranscript(current, text);
        setValue('content', next, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
        scheduleLiveVoiceAutoSend(next);
        return next;
      });
      setLiveInterimTranscript('');
      setVoiceCaptureMode('idle');
      setAudioStatus('Voice input transcribed into the message composer.');
    } catch (error) {
      setVoiceCaptureMode('error');
      setAudioStatus(error instanceof Error ? error.message : 'Voice transcription failed.');
    }
  }

  function stopVoiceInput(): void {
    clearLiveVoiceAutoSendTimer();
    const recognition = speechRecognitionRef.current;
    speechRecognitionRef.current = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try { recognition.stop(); } catch { recognition.abort(); }
    }
    const recorder = mediaRecorderRef.current;
    mediaRecorderRef.current = null;
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop();
    } else {
      stopMediaStream();
    }
    setLiveInterimTranscript('');
    setVoiceCaptureMode((current) => current === 'transcribing' ? current : 'idle');
  }

  function stopMediaStream(): void {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  }

  return {
    voiceCaptureMode, setVoiceCaptureMode,
    liveTranscript, setLiveTranscript,
    liveInterimTranscript, setLiveInterimTranscript,
    startVoiceInput,
    stopVoiceInput,
  };
}
