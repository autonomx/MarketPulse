import { describe, expect, it } from 'vitest';

import {
  DesktopCompanionFailureCircuit,
  normalizeOperationalStatus,
} from './desktop-companion-operational-guard';

describe('desktop companion provider failure circuit', () => {
  it('backs off after three failures and stops after six', () => {
    const circuit = new DesktopCompanionFailureCircuit(3, 6, 60_000);

    expect(circuit.record({ providerError: true, observed: false, nowMs: 1_000 })).toBe('none');
    expect(circuit.record({ providerError: true, observed: false, nowMs: 2_000 })).toBe('none');
    expect(circuit.record({ providerError: true, observed: false, nowMs: 3_000 })).toBe('backoff');
    expect(circuit.canResume(62_999)).toBe(false);
    expect(circuit.canResume(63_000)).toBe(true);
    expect(circuit.record({ providerError: true, observed: false, nowMs: 64_000 })).toBe('backoff');
    expect(circuit.record({ providerError: true, observed: false, nowMs: 65_000 })).toBe('backoff');
    expect(circuit.record({ providerError: true, observed: false, nowMs: 66_000 })).toBe('stop');
  });

  it('resets after a successful observation', () => {
    const circuit = new DesktopCompanionFailureCircuit();
    circuit.record({ providerError: true, observed: false, nowMs: 1_000 });
    circuit.record({ providerError: true, observed: false, nowMs: 2_000 });
    circuit.record({ providerError: false, observed: true, nowMs: 3_000 });

    expect(circuit.snapshot()).toEqual({ consecutiveFailures: 0, backoffUntilMs: 0 });
  });
});

describe('desktop companion operational status', () => {
  it('normalizes only bounded non-content operational fields', () => {
    expect(normalizeOperationalStatus({
      available: false,
      kill_switch: true,
      reason: 'deployment_kill_switch',
      max_consecutive_provider_failures: 6,
      circuit_backoff_seconds: 60,
      privateScreenText: 'secret',
    })).toEqual({
      available: false,
      kill_switch: true,
      reason: 'deployment_kill_switch',
      max_consecutive_provider_failures: 6,
      circuit_backoff_seconds: 60,
    });
  });
});
