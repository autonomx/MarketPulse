import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react';
import { omnixApiClient } from '../../../api/client';
import { createFetchSpeechServiceTransport, createTtsServiceClient, type AssistantWorkspaceRuntimeConfig } from '../workspace';
import { stopAssistantPcmStream } from '../workspace';
import type { CharacterLiveCallRuntime, LiveCallSpeechStyle } from './characterClient';
import { STREAMING_TTS_RECOVERY_DELAY_SECONDS, STREAMING_TTS_SAMPLE_RATE, audioSourceToArrayBuffer, canUseDecodedAudioPlayback, getSynthesizedAudioSource, getVoiceJobAudioSource, makePlayableAudioSource, voiceJobErrorMessage, voiceProfileId, waitForAudioElementPlaying, waitForAudioElementToFinish, waitForStreamingPlaybackToFinish, type AssistantSettings, type StreamingTtsPlayback, type StreamingTtsWindow, type VoiceProfileAsset } from './chatbotWorkspaceModel';
import type { VoiceTurnDiagnostics } from './useVoiceTurnDiagnostics';
import { ASSISTANT_VOICE_INTERRUPT_EVENT } from '../../../events/bus';

type ResponseAudioOptions = Omit<VoiceTurnDiagnostics, 'voiceTurnPerformanceRef'> & {
  runtimeConfig: AssistantWorkspaceRuntimeConfig;
  assistantSettings: AssistantSettings;
  liveCallRuntimeRef: RefObject<CharacterLiveCallRuntime | null>;
  voiceProfiles: VoiceProfileAsset[];
  activeVoiceId: string;
  activeVoiceLabel: string;
  activeSessionId: string | undefined;
  selectedProviderId: string;
  selectedModelId: string;
  selectedPersonalityLabel: string;
  setAudioStatus: Dispatch<SetStateAction<string | null>>;
};

