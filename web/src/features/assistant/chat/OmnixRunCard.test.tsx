/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OmnixRunCard } from './OmnixRunCard';
import { createTestQueryClient } from '../../../test/renderWithProviders';
import { assistantApiClient } from '../api/assistantClient';

// Fixtures carry only the fields each test reads.
function fixture<T>(value: unknown): T {
  return value as T;
}

function renderCard(metadata: Record<string, unknown>) {
  const client = createTestQueryClient();
  return render(<QueryClientProvider client={client}><OmnixRunCard metadata={metadata} /></QueryClientProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('OmnixRunCard', () => {
  it('shows the Pi-native coding lifecycle without internal orchestration phases', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-short-quality',
      status: 'running',
      desired_state: 'running',
      revision: 1,
      quality_stage: 'implementing',
      quality_attempt: 1,
      spec: { profile: 'coding', quality_policy: 'strict', task: 'Fix the editor' },
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-short-quality',
      evaluated_at: '2026-09-12T00:00:00Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-short-quality',
        status: 'running',
        profile: 'coding',
        task: 'Fix the editor',
      },
    });

    const pipeline = await screen.findByRole('region', { name: 'Coding run lifecycle' });
    expect(pipeline.textContent).toContain('Starting');
    expect(pipeline.textContent).toContain('Pi working');
    expect(pipeline.textContent).toContain('Acceptance');
    expect(pipeline.textContent).toContain('Completed');
    expect(pipeline.textContent).not.toContain('Inspect');
    expect(pipeline.textContent).not.toContain('Plan');
    expect(pipeline.textContent).not.toContain('Validate');
    expect(pipeline.textContent).not.toContain('Independent review');
  });

  it('offers explicit interruption when a coding run has an unresolved stall advisory', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-stall-warning',
      status: 'running',
      desired_state: 'running',
      revision: 2,
      spec: { profile: 'coding', task: 'Fix the editor' },
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([fixture({
      event_id: 'stall-1',
      run_id: 'run-stall-warning',
      sequence: 9,
      event_type: 'run.stall_suspected',
      payload: {
        reason: 'no durable agent activity for 120s after model.message',
        automatic_recovery: false,
      },
      created_at: '2026-09-12T01:00:00Z',
    })]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-stall-warning',
      evaluated_at: '2026-09-12T01:00:00Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);
    const command = vi.spyOn(assistantApiClient, 'commandAgentRun').mockResolvedValue({
      run_id: 'run-stall-warning',
      status: 'running',
      desired_state: 'running',
      revision: 3,
      spec: { profile: 'coding', task: 'Fix the editor' },
    } as never);

    renderCard({
      agent_run: {
        run_id: 'run-stall-warning',
        status: 'running',
        profile: 'coding',
        task: 'Fix the editor',
        revision: 2,
      },
    });

    const warning = await screen.findByRole('region', { name: 'Possible Pi stall' });
    expect(warning.textContent).toContain('will not restart it automatically');
    fireEvent.click(screen.getByRole('button', { name: 'Interrupt and recover' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith(
      'run-stall-warning',
      'resume',
      expect.objectContaining({ message: expect.stringContaining('user explicitly requested') }),
    ));
  });

  it('keeps a warning on a run that went unsandboxed', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-unsandboxed',
      status: 'running',
      desired_state: 'running',
      revision: 2,
      spec: { profile: 'coding', task: 'Fix the editor' },
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([fixture({
      event_id: 'unsandboxed-1',
      run_id: 'run-unsandboxed',
      sequence: 2,
      event_type: 'run.unsandboxed',
      payload: { reason: 'the agent sandbox needs Docker, which is not running', commands_need_approval: true },
      created_at: '2026-10-03T01:00:00Z',
    })]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);

    renderCard({ agent_run: { run_id: 'run-unsandboxed', status: 'running', profile: 'coding', task: 'Fix the editor', revision: 2 } });

    const warning = await screen.findByRole('region', { name: 'Running without the sandbox' });
    expect(warning.textContent).toContain('Docker, which is not running');
    expect(warning.textContent).toContain('needs your approval');
  });

  it('renders an agent run from durable chat metadata', () => {
    renderCard({ agent_run: { run_id: 'run-1', status: 'paused', profile: 'coding', task: 'Fix tests', revision: 2 } });
    expect(screen.getByText('Agent · coding')).toBeTruthy();
    expect(screen.getByText('paused')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeTruthy();
  });

  it('steers an active coding run without sending a chat command or cancelling it', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-steer',
      status: 'running',
      desired_state: 'running',
      revision: 2,
      last_error: null,
      spec: { profile: 'coding', task: 'Fix the editor' },
    } as never);
    const command = vi.spyOn(assistantApiClient, 'commandAgentRun').mockResolvedValue({
      run_id: 'run-steer',
      status: 'running',
      desired_state: 'running',
      revision: 2,
      last_error: null,
      spec: { profile: 'coding', task: 'Fix the editor' },
    } as never);

    renderCard({ agent_run: { run_id: 'run-steer', status: 'running', profile: 'coding', task: 'Fix the editor', revision: 2 } });

    fireEvent.change(screen.getByRole('textbox', { name: 'Steering guidance' }), {
      target: { value: 'Keep the existing layout and add a focused regression test.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Steer run' }));

    await waitFor(() => expect(command).toHaveBeenCalledWith(
      'run-steer',
      'steer',
      { message: 'Keep the existing layout and add a focused regression test.' },
    ));
    expect(command).not.toHaveBeenCalledWith('run-steer', 'cancel', expect.anything());
  });

  it('labels automatic runtime recovery instead of showing a generic running state', () => {
    renderCard({ agent_run: { run_id: 'run-recovering', status: 'resume_requested', profile: 'coding', task: 'Recover the run', revision: 3 } });
    expect(screen.getByText('recovering')).toBeTruthy();
  });

  it('shows when an Agent is waiting for the user to answer a clarification', async () => {
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([
      fixture({
        event_id: 'event-clarification',
        run_id: 'run-waiting',
        sequence: 4,
        event_type: 'model.message',
        payload: {
          phase: 'message_end',
          requires_user_input: true,
          text: 'Which header control should move?',
        },
        created_at: '2026-09-05T00:00:00Z',
      }),
    ]);
    renderCard({
      agent_run: {
        run_id: 'run-waiting',
        status: 'waiting_for_input',
        profile: 'coding',
        task: 'Fix the chat header',
        revision: 4,
      },
    });
    expect(screen.getByText('waiting for your input')).toBeTruthy();
    expect(screen.getByText('Waiting for your response')).toBeTruthy();
    expect((await screen.findAllByText('Which header control should move?')).length).toBeGreaterThan(0);
    expect(screen.getByText('Reply in the chat composer below to continue this run.')).toBeTruthy();
  });

  it('shows semantic routing and compiler diagnostics', () => {
    renderCard({
      agent_run: {
        run_id: 'run-routing',
        status: 'paused',
        profile: 'coding',
        task: 'Fix Aurora light mode',
        revision: 2,
      },
      semantic_task: {
        intent: 'repair Aurora appearance',
        reason_code: 'workspace_ui_mutation',
        ambiguity: 'none',
      },
      turn_plan: {
        lane: 'agent',
        profile_id: 'coding',
        disposition: 'revise_objective',
        run_action: 'steer_agent',
        authority_delta: ['workspace_read', 'workspace_mutate', 'workspace_execute'],
      },
      semantic_compilation: {
        lane: 'agent',
        profile_id: 'coding',
        action_intents: ['workspace_read', 'workspace_mutate', 'workspace_execute'],
        anomalies: [],
      },
      routing_decision: {
        production_router: 'semantic_v2',
        production_lane: 'agent',
        parser: { provider: 'chatgpt_codex', model: 'gpt-fast', latency_ms: 143, cache_hit: false },
        semantic_v2: { lane: 'agent', reason: 'semantic_v2:workspace_ui_mutation' },
      },
    });

    expect(screen.getByText('Routing & compiler')).toBeTruthy();
    expect(screen.getByText(/agent · coding · revise_objective · steer_agent/)).toBeTruthy();
    expect(screen.getByText(/authority=workspace_read, workspace_mutate, workspace_execute/)).toBeTruthy();
    expect(screen.getByText(/workspace_ui_mutation · none/)).toBeTruthy();
    expect(screen.getByText(/coding · workspace_read, workspace_mutate, workspace_execute/)).toBeTruthy();
    expect(screen.getByText(/gpt-fast · 143ms/)).toBeTruthy();
    expect(screen.getByText(/semantic_v2 · lane=agent/)).toBeTruthy();
  });

  it('renders and updates a durable task graph result', () => {
    renderCard({
      task_graph_run: {
        run_id: 'graph-1',
        status: 'completed',
        revision: 3,
        result: 'Combined final answer.',
        graph: {
          graph_id: 'graph-def-1',
          revision: 1,
          nodes: [
            { id: 'research-1', kind: 'evidence_read', profile_id: 'research' },
            { id: 'synthesize-results', kind: 'synthesis', profile_id: null },
          ],
          output_contract: { result_node: 'synthesize-results' },
        },
        node_states: [
          { node_id: 'research-1', status: 'completed' },
          { node_id: 'synthesize-results', status: 'completed' },
        ],
      },
    });
    expect(screen.getByText('Agent · Task graph')).toBeTruthy();
    expect(screen.getByText('completed')).toBeTruthy();
    expect(screen.getByText('Combined final answer.')).toBeTruthy();
    expect(screen.getByText(/2\/2 nodes complete/)).toBeTruthy();
  });

  it('renders child-agent approval details for a task graph', () => {
    renderCard({
      task_graph_run: {
        run_id: 'graph-approval',
        status: 'waiting_for_approval',
        revision: 2,
        graph: {
          nodes: [{ id: 'email-1', kind: 'agent', profile_id: 'personal-assistant' }],
          output_contract: { result_node: 'email-1' },
        },
        node_states: [{
          node_id: 'email-1',
          status: 'waiting_for_approval',
          child_run_id: 'child-email',
          output: {
            pending_approvals: [{
              approval_id: 'approval-1',
              capability_id: 'gmail.send_email',
              request_payload: { command: 'send email' },
            }],
          },
        }],
      },
    });
    expect(screen.getByText('gmail.send_email')).toBeTruthy();
    expect(screen.getByText('send email')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeTruthy();
  });

  it('renders a workflow approval surface', () => {
    renderCard({ workflow_run: { run_id: 'wf-1', workflow_id: 'morning', workflow_version: 1, status: 'waiting_for_approval', current_step_id: 'confirm', input_payload: {}, revision: 2 } });
    expect(screen.getByText('Workflow · morning')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeTruthy();
  });

  it('shows thinking inline while tool details stay collapsible', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue(fixture({
      run_id: 'run-repair',
      status: 'running',
      desired_state: 'running',
      revision: 3,
      spec: { profile: 'coding', task: 'Fix the issue in code', evidence_policy: { requirements: [] } },
    }));
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([
      fixture({
        event_id: 'activity-1',
        run_id: 'run-repair',
        sequence: 1,
        event_type: 'model.message',
        payload: { text: 'I found the validation failure and I am correcting the implementation.' },
        created_at: '2026-08-29T00:00:00Z',
      }),
      fixture({
        event_id: 'activity-2',
        run_id: 'run-repair',
        sequence: 2,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'tool-1',
          tool: 'powershell',
          args: { command: 'python -m pytest src/tests/live_speech -q' },
        },
        created_at: '2026-08-29T00:00:01Z',
      }),
      fixture({
        event_id: 'activity-3',
        run_id: 'run-repair',
        sequence: 3,
        event_type: 'tool.completed',
        payload: {
          tool_call_id: 'tool-1',
          tool: 'powershell',
          is_error: false,
          result: { details: { exitCode: 1, stderr: '2 failed, 18 passed' } },
        },
        created_at: '2026-08-29T00:00:02Z',
      }),
      fixture({
        event_id: 'activity-4b',
        run_id: 'run-repair',
        sequence: 4,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'tool-2',
          tool: 'read',
          args: { path: 'web/src/features/assistant/chat/OmnixRunCard.tsx' },
        },
        created_at: '2026-08-29T00:00:02Z',
      }),
      fixture({
        event_id: 'activity-4c',
        run_id: 'run-repair',
        sequence: 5,
        event_type: 'tool.completed',
        payload: {
          tool_call_id: 'tool-2',
          tool: 'read',
          is_error: false,
          result: { output: 'source loaded' },
        },
        created_at: '2026-08-29T00:00:02Z',
      }),
      fixture({
        event_id: 'activity-4',
        run_id: 'run-repair',
        sequence: 4,
        event_type: 'acceptance.completed',
        payload: {
          passed: false,
          retrying: true,
          failures: ['successful_test_command'],
        },
        created_at: '2026-08-29T00:00:03Z',
      }),
      fixture({
        event_id: 'activity-5',
        run_id: 'run-repair',
        sequence: 5,
        event_type: 'acceptance.retry_requested',
        payload: { attempt: 1, failures: ['successful_test_command'] },
        created_at: '2026-08-29T00:00:04Z',
      }),
    ]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-repair',
      evaluated_at: '2026-08-29T00:00:04Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-repair',
        status: 'running',
        profile: 'coding',
        task: 'Fix the issue in code',
        revision: 3,
      },
    });

    const thinking = await screen.findByText('Thinking');
    expect(thinking.closest('details')).toBeNull();
    expect(screen.getByText(/I found the validation failure/)).toBeTruthy();

    const failedTool = screen.getByText('Failed command');
    const toolGroup = screen.getByText('2 tool calls').closest('details') as HTMLDetailsElement;
    expect(toolGroup.open).toBe(false);
    expect(failedTool.closest('.assistant-runtime-tool-call')).toBeTruthy();
    expect(toolGroup.querySelectorAll('.assistant-runtime-tool-call')).toHaveLength(2);
    expect(toolGroup.querySelectorAll('details')).toHaveLength(0);
    fireEvent.click(toolGroup.querySelector('summary')!);
    expect(toolGroup.open).toBe(true);

    expect(screen.getAllByText('python -m pytest src/tests/live_speech -q').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 failed, 18 passed/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Acceptance needs another pass; retrying/)).toBeTruthy();
    expect(screen.getAllByText('Automatic repair attempt 1 started').length).toBeGreaterThan(0);
  });

  it('keeps an in-flight Pi tool inspectable directly under Thinking', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue(fixture({
      run_id: 'run-thinking',
      status: 'running',
      desired_state: 'running',
      revision: 1,
      spec: { profile: 'coding', task: 'Inspect the repository', evidence_policy: { requirements: [] } },
    }));
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([
      fixture({
        event_id: 'thinking-tool-1',
        run_id: 'run-thinking',
        sequence: 1,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'tool-live',
          tool: 'powershell',
          args: { command: 'git status --short --branch' },
        },
        created_at: '2026-09-03T00:00:00Z',
      }),
      fixture({
        event_id: 'thinking-tool-2',
        run_id: 'run-thinking',
        sequence: 2,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'tool-live-2',
          tool: 'read',
          args: { path: 'web/src/features/assistant/chat/OmnixRunCardCore.tsx' },
        },
        created_at: '2026-09-03T00:00:01Z',
      }),
    ]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-thinking',
      evaluated_at: '2026-09-03T00:00:00Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-thinking',
        status: 'running',
        profile: 'coding',
        task: 'Inspect the repository',
        revision: 1,
      },
    });

    const thinking = await screen.findByText('Thinking');
    expect(thinking.closest('details')).toBeNull();
    expect(screen.getAllByText('2 tool calls')).toHaveLength(1);

    const runningTool = screen.getAllByText('Running command').find(
      (node) => node.closest('.assistant-runtime-tool-call-heading'),
    )!;
    const toolGroup = screen.getByText('2 tool calls').closest('details') as HTMLDetailsElement;
    expect(toolGroup.open).toBe(false);

    fireEvent.click(toolGroup.querySelector('summary')!);
    expect(toolGroup.open).toBe(true);
    expect(screen.getByText('git status --short --branch')).toBeTruthy();
    expect(screen.getByText('web/src/features/assistant/chat/OmnixRunCardCore.tsx')).toBeTruthy();
    expect(screen.getAllByText(/Tool is still running/)).toHaveLength(2);
  });

  it('shows durable progress, tests, and diff evidence', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue(fixture({
      run_id: 'run-evidence',
      status: 'completed',
      desired_state: 'running',
      revision: 5,
      started_at: '2026-08-27T00:00:00Z',
      completed_at: '2026-08-27T00:01:37Z',
      usage: { input_tokens: 1234, output_tokens: 567, input_tokens_reported: true, output_tokens_reported: true },
      spec: { profile: 'coding', task: 'Fix tests', request_mode: { mode: 'agent', source: 'classifier' }, evidence_policy: { requirements: [] } },
    }));
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([
      fixture({
        event_id: 'event-plan',
        run_id: 'run-evidence',
        sequence: 0,
        event_type: 'model.message',
        payload: {
          phase: 'message_end',
          text: 'I will inspect the repository and make the requested change.',
        },
        created_at: '2026-08-27T00:00:00Z',
      }),
      fixture({
        event_id: 'event-1',
        run_id: 'run-evidence',
        sequence: 1,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'tool-1',
          tool: 'bash',
          args: { command: 'python -m pytest src/tests/agent_runtime -q' },
        },
        created_at: '2026-08-27T00:00:00Z',
      }),
      fixture({
        event_id: 'event-2',
        run_id: 'run-evidence',
        sequence: 2,
        event_type: 'tool.completed',
        payload: { tool_call_id: 'tool-1', tool: 'bash', is_error: false },
        created_at: '2026-08-27T00:00:01Z',
      }),
      fixture({
        event_id: 'event-3',
        run_id: 'run-evidence',
        sequence: 3,
        event_type: 'model.message',
        payload: {
          text: 'Implemented the requested fix.\n\n- Updated the runtime UI.\n\nVerification:\n\n- `npm test` passed.',
        },
        created_at: '2026-08-27T00:00:01Z',
      }),
      fixture({
        event_id: 'event-review-stage',
        run_id: 'run-evidence',
        sequence: 4,
        event_type: 'quality.stage',
        payload: { stage: 'self_review' },
        created_at: '2026-08-27T00:01:00Z',
      }),
      fixture({
        event_id: 'event-review',
        run_id: 'run-evidence',
        sequence: 5,
        event_type: 'model.message',
        payload: {
          phase: 'message_end',
          text: '{"verdict":"approve","requirements":[],"findings":[],"missing_tests":[],"residual_risks":[]}',
        },
        created_at: '2026-08-27T00:01:01Z',
      }),
      fixture({
        event_id: 'event-4',
        run_id: 'run-evidence',
        sequence: 6,
        event_type: 'acceptance.completed',
        payload: { passed: true },
        created_at: '2026-08-27T00:01:37Z',
      }),
    ]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue(fixture([{
      revision_id: 'revision-1',
      run_id: 'run-evidence',
      sequence: 1,
      user_instruction: 'Fix tests',
      effective_objective: 'Fix tests',
      evidence_decision: { confidence: 0.98, reason: 'required:repo_ci_state', classifier: 'deterministic', policy: {} },
      required_local_capabilities: [],
      required_external_capabilities: [],
      expected_artifacts: ['diff'],
      acceptance_checks: ['successful_test_command'],
      created_at: '2026-08-27T00:00:00Z',
    }]));
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-evidence',
      evaluated_at: '2026-08-27T00:00:02Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: ['manifest:run-evidence'],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([
      {
        artifact_id: 'artifact-1',
        run_id: 'run-evidence',
        kind: 'diff',
        name: 'workspace.diff',
        storage_ref: 'agent/runs/workspace/run/workspace.diff',
        checksum: 'abc',
        metadata: {
          preview: 'diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n-old\n+fixed',
          file_stats: [{ path: 'a.ts', additions: 1, deletions: 1 }],
          additions: 1,
          deletions: 1,
        },
        created_at: '2026-08-27T00:00:02Z',
      },
    ]);

    renderCard({
      agent_run: {
        run_id: 'run-evidence',
        status: 'completed',
        profile: 'coding',
        task: 'Fix tests',
        revision: 5,
      },
    });

    expect(await screen.findByText('Thinking')).toBeTruthy();
    expect(screen.getByText('Ran command')).toBeTruthy();
    expect(screen.getAllByText('Acceptance passed').length).toBeGreaterThan(0);
    expect(screen.getByText('View tests')).toBeTruthy();
    expect(screen.getByText('View diff')).toBeTruthy();
    expect(screen.getByText('Authority & evidence')).toBeTruthy();
    expect(await screen.findByText('manifest:run-evidence')).toBeTruthy();
    expect(screen.getByText('Worked for 1m 37s')).toBeTruthy();
    expect(screen.getByText('Input tokens')).toBeTruthy();
    expect(screen.getByText('1,234')).toBeTruthy();
    expect(screen.getByText('567')).toBeTruthy();
    expect(screen.getByText('Implemented the requested fix.')).toBeTruthy();
    expect(screen.getByText('Updated the runtime UI.')).toBeTruthy();
    const completion = screen.getByRole('region', { name: 'Coding agent completion summary' });
    expect(completion.textContent).toContain('Implemented the requested fix.');
    expect(completion.textContent).not.toContain('"verdict":"approve"');
    expect(screen.getByText('I will inspect the repository and make the requested change.')).toBeTruthy();
    expect(screen.getByText('Edited 1 file')).toBeTruthy();
    expect(screen.getByText('a.ts')).toBeTruthy();
    expect(screen.getAllByText('+1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('-1').length).toBeGreaterThan(0);
  });

  it('falls back to a task summary when the implementation response is missing', async () => {
    vi.spyOn(assistantApiClient, 'getAgentRun').mockResolvedValue({
      run_id: 'run-fallback-summary',
      status: 'completed',
      desired_state: 'running',
      revision: 2,
      spec: { profile: 'coding', task: 'Add the sidebar collapse control' },
    } as never);
    vi.spyOn(assistantApiClient, 'listAgentRunEvents').mockResolvedValue([
      fixture({
        event_id: 'fallback-plan',
        run_id: 'run-fallback-summary',
        sequence: 1,
        event_type: 'model.message',
        payload: { phase: 'message_end', text: 'I will inspect the sidebar first.' },
        created_at: '2026-09-09T00:00:00Z',
      }),
      fixture({
        event_id: 'fallback-tool-start',
        run_id: 'run-fallback-summary',
        sequence: 2,
        event_type: 'tool.started',
        payload: {
          tool_call_id: 'fallback-tool',
          tool: 'bash',
          args: { command: 'npm run test -- sidebar' },
        },
        created_at: '2026-09-09T00:00:01Z',
      }),
      fixture({
        event_id: 'fallback-tool-complete',
        run_id: 'run-fallback-summary',
        sequence: 3,
        event_type: 'tool.completed',
        payload: { tool_call_id: 'fallback-tool', tool: 'bash', is_error: false },
        created_at: '2026-09-09T00:00:02Z',
      }),
      fixture({
        event_id: 'fallback-acceptance',
        run_id: 'run-fallback-summary',
        sequence: 4,
        event_type: 'acceptance.completed',
        payload: { passed: true },
        created_at: '2026-09-09T00:00:03Z',
      }),
    ]);
    vi.spyOn(assistantApiClient, 'listAgentTaskRevisions').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'getAgentEvidenceSet').mockResolvedValue({
      run_id: 'run-fallback-summary',
      evaluated_at: '2026-09-09T00:00:03Z',
      requirements: [],
      missing_requirements: [],
      stale_receipts: [],
      wrong_subject_receipts: [],
      insufficient_trust_receipts: [],
      source_manifest_ids: [],
      attribution_refs: [],
      passed: true,
    });
    vi.spyOn(assistantApiClient, 'listAgentEvidenceReceipts').mockResolvedValue([]);
    vi.spyOn(assistantApiClient, 'listAgentArtifacts').mockResolvedValue([]);

    renderCard({
      agent_run: {
        run_id: 'run-fallback-summary',
        status: 'completed',
        profile: 'coding',
        task: 'Add the sidebar collapse control',
        revision: 2,
      },
    });

    const completion = await screen.findByRole('region', { name: 'Coding agent completion summary' });
    expect(completion.textContent).toContain('Completed the requested coding task: Add the sidebar collapse control.');
    expect(completion.textContent).toContain('npm run test -- sidebar');
    expect(completion.textContent).not.toContain('I will inspect the sidebar first.');
  });
});
