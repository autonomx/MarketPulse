import { useEffect, useRef } from 'react';
import { createLiveCallDiagnosticsReporter, type LiveCallDiagnosticsReporter } from '../workspace';
import { elapsedMs, finiteNumber, type VoicePerformanceStage, type VoiceTurnPerformance, type VoiceTurnTimestampStage } from './chatbotWorkspaceModel';
import { ASSISTANT_VOICE_PERF_EVENT } from '../../../events/bus';

/** Timings and diagnostics of one live voice turn, from the final transcript to the first audio. */
export function useVoiceTurnDiagnostics() {
  const voiceTurnPerformanceRef = useRef<VoiceTurnPerformance | null>(null);
  const voiceTurnDiagnosticsRef = useRef<LiveCallDiagnosticsReporter | null>(null);

  useEffect(() => {
    const handlePerfEvent = (event: Event) => {
      const detail = (event as CustomEvent<VoicePerformanceStage>).detail;
      if (!detail || typeof detail.stage !== 'string' || typeof detail.turnId !== 'string') return;
      if (detail.stage === 'semantic_turn_assessed' || detail.stage === 'stt_final_requested') {
        if (voiceTurnDiagnosticsRef.current?.traceId !== `live-call:${detail.turnId}`) {
          void voiceTurnDiagnosticsRef.current?.close('turn_superseded');
          voiceTurnDiagnosticsRef.current = createLiveCallDiagnosticsReporter(`live-call:${detail.turnId}`);
        }
        recordVoiceTurnDiagnostic(detail.stage, {
          delay_ms: finiteNumber(detail.delayMs),
          pace: typeof detail.pace === 'string' ? detail.pace : undefined,
          probability_done: finiteNumber(detail.probabilityDone),
          reason: typeof detail.reason === 'string' ? detail.reason : undefined,
        });
      }
      if (detail.stage !== 'stt_final_received') return;
      if (!voiceTurnDiagnosticsRef.current) {
        voiceTurnDiagnosticsRef.current = createLiveCallDiagnosticsReporter(`live-call:${detail.turnId}`);
      }
      voiceTurnPerformanceRef.current = {
        turnId: detail.turnId,
        sttFinalReceivedAt: performance.now(),
        transcriptChars: typeof detail.transcriptChars === 'number' ? detail.transcriptChars : undefined,
        sttFinalizeMs: typeof detail.sttFinalizeMs === 'number' ? detail.sttFinalizeMs : undefined,
      };
      console.info('[Omnix Voice Perf] voice turn accepted', {
        turnId: detail.turnId,
        transcriptChars: voiceTurnPerformanceRef.current.transcriptChars,
        sttFinalizeMs: voiceTurnPerformanceRef.current.sttFinalizeMs,
      });
      recordVoiceTurnDiagnostic('stt_final_received', {
        stt_finalize_ms: voiceTurnPerformanceRef.current.sttFinalizeMs,
        input_chars: voiceTurnPerformanceRef.current.transcriptChars,
      });
    };
    window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerfEvent);
    return () => window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerfEvent);
  }, []);

  useEffect(() => () => {
    void voiceTurnDiagnosticsRef.current?.close('workspace_unmounted');
    voiceTurnDiagnosticsRef.current = null;
  }, []);

  function markVoiceTurnPerformance(stage: VoiceTurnTimestampStage): void {
    const current = voiceTurnPerformanceRef.current;
    if (!current) return;
    if (current[stage] === undefined) current[stage] = performance.now();
  }

  function recordVoiceTurnDiagnostic(event: string, details: Record<string, unknown> = {}): void {
    const reporter = voiceTurnDiagnosticsRef.current;
    const performanceState = voiceTurnPerformanceRef.current;
    if (!reporter) return;
    const reporterTurnId = reporter.traceId.startsWith('live-call:voice-turn:')
      ? reporter.traceId.slice('live-call:'.length)
      : performanceState?.turnId;
    const performanceMatchesReporter = Boolean(
      performanceState && reporterTurnId && performanceState.turnId === reporterTurnId,
    );
    reporter.record(event, {
      turn_id: reporterTurnId,
      elapsed_from_stt_final_ms: performanceMatchesReporter && performanceState
        ? Math.round(performance.now() - performanceState.sttFinalReceivedAt)
        : undefined,
      ...details,
    }, 'chatbot_workspace');
  }

  function logVoiceTurnPerformance(): void {
    const current = voiceTurnPerformanceRef.current;
    if (!current?.audioPlayStartedAt || current.turnaroundLogged) return;
    current.turnaroundLogged = true;

    const totalMs = Math.round(current.audioPlayStartedAt - current.sttFinalReceivedAt);
    const rows = [
      { segment: 'STT finalize request -> final transcript', ms: current.sttFinalizeMs ?? null },
      { segment: 'Final transcript -> chat submit', ms: elapsedMs(current.sttFinalReceivedAt, current.chatSubmitStartedAt) },
      { segment: 'Chat submit -> chat response', ms: elapsedMs(current.chatSubmitStartedAt, current.chatResponseReceivedAt) },
      { segment: 'Chat response -> TTS start', ms: elapsedMs(current.chatResponseReceivedAt, current.ttsStartedAt) },
      { segment: 'TTS synth/output ready', ms: elapsedMs(current.ttsStartedAt, current.ttsReadyAt) },
      { segment: 'TTS ready -> first audio scheduled', ms: elapsedMs(current.ttsReadyAt, current.audioFirstScheduledAt) },
      { segment: 'First audio scheduled -> playback started', ms: elapsedMs(current.audioFirstScheduledAt, current.audioPlayStartedAt) },
      { segment: 'Audio ready -> playback started', ms: elapsedMs(current.ttsReadyAt, current.audioPlayStartedAt) },
      { segment: 'Total final transcript -> audio playback', ms: totalMs },
    ];

    console.info('[Omnix Voice Perf] voice audio turnaround', {
      turnId: current.turnId,
      totalMs,
      targetMs: 1000,
      withinTarget: totalMs < 1000,
      transcriptChars: current.transcriptChars,
    });
    console.table(rows);
    recordVoiceTurnDiagnostic('voice_audio_turnaround', {
      total_ms: totalMs,
      target_ms: 1000,
      within_target: totalMs < 1000,
      stt_finalize_ms: current.sttFinalizeMs,
      final_to_chat_submit_ms: elapsedMs(current.sttFinalReceivedAt, current.chatSubmitStartedAt),
      chat_submit_to_response_open_ms: elapsedMs(current.chatSubmitStartedAt, current.chatResponseReceivedAt),
      response_open_to_first_chunk_ms: elapsedMs(current.chatResponseReceivedAt, current.llmFirstChunkReceivedAt),
      first_chunk_to_llm_complete_ms: elapsedMs(current.llmFirstChunkReceivedAt, current.llmCompletedAt),
      tts_start_to_ready_ms: elapsedMs(current.ttsStartedAt, current.ttsReadyAt),
      tts_ready_to_playback_ms: elapsedMs(current.ttsReadyAt, current.audioPlayStartedAt),
    });
  }

  return { voiceTurnPerformanceRef, markVoiceTurnPerformance, recordVoiceTurnDiagnostic, logVoiceTurnPerformance };
}

export type VoiceTurnDiagnostics = ReturnType<typeof useVoiceTurnDiagnostics>;