/** Spoken replies: synthesizes a reply in the call's voice and plays it, one at a time. */
export function useResponseAudio({
  runtimeConfig,
  assistantSettings,
  liveCallRuntimeRef,
  voiceProfiles,
  activeVoiceId,
  activeVoiceLabel,
  activeSessionId,
  selectedProviderId,
  selectedModelId,
  selectedPersonalityLabel,
  setAudioStatus,
  markVoiceTurnPerformance,
  recordVoiceTurnDiagnostic,
  logVoiceTurnPerformance,
}: ResponseAudioOptions) {
  const queryClient = useQueryClient();
  const [isAssistantSpeaking, setIsAssistantSpeaking] = useState(false);
  const assistantAudioRef = useRef<HTMLAudioElement | null>(null);
  const streamingTtsRef = useRef<StreamingTtsPlayback | null>(null);
  const assistantPlaybackTokenRef = useRef(0);

  useEffect(() => {
    const handleInterrupt = () => stopAssistantResponseAudio('Interrupted. Listening for your next message.');
    window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterrupt);
    return () => window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterrupt);
    // Stopping reads only refs and state setters, so the first render's function stays correct.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function currentLiveCallVoiceId(): string {
    const runtimeVoiceAssetId = liveCallRuntimeRef.current?.voice_asset_id;
    if (runtimeVoiceAssetId) {
      const asset = voiceProfiles.find((candidate) => candidate.id === runtimeVoiceAssetId);
      return asset ? voiceProfileId(asset) : runtimeVoiceAssetId.replace(/^voice-cloning:/, '');
    }
    return assistantSettings.voiceId || runtimeConfig.ttsVoice || '';
  }

  function currentLiveCallSpeechStyle(): LiveCallSpeechStyle {
    return liveCallRuntimeRef.current?.speech_style ?? {
      speed: 1,
      temperature: 0.6,
      top_k: 20,
      top_p: 0.85,
      repetition_penalty: 1,
      expressiveness: 'neutral',
      emotion: 'neutral',
      interruption_style: 'balanced',
    };
  }

  function currentLiveCallDisplayName(): string {
    return liveCallRuntimeRef.current?.display_name || 'Omnix Assistant';
  }

  async function playAssistantResponseAudio(text: string): Promise<void> {
    stopAssistantPcmStream();
    const spokenText = text.trim();
    if (!spokenText) {
      setAudioStatus('No assistant response is ready to play.');
      return;
    }
    const playbackToken = assistantPlaybackTokenRef.current + 1;
    assistantPlaybackTokenRef.current = playbackToken;
    let revokePlayableAudioSource: (() => void) | undefined;
    try {
      markVoiceTurnPerformance('ttsStartedAt');
      recordVoiceTurnDiagnostic('tts_request_started', {
        text_chars: spokenText.length,
      });
      setAudioStatus(activeVoiceId ? `Synthesizing ${activeVoiceLabel || activeVoiceId} voice…` : 'Synthesizing response voice…');
      if (assistantPlaybackTokenRef.current !== playbackToken) return;
      const audioSource = runtimeConfig.ttsServiceUrl
        ? await synthesizeWithTtsService(spokenText)
        : await synthesizeWithVoiceJob(spokenText);
      if (assistantPlaybackTokenRef.current !== playbackToken) return;
      markVoiceTurnPerformance('ttsReadyAt');
      recordVoiceTurnDiagnostic('tts_output_ready', { playback_mode: 'batch' });
      if (canUseDecodedAudioPlayback()) {
        await playDecodedAssistantResponseAudio(audioSource, playbackToken);
        return;
      }
      stopAssistantResponseAudio(undefined, { cancelPending: false });
      const playableAudio = makePlayableAudioSource(audioSource);
      revokePlayableAudioSource = playableAudio.revoke;
      console.info('[Omnix Voice Perf] batch TTS audio source ready', {
        sourceKind: playableAudio.revoke ? 'blob-url' : audioSource.startsWith('data:') ? 'data-url' : 'url',
        sourceLength: audioSource.length,
      });
      const audio = new Audio(playableAudio.url);
      assistantAudioRef.current = audio;
      const clearSpeakingState = () => {
        if (assistantAudioRef.current === audio) assistantAudioRef.current = null;
        revokePlayableAudioSource?.();
        revokePlayableAudioSource = undefined;
        setIsAssistantSpeaking(false);
      };
      if (typeof audio.addEventListener === 'function') {
        audio.addEventListener('ended', clearSpeakingState, { once: true });
        audio.addEventListener('pause', clearSpeakingState, { once: true });
        audio.addEventListener('error', () => {
          console.info('[Omnix Voice Perf] batch TTS audio element error', {
            code: audio.error?.code,
            message: audio.error?.message,
            networkState: audio.networkState,
            readyState: audio.readyState,
          });
          clearSpeakingState();
        }, { once: true });
      }
      setIsAssistantSpeaking(true);
      audio.preload = 'auto';
      audio.playbackRate = currentLiveCallSpeechStyle().speed;
      const playing = waitForAudioElementPlaying(audio);
      await audio.play();
      await playing;
      if (assistantPlaybackTokenRef.current !== playbackToken) return;
      markVoiceTurnPerformance('audioPlayStartedAt');
      recordVoiceTurnDiagnostic('audio_playback_started', { playback_mode: 'audio_element' });
      console.info('[Omnix Voice Perf] batch TTS audio playing', {
        duration: Number.isFinite(audio.duration) ? Math.round(audio.duration * 1000) : null,
        readyState: audio.readyState,
        networkState: audio.networkState,
      });
      logVoiceTurnPerformance();
      setAudioStatus(activeVoiceId ? 'Playing cloned response voice.' : 'Playing response voice.');
      await waitForAudioElementToFinish(audio);
    } catch (error) {
      assistantAudioRef.current = null;
      revokePlayableAudioSource?.();
      setIsAssistantSpeaking(false);
      setAudioStatus(error instanceof Error ? error.message : 'Response audio playback failed.');
    }
  }

  function stopAssistantResponseAudio(status?: string, options: { cancelPending?: boolean } = {}): void {
    if (options.cancelPending !== false) assistantPlaybackTokenRef.current += 1;
    const audio = assistantAudioRef.current;
    assistantAudioRef.current = null;
    stopStreamingTtsPlayback();
    if (audio) {
      try { audio.pause(); } catch { /* ignore playback cleanup failures */ }
      try { audio.currentTime = 0; } catch { /* ignore playback cleanup failures */ }
    }
    setIsAssistantSpeaking(false);
    if (status) setAudioStatus(status);
  }

  async function playDecodedAssistantResponseAudio(audioSource: string, playbackToken: number): Promise<void> {
    const liveWindow = window as StreamingTtsWindow;
    const AudioContextCtor = liveWindow.AudioContext ?? liveWindow.webkitAudioContext;
    if (!AudioContextCtor) throw new Error('Decoded TTS playback requires AudioContext support.');

    stopAssistantResponseAudio(undefined, { cancelPending: false });
    const audioContext = new AudioContextCtor({ latencyHint: 'interactive', sampleRate: STREAMING_TTS_SAMPLE_RATE });
    const playback: StreamingTtsPlayback = { audioContext, abortController: new AbortController(), sources: [], closed: false };
    streamingTtsRef.current = playback;

    try {
      const bytes = await audioSourceToArrayBuffer(audioSource);
      const audioBuffer = await audioContext.decodeAudioData(bytes.slice(0));
      if (audioContext.state !== 'running') await audioContext.resume();
      if (assistantPlaybackTokenRef.current !== playbackToken) return;

      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.playbackRate.value = currentLiveCallSpeechStyle().speed;
      source.connect(audioContext.destination);
      const startAt = audioContext.currentTime + STREAMING_TTS_RECOVERY_DELAY_SECONDS;
      source.start(startAt);
      playback.sources.push(source);
      setIsAssistantSpeaking(true);
      setAudioStatus(activeVoiceId ? 'Playing cloned response voice.' : 'Playing response voice.');
      console.info('[Omnix Voice Perf] decoded TTS audio scheduled', {
        bytes: bytes.byteLength,
        durationMs: Math.round(audioBuffer.duration * 1000),
        sampleRate: audioBuffer.sampleRate,
        scheduledLeadMs: Math.round((startAt - audioContext.currentTime) * 1000),
      });

      source.addEventListener('ended', () => {
        playback.sources = playback.sources.filter((entry) => entry !== source);
        if (streamingTtsRef.current === playback) {
          streamingTtsRef.current = null;
          setIsAssistantSpeaking(false);
          void audioContext.close().catch(() => undefined);
        }
      }, { once: true });

      window.setTimeout(() => {
        if (assistantPlaybackTokenRef.current !== playbackToken || playback.closed) return;
        markVoiceTurnPerformance('audioPlayStartedAt');
        recordVoiceTurnDiagnostic('audio_playback_started', { playback_mode: 'decoded_audio_context' });
        console.info('[Omnix Voice Perf] decoded TTS audio start', {
          audioContextTime: Number(audioContext.currentTime.toFixed(3)),
          activeSources: playback.sources.length,
        });
        logVoiceTurnPerformance();
      }, Math.max(0, (startAt - audioContext.currentTime) * 1000));

      await waitForStreamingPlaybackToFinish(playback, () => assistantPlaybackTokenRef.current !== playbackToken);
    } catch (error) {
      stopStreamingTtsPlayback();
      throw error;
    }
  }

  function stopStreamingTtsPlayback(): void {
    const playback = streamingTtsRef.current;
    streamingTtsRef.current = null;
    if (!playback) return;
    playback.closed = true;
    try { playback.abortController.abort(); } catch { /* ignore stream cleanup failures */ }
    playback.sources.forEach((source) => {
      try { source.stop(); } catch { /* ignore stream cleanup failures */ }
      try { source.disconnect(); } catch { /* ignore stream cleanup failures */ }
    });
    void playback.audioContext.close().catch(() => undefined);
  }

  async function synthesizeWithTtsService(text: string): Promise<string> {
    if (!runtimeConfig.ttsServiceUrl) throw new Error('TTS service URL is not configured.');
    const ttsClient = createTtsServiceClient({ baseUrl: runtimeConfig.ttsServiceUrl, transport: createFetchSpeechServiceTransport() });
    const response = await ttsClient.synthesizeSpeech({
      text,
      voice: currentLiveCallVoiceId() || undefined,
      format: 'wav',
      metadata: { source: 'chatbot_response_playback', sessionId: activeSessionId, providerId: selectedProviderId || runtimeConfig.defaultProviderId, modelId: selectedModelId || runtimeConfig.defaultModelId, speechStyle: currentLiveCallSpeechStyle(), characterId: liveCallRuntimeRef.current?.character_id, characterProfileVersion: liveCallRuntimeRef.current?.character_profile_version },
    });
    return getSynthesizedAudioSource(response);
  }

  async function synthesizeWithVoiceJob(text: string): Promise<string> {
    setAudioStatus('Queueing local Voice Studio TTS job…');
    const job = await omnixApiClient.createJob(voiceStudioSpeechJob(text, {
      speaker: currentLiveCallDisplayName(),
      voiceId: currentLiveCallVoiceId() || null,
      style: liveCallRuntimeRef.current?.speech_style.expressiveness || selectedPersonalityLabel,
    }), { timeoutMs: 120_000, timeoutMessage: 'Voice synthesis timed out after 120s.' });
    await queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
    const source = getVoiceJobAudioSource(job);
    if (!source) throw new Error(voiceJobErrorMessage(job) || 'Voice Studio did not return playable speech audio.');
    return source;
  }

  return {
    isAssistantSpeaking,
    playAssistantResponseAudio,
    stopAssistantResponseAudio,
    currentLiveCallVoiceId,
    currentLiveCallSpeechStyle,
  };
}

/** The Voice Studio job that speaks one reply in the call's voice (used without a TTS service). */
function voiceStudioSpeechJob(
  text: string,
  { speaker, voiceId, style }: { speaker: string; voiceId: string | null; style: string },
): Parameters<typeof omnixApiClient.createJob>[0] {
  return {
    module: 'voice',
    type: 'tts.synthesize',
    resource_class: 'gpu:tts',
    priority: 1,
    input_payload: {
      text,
      provider_id: null,
      speaker: speaker,
      voice_id: voiceId,
      script_mode: 'single_speaker',
      script_speakers: [{ name: speaker, count: 1 }],
      script_segments: [{ index: 0, speaker: speaker, text }],
      character_voice_assignments: [{ speaker: speaker, voice_id: voiceId, style: style, line_count: 1 }],
      save_output: true,
    },
    stages: [
      { id: 'synthesize-chatbot-response', label: 'Generate chatbot response speech', resource_class: 'gpu:tts', status: 'queued' },
      { id: 'store-chatbot-response-audio', label: 'Save chatbot response audio', resource_class: 'cpu', status: 'queued' },
    ],
  };
}
