import { desktopCompanionControlStore } from './desktop-companion-control-store';
import { type DesktopCompanionEvaluationEvent } from './desktop-companion-watch-controller';
import { unwrap } from '../../../api/http';
import { DESKTOP_COMPANION_EVALUATION_EVENT, DESKTOP_COMPANION_STATUS_EVENT, emitOmnixEvent } from '../../../events/bus';
import { POLL_INTERVALS_MS, startPolling } from '../../../shared/timers';
import { api } from '../api/gateway';

let desktopCompanionOperationalGuardInstalled = false;

export type DesktopCompanionFailureAction = 'none' | 'backoff' | 'stop';

export class DesktopCompanionFailureCircuit {
  private consecutiveFailures = 0;
  private backoffUntilMs = 0;

  constructor(
    private readonly backoffThreshold = 3,
    private readonly stopThreshold = 6,
    private readonly backoffMs = 60_000,
  ) {}

  record(input: { providerError: boolean; observed: boolean; nowMs: number }): DesktopCompanionFailureAction {
    if (input.observed && !input.providerError) {
      this.consecutiveFailures = 0;
      this.backoffUntilMs = 0;
      return 'none';
    }
    if (!input.providerError) return 'none';
    this.consecutiveFailures += 1;
    if (this.consecutiveFailures >= this.stopThreshold) return 'stop';
    if (this.consecutiveFailures >= this.backoffThreshold) {
      this.backoffUntilMs = Math.max(this.backoffUntilMs, input.nowMs + this.backoffMs);
      return 'backoff';
    }
    return 'none';
  }

  canResume(nowMs: number): boolean {
    return this.backoffUntilMs > 0 && nowMs >= this.backoffUntilMs && this.consecutiveFailures < this.stopThreshold;
  }

  reset(): void {
    this.consecutiveFailures = 0;
    this.backoffUntilMs = 0;
  }

  snapshot(): { consecutiveFailures: number; backoffUntilMs: number } {
    return { consecutiveFailures: this.consecutiveFailures, backoffUntilMs: this.backoffUntilMs };
  }
}

type OperationalStatus = {
  available: boolean;
  kill_switch: boolean;
  reason: string;
  max_consecutive_provider_failures: number;
  circuit_backoff_seconds: number;
};

let circuit = new DesktopCompanionFailureCircuit();
let resumeTimer: ReturnType<typeof setTimeout> | null = null;
let stopOperationalPolling: (() => void) | null = null;

export function initializeDesktopCompanionOperationalGuard(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (desktopCompanionOperationalGuardInstalled) return () => undefined;
  desktopCompanionOperationalGuardInstalled = true;
  const handleEvaluation = (event: Event) => {
    const detail = (event as CustomEvent<DesktopCompanionEvaluationEvent>).detail;
    if (detail?.kind !== 'vision_result') return;
    const action = circuit.record({
      providerError: detail.providerError === true,
      observed: detail.observed === true,
      nowMs: Date.now(),
    });
    if (action === 'backoff') beginBackoff('provider_failure_backoff');
    if (action === 'stop') stopWatch('provider_failure_circuit_open');
  };
  window.addEventListener(DESKTOP_COMPANION_EVALUATION_EVENT, handleEvaluation);
  stopOperationalPolling = startPolling(checkOperationalStatus, POLL_INTERVALS_MS.desktopCompanionStatus);
  void checkOperationalStatus();
  return () => {
    window.removeEventListener(DESKTOP_COMPANION_EVALUATION_EVENT, handleEvaluation);
    if (resumeTimer !== null) clearTimeout(resumeTimer);
    stopOperationalPolling?.();
    resumeTimer = null;
    stopOperationalPolling = null;
    circuit.reset();
    desktopCompanionOperationalGuardInstalled = false;
  };
}

export function normalizeOperationalStatus(value: unknown): OperationalStatus | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  if (typeof input.available !== 'boolean' || typeof input.kill_switch !== 'boolean') return null;
  return {
    available: input.available,
    kill_switch: input.kill_switch,
    reason: typeof input.reason === 'string' ? input.reason.slice(0, 120) : 'unknown',
    max_consecutive_provider_failures: boundedInt(input.max_consecutive_provider_failures, 6, 1, 20),
    circuit_backoff_seconds: boundedInt(input.circuit_backoff_seconds, 60, 5, 600),
  };
}

async function checkOperationalStatus(): Promise<void> {
  try {
    const status = normalizeOperationalStatus(await unwrap(api.GET('/api/desktop-companion/operational-status')));
    if (!status) return;
    circuit = new DesktopCompanionFailureCircuit(
      Math.min(3, status.max_consecutive_provider_failures),
      status.max_consecutive_provider_failures,
      status.circuit_backoff_seconds * 1000,
    );
    if (!status.available || status.kill_switch) stopWatch(status.reason || 'deployment_kill_switch');
  } catch {
    // Failure to read operational metadata does not disable manual Desktop Ask.
  }
}

function beginBackoff(reason: string): void {
  const state = desktopCompanionControlStore.getState();
  if (!state.requested) return;
  desktopCompanionControlStore.dispatch('pause');
  publish(reason);
  if (resumeTimer !== null) clearTimeout(resumeTimer);
  const delay = Math.max(0, circuit.snapshot().backoffUntilMs - Date.now());
  resumeTimer = setTimeout(() => {
    resumeTimer = null;
    if (circuit.canResume(Date.now()) && desktopCompanionControlStore.getState().requested) {
      desktopCompanionControlStore.dispatch('resume');
      publish('provider_failure_backoff_complete');
    }
  }, delay);
}

function stopWatch(reason: string): void {
  if (resumeTimer !== null) clearTimeout(resumeTimer);
  resumeTimer = null;
  desktopCompanionControlStore.dispatch('stop');
  publish(reason);
}

function publish(reason: string): void {
  emitOmnixEvent(DESKTOP_COMPANION_STATUS_EVENT, { phase: reason.includes('backoff') ? 'backing_off' : 'off', reason });
}

function boundedInt(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.max(minimum, Math.min(maximum, Math.round(numeric)));
}
