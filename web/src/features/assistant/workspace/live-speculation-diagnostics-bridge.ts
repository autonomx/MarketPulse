/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { createLiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { ASSISTANT_VOICE_PERF_EVENT } from '../../../events/bus';

let liveSpeculationDiagnosticsInstalled = false;

const HOT_PATH_STAGE_PREFIXES = [
  'llm_speculation_',
  'tts_speculative_',
  'live_chat_direct_gateway_',
] as const;

export function isSpeculationDiagnosticStage(stage: string): boolean {
  return HOT_PATH_STAGE_PREFIXES.some((prefix) => stage.startsWith(prefix));
}

export function initializeLiveSpeculationDiagnosticsBridge(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveSpeculationDiagnosticsInstalled) return () => undefined;
  liveSpeculationDiagnosticsInstalled = true;
  const reporter = createLiveCallDiagnosticsReporter('live-call:speculation');

  const handlePerformance = (event: Event): void => {
    const detail = (event as CustomEvent<Record<string, unknown>>).detail ?? {};
    const stage = typeof detail.stage === 'string' ? detail.stage : '';
    if (!isSpeculationDiagnosticStage(stage)) return;
    const { stage: _stage, timestamp: _timestamp, ...safeDetails } = detail;
    reporter.record(stage, safeDetails, 'speculation');
  };

  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
  return () => {
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    liveSpeculationDiagnosticsInstalled = false;
    void reporter.close('speculation_diagnostics_stopped');
  };
}
