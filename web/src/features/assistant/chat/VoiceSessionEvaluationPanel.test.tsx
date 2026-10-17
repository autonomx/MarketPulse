import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const activePolicy = {
  preset: 'natural' as const,
  version: 1,
  values: {
    silence_tolerance_ms: 15_000,
    initiative_threshold_ms: 18_000,
    initiative_cooldown_ms: 45_000,
    listener_backchannel_frequency: 0.16,
    typical_turn_words: 70,
    interruption_sensitivity: 0.7,
    response_onset_ms: 420,
  },
  reason: 'initial_server_policy',
  evidence_evaluation_ids: [],
  active: true,
  created_at: '2026-07-11T00:00:00+00:00',
};

const evaluations = Array.from({ length: 5 }, (_, index) => ({
  evaluation_id: `evaluation-${index}`,
  call_id: `call-${index}`,
  session_id: 'chat:one',
  started_at: '2026-07-11T12:00:00+00:00',
  ended_at: `2026-07-11T12:1${index}:00+00:00`,
  exact_commit_sha: 'a'.repeat(40),
  app_version: '1.0.0',
  browser_version: 'Chrome 150',
  os_version: 'Windows 11',
  character_id: 'maya',
  profile_version: 4,
  presence_preset: 'natural' as const,
  conversation_stance: 'discuss',
  configured_duplex_mode: 'automatic' as const,
  resolved_duplex_mode: 'echo_aware' as const,
  calibration_version: 'live-voice-calibration-v1',
  input_device_hash: 'input',
  output_device_hash: 'output',
  environment_hash: 'environment',
  latency_summary: { first_audio_p95_ms: 700 },
  quality_metrics: {
    interruption_success_rate: 1,
    silence_fill_regret_rate: 0.2,
    backchannel_collision_rate: 0.08,
    perceived_pressure_score: 3,
    perceived_listening_score: 3,
  },
  eos_termination_counts: { natural_eos: 8 },
  scenario_labels: ['speakers-quiet'],
  release_gate_status: 'insufficient' as const,
  listening_score: 3,
  pressure_score: 3,
  created_at: '2026-07-11T12:20:00+00:00',
  updated_at: '2026-07-11T12:20:00+00:00',
}));

const releaseGate = vi.fn();
const list = vi.fn();
const activePolicies = vi.fn();
const policyVersions = vi.fn();
const createPolicyVersion = vi.fn();
const activatePolicy = vi.fn();
const rollbackPolicy = vi.fn();

vi.mock('../workspace/live-chat-evaluation-client', async () => {
  const actual = await vi.importActual<typeof import('../workspace/live-chat-evaluation-client')>('../workspace/live-chat-evaluation-client');
  return {
    ...actual,
    liveChatEvaluationClient: {
      releaseGate: (...args: unknown[]) => releaseGate(...args),
      list: (...args: unknown[]) => list(...args),
      activePolicies: (...args: unknown[]) => activePolicies(...args),
      policyVersions: (...args: unknown[]) => policyVersions(...args),
      createPolicyVersion: (...args: unknown[]) => createPolicyVersion(...args),
      activatePolicy: (...args: unknown[]) => activatePolicy(...args),
      rollbackPolicy: (...args: unknown[]) => rollbackPolicy(...args),
      export: vi.fn(async () => ({})),
    },
  };
});

import { VoiceSessionEvaluationPanel } from './VoiceSessionEvaluationPanel';

describe('VoiceSessionEvaluationPanel', () => {
  beforeEach(() => {
    releaseGate.mockReset().mockResolvedValue({
      status: 'insufficient',
      generated_at: '2026-07-11T12:20:00+00:00',
      records_scanned: 5,
      traces: 5,
      scenarios: ['speakers-quiet'],
      missing_scenarios: ['headphones-quiet', 'sustained-20-minute-conversation'],
      character_modes: ['character'],
      metrics: [],
      failures: [],
      insufficient: ['system and character evidence must be aggregated'],
    });
    list.mockReset().mockResolvedValue(evaluations);
    activePolicies.mockReset().mockResolvedValue({
      quiet: { ...activePolicy, preset: 'quiet' },
      natural: activePolicy,
      engaged: { ...activePolicy, preset: 'engaged' },
      listener: { ...activePolicy, preset: 'listener' },
    });
    policyVersions.mockReset().mockResolvedValue([activePolicy]);
    createPolicyVersion.mockReset().mockResolvedValue({ ...activePolicy, version: 2, active: false });
    activatePolicy.mockReset().mockResolvedValue({ ...activePolicy, version: 2, active: true });
    rollbackPolicy.mockReset().mockResolvedValue(activePolicy);
  });

  it('shows durable history and the aggregate release posture', async () => {
    render(<VoiceSessionEvaluationPanel />);

    expect(await screen.findByText('5 durable Voice Session evaluations loaded.')).toBeInTheDocument();
    expect(screen.getAllByText('700 ms').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('100%').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Insufficient').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/1 scenarios observed · 5 durable traces/)).toBeInTheDocument();
    expect(screen.getByText(/Headphones Quiet/)).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Durable Voice Session evaluations' }).children).toHaveLength(5);
    expect(releaseGate).toHaveBeenCalledWith({ persistStatus: true });
  });

  it('creates an inactive evidence-backed candidate before activation', async () => {
    render(<VoiceSessionEvaluationPanel />);
    await screen.findByText('5 durable Voice Session evaluations loaded.');

    fireEvent.click(screen.getByRole('button', { name: 'Create tuning candidate' }));
    await waitFor(() => expect(createPolicyVersion).toHaveBeenCalledTimes(1));
    const [preset, payload] = createPolicyVersion.mock.calls[0];
    expect(preset).toBe('natural');
    expect(payload.evidence_evaluation_ids).toHaveLength(5);
    expect(payload.reason).toBe('evidence_driven_voice_session_tuning');
    expect(await screen.findByRole('button', { name: 'Activate v2' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Activate v2' }));
    await waitFor(() => expect(activatePolicy).toHaveBeenCalledWith('natural', 2));
  });
});
