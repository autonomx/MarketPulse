import { describe, expect, it } from 'vitest';
import { summarizeServiceStatus, type HermesStatus } from './ServicesSettings';

const status: HermesStatus = {
  enabled: true,
  reachable: true,
  state: 'ready',
  message: 'Hermes is reachable.',
  base_url: 'http://127.0.0.1:8642',
  health: {},
  capabilities: {},
  error: null,
  timeout_seconds: 45,
  api_key_configured: false,
  diagnostics: { status_path: '/api/hermes/status', test_path: '/api/hermes/test', test_dry_run_only: true },
};

describe('Hermes status summary', () => {
  it('reads reachability and state from the status route', () => {
    expect(summarizeServiceStatus(status)).toBe('Ready');
    expect(summarizeServiceStatus({ ...status, reachable: false, state: 'unreachable' })).toBe('unreachable');
    expect(summarizeServiceStatus({ ...status, enabled: false })).toBe('Disabled');
    expect(summarizeServiceStatus({ ...status, error: 'timeout' })).toBe('Error');
    expect(summarizeServiceStatus(undefined)).toBe('Unavailable');
  });
});
