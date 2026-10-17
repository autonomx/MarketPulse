/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { renderMarkdownHtml } from './markdownRenderer';
import './OmnixRunCard.css';
import { assistantApiClient, type AgentRunSnapshot, type TaskGraphRunSnapshot } from '../api/assistantClient';

type Metadata = Record<string, unknown>;

/**
 * What the run cards render. A chat message's metadata gives a partial snapshot
 * until the first fetch, so every field beyond the identity is optional.
 */
type AgentRunView = Partial<Omit<AgentRunSnapshot, 'run_id' | 'status' | 'spec'>> & {
  run_id: string;
  status: string;
  spec: Partial<Omit<AgentRunSnapshot['spec'], 'profile' | 'task'>> & { profile: string; task: string };
};
type TaskGraphRunView = Partial<Omit<TaskGraphRunSnapshot, 'run_id' | 'status' | 'graph' | 'node_states'>> & {
  run_id: string;
  status: string;
  graph: { graph_id?: string; revision?: number; nodes: Array<{ id: string; kind: string; profile_id?: string | null; objective?: string }>; output_contract?: Metadata; reference_context?: string };
  node_states?: Array<{ node_id: string; status: string; child_run_id?: string | null; last_error?: string | null; output?: Metadata }>;
};

function asRecord(value: unknown): Metadata | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Metadata : null;
}

function runId(value: Metadata | null): string {
  return typeof value?.run_id === 'string' ? value.run_id : '';
}

function stringField(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function displayRunStatus(value: string): string {
  if (value === 'resume_requested') return 'recovering';
  if (value === 'pause_requested') return 'pausing';
  if (value === 'cancel_requested') return 'cancelling';
  if (value === 'waiting_for_input') return 'waiting for your input';
  return value;
}

function resultSummary(value: unknown, depth = 0): string {
  if (depth > 3 || value == null) return '';
  if (typeof value === 'string') return value.replace(/\s+/g, ' ').trim().slice(0, 320);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value
      .map((item) => resultSummary(item, depth + 1))
      .filter(Boolean)
      .join(' · ')
      .slice(0, 320);
  }
  const row = asRecord(value);
  if (!row) return '';
  const preferredKeys = ['error', 'message', 'stderr', 'stdout', 'output', 'text', 'content', 'details'];
  for (const key of preferredKeys) {
    if (!(key in row)) continue;
    const summary = resultSummary(row[key], depth + 1);
    if (summary) return summary;
  }
  const exitCode = toolExitCode(row);
  return exitCode !== null ? `exit code ${exitCode}` : '';
}

function toolExitCode(value: unknown): number | null {
  const row = asRecord(value);
  if (!row) return null;
  const direct = row.exitCode ?? row.exit_code;
  if (typeof direct === 'number' && Number.isFinite(direct)) return direct;
  if (typeof direct === 'string' && direct.trim() && Number.isFinite(Number(direct))) return Number(direct);
  return row.details ? toolExitCode(row.details) : null;
}

function toolFailed(payload: Metadata): boolean {
  const exitCode = toolExitCode(payload.result);
  return payload.is_error === true || (exitCode !== null && exitCode !== 0);
}

type ActivityTone = 'neutral' | 'success' | 'failure';

type ActivityItem =
  | {
      kind: 'message';
      key: string;
      text: string;
    }
  | {
      kind: 'tool';
      key: string;
      tool: string;
      title: string;
      status: 'running' | 'completed' | 'failed';
      args: Metadata;
      result: unknown;
    }
  | {
      kind: 'status';
      key: string;
      label: string;
      tone: ActivityTone;
    };

type ToolActivityItem = Extract<ActivityItem, { kind: 'tool' }>;

type ActivitySection =
  | {
      kind: 'thinking';
      key: string;
      text: string;
      tools: ToolActivityItem[];
    }
  | Extract<ActivityItem, { kind: 'status' }>
  | {
      kind: 'tool-group';
      key: string;
      tools: ToolActivityItem[];
    };

function compactText(value: string, limit = 96): string {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length <= limit ? text : `${text.slice(0, Math.max(1, limit - 1))}…`;
}
function prettyValue(value: unknown): string {
  if (value == null) return '';
  let text = '';
  if (typeof value === 'string') {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
  }
  const limit = 16_000;
  return text.length <= limit
    ? text
    : `${text.slice(0, limit)}\n… output truncated …`;
}

function humanizeToolName(value: string): string {
  const clean = value
    .replace(/^mcp__[^_]+__/, '')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return clean || 'tool';
}

function toolActivityTitle(
  tool: string,
  args: Metadata,
  status: 'running' | 'completed' | 'failed',
): string {
  const command = stringField(args.command);
  const path = stringField(args.path) || stringField(args.file_path);
  const verb = status === 'running' ? 'Running' : status === 'failed' ? 'Failed' : 'Ran';
  if (command) return `${verb} command`;
  if (path) {
    const action = /edit|write|patch|update/i.test(tool) ? 'file edit' : 'file read';
    return `${verb} ${action}`;
  }
  return `${verb} ${humanizeToolName(tool)}`;
}

function acceptanceActivityLabel(event: { event_type: string; payload: Metadata }): {
  label: string;
  tone: ActivityTone;
} | null {
  if (event.event_type === 'run.started') return { label: 'Agent started', tone: 'neutral' };
  if (event.event_type === 'run.recovery_requested') {
    const attempt = Number(event.payload.attempt ?? 0);
    return { label: `Runtime stalled; recovery attempt ${attempt || '?'}`, tone: 'neutral' };
  }
  if (event.event_type === 'run.recovery_failed') return { label: 'Automatic recovery failed', tone: 'failure' };
  if (event.event_type === 'run.stall_suspected') return { label: 'Pi may be stalled; automatic recovery was not started', tone: 'neutral' };
  if (event.event_type === 'run.unsandboxed') return { label: 'Running without the sandbox: every command needs approval', tone: 'failure' };
  if (event.event_type === 'steering.received') return { label: 'Steering received', tone: 'neutral' };
  if (event.event_type === 'acceptance.started') return { label: 'Verifying acceptance', tone: 'neutral' };
  if (event.event_type === 'acceptance.completed') {
    if (event.payload.passed !== false) return { label: 'Acceptance passed', tone: 'success' };
    const failures = Array.isArray(event.payload.failures)
      ? event.payload.failures.map(String).filter(Boolean).join(', ')
      : '';
    if (event.payload.retrying === true) {
      return {
        label: `Acceptance needs another pass; retrying${failures ? ` · ${failures}` : ''}`,
        tone: 'neutral',
      };
    }
    return {
      label: `Acceptance failed${failures ? ` · ${failures}` : ''}`,
      tone: 'failure',
    };
  }
  if (event.event_type === 'acceptance.retry_requested') {
    const attempt = Number(event.payload.attempt ?? 0);
    return { label: `Automatic repair attempt ${attempt || '?'} started`, tone: 'neutral' };
  }
  if (event.event_type === 'run.failed') return { label: 'Agent failed', tone: 'failure' };
  return null;
}

function activityItems(
  events: Array<{ event_id?: string; event_type: string; payload: Metadata }>,
): ActivityItem[] {
  const completed = new Map<string, Metadata>();
  const started = new Set<string>();
  events.forEach((event) => {
    if (event.event_type === 'tool.started') {
      const id = stringField(event.payload.tool_call_id);
      if (id) started.add(id);
    }
    if (event.event_type === 'tool.completed') {
      const id = stringField(event.payload.tool_call_id);
      if (id) completed.set(id, event.payload);
    }
  });

  const rows: ActivityItem[] = [];
  events.forEach((event, index) => {
    const key = stringField(event.event_id) || `${event.event_type}-${index}`;
    if (event.event_type === 'model.message') {
      const text = stringField(event.payload.text).trim();
      if (text) rows.push({ kind: 'message', key, text });
      return;
    }
    if (event.event_type === 'tool.started') {
      const id = stringField(event.payload.tool_call_id);
      const tool = stringField(event.payload.tool) || 'tool';
      const args = asRecord(event.payload.args) ?? {};
      const result = id ? completed.get(id) : undefined;
      const status = result ? (toolFailed(result) ? 'failed' : 'completed') : 'running';
      rows.push({
        kind: 'tool',
        key: id || key,
        tool,
        title: toolActivityTitle(tool, args, status),
        status,
        args,
        result: result?.result,
      });
      return;
    }
    if (event.event_type === 'tool.completed') {
      const id = stringField(event.payload.tool_call_id);
      if (id && started.has(id)) return;
      const tool = stringField(event.payload.tool) || 'tool';
      const args: Metadata = {};
      const status = toolFailed(event.payload) ? 'failed' : 'completed';
      rows.push({
        kind: 'tool',
        key: id || key,
        tool,
        title: toolActivityTitle(tool, args, status),
        status,
        args,
        result: event.payload.result,
      });
      return;
    }
    const status = acceptanceActivityLabel(event);
    if (status) rows.push({ kind: 'status', key, ...status });
  });
  return rows;
}

function activitySummary(items: ActivityItem[]): string {
  const latest = items.at(-1);
  if (!latest) return '';
  if (latest.kind === 'message') return compactText(latest.text, 88);
  if (latest.kind === 'tool') return latest.title;
  return latest.label;
}

function activitySections(items: ActivityItem[]): ActivitySection[] {
  const sections: ActivitySection[] = [];
  let thinking: Extract<ActivitySection, { kind: 'thinking' }> | null = null;
  let standaloneTools: Extract<ActivitySection, { kind: 'tool-group' }> | null = null;

  const flushThinking = (): void => {
    if (!thinking) return;
    sections.push(thinking);
    thinking = null;
  };

  const flushStandaloneTools = (): void => {
    if (!standaloneTools) return;
    sections.push(standaloneTools);
    standaloneTools = null;
  };

  items.forEach((item) => {
    if (item.kind === 'message') {
      flushStandaloneTools();
      flushThinking();
      thinking = {
        kind: 'thinking',
        key: item.key,
        text: item.text,
        tools: [],
      };
      return;
    }
    if (item.kind === 'tool') {
      if (thinking) {
        thinking.tools.push(item);
      } else if (standaloneTools) {
        standaloneTools.tools.push(item);
      } else {
        standaloneTools = {
          kind: 'tool-group',
          key: `tool-group-${item.key}`,
          tools: [item],
        };
      }
      return;
    }
    flushStandaloneTools();
    flushThinking();
    sections.push(item);
  });
  flushStandaloneTools();
  flushThinking();
  return sections;
}

function ToolCallGroup({ tools }: { tools: ToolActivityItem[] }) {
  const toolNames = [...new Set(tools.map((item) => humanizeToolName(item.tool)))].join(' / ');
  return (
    <details className="assistant-runtime-tool-group">
      <summary>
        <span className="assistant-runtime-tool-icon" aria-hidden="true">+</span>
        <span>{tools.length === 1 ? '1 tool call' : `${tools.length} tool calls`}</span>
        <small>{toolNames}</small>
      </summary>
      <div role="group" className="assistant-runtime-tool-group-body" aria-label="Tool calls">
        {tools.map((item) => {
          const command = stringField(item.args.command);
          const path = stringField(item.args.path) || stringField(item.args.file_path);
          const result = prettyValue(item.result);
          const showArgs = Object.keys(item.args).length > 0 && !command && !path;
          return (
            <div
              className="assistant-runtime-tool-call"
              data-tool-status={item.status}
              key={item.key}
            >
              <div className="assistant-runtime-tool-call-heading">
                <span className="assistant-runtime-tool-icon" aria-hidden="true">+</span>
                <span>{item.title}</span>
                <small>{humanizeToolName(item.tool)}</small>
              </div>
              <div className="assistant-runtime-tool-body">
                {command ? (
                  <div>
                    <strong>Command</strong>
                    <pre>{command}</pre>
                  </div>
                ) : null}
                {path ? (
                  <div>
                    <strong>Path</strong>
                    <pre>{path}</pre>
                  </div>
                ) : null}
                {showArgs ? (
                  <div>
                    <strong>Arguments</strong>
                    <pre>{prettyValue(item.args)}</pre>
                  </div>
                ) : null}
                {result ? (
                  <div>
                    <strong>{item.status === 'failed' ? 'Error / output' : 'Result'}</strong>
                    <pre>{result}</pre>
                  </div>
                ) : item.status === 'running' ? (
                  <span className="assistant-runtime-tool-running">Tool is still running...</span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
}
function testEvidence(
  events: Array<{ event_type: string; payload: Metadata }>,
): Array<{ id: string; command: string; status: string; detail: string }> {
  const completed = new Map<string, Metadata>();
  events.forEach((event) => {
    if (event.event_type !== 'tool.completed') return;
    const id = stringField(event.payload.tool_call_id);
    if (id) completed.set(id, event.payload);
  });
  const testPattern = /(?:pytest|vitest|npm\s+(?:run\s+)?test|typecheck|\btsc\b|\bruff\b)/i;
  return events.flatMap((event, index) => {
    if (event.event_type !== 'tool.started') return [];
    const args = asRecord(event.payload.args);
    const command = stringField(args?.command);
    if (!command || !testPattern.test(command)) return [];
    const id = stringField(event.payload.tool_call_id) || `test-${index}`;
    const result = completed.get(id);
    return [{
      id,
      command,
      status: result ? (toolFailed(result) ? 'failed' : 'passed') : 'running',
      detail: result && toolFailed(result) ? resultSummary(result.result) : '',
    }];
  });
}

type DiffFileStat = { path: string; additions: number; deletions: number };

type TerminalSummary = { text: string; eventIndex: number };

function isTerminalAssistantMessage(event: { event_type: string; payload: Metadata }): boolean {
  if (event.event_type !== 'model.message') return false;
  const phase = stringField(event.payload.phase);
  return (!phase || phase === 'message_end' || phase === 'turn_end')
    && Boolean(stringField(event.payload.text).trim());
}

function terminalSummary(
  events: Array<{ event_type: string; payload: Metadata }>,
): TerminalSummary | null {
  // A quality-gated coding run emits a structured self-review message after
  // the implementation response. It is durable evidence for Omnix, not the
  // user-facing completion summary. Keep the summary in the implementation
  // window so review JSON cannot leak into the completion card.
  const selfReviewMarker = events.reduce((latest, event, index) => (
    event.event_type === 'quality.stage' && event.payload.stage === 'self_review' ? index : latest
  ), -1);
  const end = selfReviewMarker >= 0 ? selfReviewMarker : events.length;
  const lastTool = events.reduce((latest, event, index) => (
    index < end && (event.event_type === 'tool.started' || event.event_type === 'tool.completed')
      ? index
      : latest
  ), -1);
  // A coding completion without an implementation tool has no reliable way
  // to distinguish a planning update from a final response. Prefer the
  // deterministic task/diff/check summary in that case.
  if (lastTool < 0) return null;
  for (let index = end - 1; index > lastTool; index -= 1) {
    const event = events[index];
    if (!isTerminalAssistantMessage(event)) continue;
    return { text: stringField(event.payload.text).trim(), eventIndex: index };
  }
  return null;
}

function runElapsedLabel(
  events: Array<{ event_type: string; created_at?: string }>,
): string {
  const started = events.find((event) => event.event_type === 'run.started') ?? events[0];
  const start = Date.parse(started?.created_at ?? '');
  const end = Math.max(...events.map((event) => Date.parse(event.created_at ?? '')).filter(Number.isFinite));
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '';
  const totalSeconds = Math.max(0, Math.round((end - start) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatElapsedMilliseconds(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.round(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function runDurationLabel(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
  events: Array<{ event_type: string; created_at?: string }>,
): string {
  const start = Date.parse(startedAt ?? '');
  const end = completedAt ? Date.parse(completedAt) : Date.now();
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
    return formatElapsedMilliseconds(end - start);
  }
  return runElapsedLabel(events);
}

function formatTokenCount(value: unknown, reported: unknown): string {
  if (reported !== true) return '—';
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.round(count).toLocaleString() : '—';
}

function diffFileStats(metadata: Metadata, preview: string): DiffFileStat[] {
  const stored = Array.isArray(metadata.file_stats)
    ? metadata.file_stats
      .map(asRecord)
      .filter((value): value is Metadata => Boolean(value))
      .map((value) => ({
        path: stringField(value.path),
        additions: Math.max(0, Number(value.additions ?? 0) || 0),
        deletions: Math.max(0, Number(value.deletions ?? 0) || 0),
      }))
      .filter((value) => Boolean(value.path))
    : [];
  if (stored.length) return stored;

  const modifiedPaths = Array.isArray(metadata.modified_paths)
    ? metadata.modified_paths.map(String).filter(Boolean)
    : [];
  const stats = new Map<string, DiffFileStat>(
    modifiedPaths.map((path) => [path, { path, additions: 0, deletions: 0 }]),
  );
  let currentPath = '';
  preview.split('\n').forEach((line) => {
    if (line.startsWith('diff --git ')) {
      currentPath = '';
      return;
    }
    if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      let candidate = line.slice(4).trim();
      if (candidate !== '/dev/null') {
        if (candidate.startsWith('a/') || candidate.startsWith('b/')) candidate = candidate.slice(2);
        currentPath = candidate;
        if (!stats.has(candidate)) stats.set(candidate, { path: candidate, additions: 0, deletions: 0 });
      }
      return;
    }
    const current = stats.get(currentPath);
    if (!current) return;
    if (line.startsWith('+')) current.additions += 1;
    if (line.startsWith('-')) current.deletions += 1;
  });
  return [...stats.values()];
}

function fallbackCompletionSummary(
  task: string,
  tests: Array<{ command: string; status: string }>,
): string {
  const verification = tests.length
    ? `\n\nVerification:\n${tests.map((test) => `- \`${test.command}\` ${test.status}.`).join('\n')}`
    : '';
  return `Completed the requested coding task: ${task}.${verification}`;
}

const ACTIVITY_RENDER_LIMIT = 40;
const AGENT_EVENT_PAGE_SIZE = 500;
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

async function listAllAgentRunEvents(runId: string) {
  const allEvents = [];
  let afterSequence = 0;

  while (true) {
    const page = await assistantApiClient.listAgentRunEvents(runId, afterSequence);
    allEvents.push(...page);
    if (page.length < AGENT_EVENT_PAGE_SIZE) return allEvents;

    const nextSequence = page.at(-1)?.sequence;
    if (typeof nextSequence !== 'number' || nextSequence <= afterSequence) return allEvents;
    afterSequence = nextSequence;
  }
}

function unresolvedStallWarning(
  events: Array<{ event_type: string; payload: Metadata }>,
): Metadata | null {
  let stallIndex = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].event_type !== 'run.stall_suspected') continue;
    stallIndex = index;
    break;
  }
  if (stallIndex < 0) return null;
  const resumed = events.slice(stallIndex + 1).some((event) => (
    event.event_type === 'model.message'
    || event.event_type === 'tool.started'
    || event.event_type === 'tool.output'
    || event.event_type === 'tool.completed'
    || event.event_type === 'run.settled'
  ));
  return resumed ? null : events[stallIndex].payload;
}

export function OmnixRunCard({ metadata }: { metadata?: Metadata }) {
  const agent = asRecord(metadata?.agent_run);
  if (runId(agent)) return <AgentRunCard initial={agent!} routing={metadata} />;
  const taskGraph = asRecord(metadata?.task_graph_run);
  if (runId(taskGraph)) return <TaskGraphRunCard initial={taskGraph!} />;
  const workflow = asRecord(metadata?.workflow_run);
  if (runId(workflow)) return <WorkflowRunCard initial={workflow!} />;
  return null;
}

// WP-4.7: an operator let this run go unsandboxed; it says so for the run's whole life.
function UnsandboxedRunWarning({ events }: { events: Array<{ event_type: string; payload: Metadata }> }) {
  const unsandboxed = events.find((event) => event.event_type === 'run.unsandboxed');
  if (!unsandboxed) return null;
  return (
    <section className="assistant-runtime-stall-warning" role="region" aria-label="Running without the sandbox">
      <div>
        <strong>Running without the sandbox</strong>
        <p>{stringField(unsandboxed.payload.reason) || 'The agent sandbox is unavailable.'}</p>
        <small>An operator allowed unsandboxed agent runs; every command this agent runs needs your approval.</small>
      </div>
    </section>
  );
}

function StallWarning({ reason, pending, onRecover }: { reason: string; pending: boolean; onRecover: () => void }) {
  return (
    <section className="assistant-runtime-stall-warning" aria-live="polite" aria-label="Possible Pi stall">
      <div>
        <strong>Pi may be stalled</strong>
        <p>{reason || 'No agent activity has been observed recently.'}</p>
        <small>Omnix has left the Pi session running and will not restart it automatically.</small>
      </div>
      <button type="button" disabled={pending} onClick={onRecover}>Interrupt and recover</button>
    </section>
  );
}

function AgentRunCard({ initial, routing }: { initial: Metadata; routing?: Metadata }) {
  const id = runId(initial);
  const queryClient = useQueryClient();
  const [steeringMessage, setSteeringMessage] = useState('');
  const [showAllActivity, setShowAllActivity] = useState(false);
  const query = useQuery<AgentRunView>({
    queryKey: ['agent-run', id],
    queryFn: () => assistantApiClient.getAgentRun(id),
    initialData: {
      run_id: id,
      status: String(initial.status ?? 'starting'),
      desired_state: 'running',
      revision: Number(initial.revision ?? 1),
      usage: { input_tokens: 0, output_tokens: 0, input_tokens_reported: false, output_tokens_reported: false },
      last_error: typeof initial.last_error === 'string' ? initial.last_error : null,
      spec: {
        profile: String(initial.profile ?? 'agent'),
        task: String(initial.task ?? 'Agent task'),
      },
    },
    refetchInterval: (state) => TERMINAL.has(String(state.state.data?.status ?? '')) ? false : 1500,
  });
  const status = query.data.status;
  const live = !TERMINAL.has(status);
  const thinkingLive = live && status !== 'waiting_for_input';
  const events = useQuery({
    queryKey: ['agent-run', id, 'events'],
    queryFn: () => listAllAgentRunEvents(id),
    refetchInterval: live ? 1500 : false,
  });
  const artifacts = useQuery({
    queryKey: ['agent-run', id, 'artifacts'],
    queryFn: () => assistantApiClient.listAgentArtifacts(id),
    refetchInterval: live ? 2000 : false,
  });
  const revisions = useQuery({
    queryKey: ['agent-run', id, 'task-revisions'],
    queryFn: () => assistantApiClient.listAgentTaskRevisions(id),
    refetchInterval: live ? 2000 : false,
  });
  const evidence = useQuery({
    queryKey: ['agent-run', id, 'evidence'],
    queryFn: () => assistantApiClient.getAgentEvidenceSet(id),
    refetchInterval: live ? 2000 : false,
  });
  const receipts = useQuery({
    queryKey: ['agent-run', id, 'evidence', 'receipts'],
    queryFn: () => assistantApiClient.listAgentEvidenceReceipts(id),
    refetchInterval: live ? 2000 : false,
  });
  const approvals = useQuery({
    queryKey: ['agent-run', id, 'approvals'],
    queryFn: () => assistantApiClient.listAgentApprovals(id, 'pending'),
    enabled: status === 'waiting_for_approval',
    refetchInterval: status === 'waiting_for_approval' ? 1500 : false,
  });
  const command = useMutation({
    mutationFn: (input: { type: 'steer' | 'pause' | 'resume' | 'cancel' | 'approve' | 'reject'; payload?: Record<string, unknown> }) =>
      assistantApiClient.commandAgentRun(id, input.type, input.payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['agent-run', id] });
      void queryClient.invalidateQueries({ queryKey: ['agent-run', id, 'approvals'] });
      void queryClient.invalidateQueries({ queryKey: ['agent-run', id, 'events'] });
    },
  });
  const canSteer = query.data.spec.profile === 'coding'
    && live
    && status !== 'waiting_for_approval'
    && status !== 'waiting_for_input';
  const submitSteering = (): void => {
    const message = steeringMessage.trim();
    if (!message || command.isPending) return;
    command.mutate(
      { type: 'steer', payload: { message } },
      { onSuccess: () => setSteeringMessage('') },
    );
  };
  const runEvents = (events.data ?? []).map((event) => ({ ...event, payload: event.payload ?? {} }));
  const stallWarning = unresolvedStallWarning(runEvents);
  const clarificationQuestion = status === 'waiting_for_input'
    ? [...runEvents].reverse().find((event) => (
        event.event_type === 'model.message'
        && (event.payload.requires_user_input === true || stringField(event.payload.text).trim())
      ))
    : undefined;
  const completionSummary = status === 'completed' && query.data.spec.profile === 'coding'
    ? terminalSummary(runEvents)
      ?? { text: fallbackCompletionSummary(query.data.spec.task, testEvidence(runEvents)), eventIndex: -1 }
    : null;
  const finalSummary = completionSummary?.text ?? '';
  const summaryEventIndex = completionSummary?.eventIndex ?? -1;
  const activity = activityItems(
    summaryEventIndex >= 0
      ? runEvents.filter((_event, index) => index !== summaryEventIndex)
      : runEvents,
  );
  const renderedActivity = showAllActivity ? activity : activity.slice(-ACTIVITY_RENDER_LIMIT);
  const hiddenActivity = activity.slice(0, Math.max(0, activity.length - renderedActivity.length));
  const hiddenToolCalls = hiddenActivity.filter((item) => item.kind === 'tool').length;
  const totalToolCalls = activity.filter((item) => item.kind === 'tool').length;
  const sections = activitySections(renderedActivity);
  const latestActivity = activitySummary(activity);
  const tests = testEvidence(runEvents);
  const diff = (artifacts.data ?? []).filter((artifact) => artifact.kind === 'diff').at(-1);
  const diffPreview = stringField(diff?.metadata?.preview);
  const changedFiles = diffFileStats(diff?.metadata ?? {}, diffPreview);
  const totalAdditions = changedFiles.reduce((total, file) => total + file.additions, 0);
  const totalDeletions = changedFiles.reduce((total, file) => total + file.deletions, 0);
  const elapsed = runDurationLabel(query.data.started_at, query.data.completed_at, runEvents);
  const inputTokens = formatTokenCount(query.data.usage?.input_tokens, query.data.usage?.input_tokens_reported);
  const outputTokens = formatTokenCount(query.data.usage?.output_tokens, query.data.usage?.output_tokens_reported);
  const latestRevision = (revisions.data ?? []).at(-1);
  const requestMode = asRecord(query.data.spec.request_mode);
  const evidencePolicy = asRecord(query.data.spec.evidence_policy);
  const evidenceRequirements = Array.isArray(evidencePolicy?.requirements)
    ? evidencePolicy.requirements.map(asRecord).filter((value): value is Metadata => Boolean(value))
    : [];
  const attributionRefs = evidence.data?.attribution_refs ?? [];
  const semanticTask = asRecord(routing?.semantic_task);
  const turnPlan = asRecord(routing?.turn_plan);
  const semanticCompilation = asRecord(routing?.semantic_compilation);
  const routingDecision = asRecord(routing?.routing_decision) ?? asRecord(routing?.routing_shadow);
  const authorityCompilation = asRecord(routing?.authority_compilation);
  const legacyRoute = asRecord(routingDecision?.legacy);
  const semanticV2Route = asRecord(routingDecision?.semantic_v2);
  const parserDiagnostics = asRecord(routingDecision?.parser);
  const semanticActions = Array.isArray(semanticCompilation?.action_intents)
    ? semanticCompilation.action_intents.map(String)
    : [];
  const semanticAnomalies = Array.isArray(semanticCompilation?.anomalies)
    ? semanticCompilation.anomalies.map(asRecord).filter((value): value is Metadata => Boolean(value))
    : [];
  const issuedLocal = Array.isArray(authorityCompilation?.issued_local)
    ? authorityCompilation.issued_local.map(String)
    : [];
  const issuedExternal = Array.isArray(authorityCompilation?.issued_external)
    ? authorityCompilation.issued_external.map(String)
    : [];
  const deniedActions = Array.isArray(authorityCompilation?.denied_actions)
    ? authorityCompilation.denied_actions.map(String)
    : [];
  const productionRouter = stringField(routingDecision?.production_router)
    || stringField(routingDecision?.production)
    || 'semantic_v2';
  const productionLane = stringField(routingDecision?.production_lane)
    || (productionRouter === 'semantic_v2' ? stringField(semanticV2Route?.lane) : stringField(legacyRoute?.lane))
    || stringField(asRecord(routing?.omnix_route)?.lane);
  const turnPlanAuthorityDelta = Array.isArray(turnPlan?.authority_delta)
    ? turnPlan.authority_delta.map(String)
    : [];
  const showRouting = Boolean(turnPlan || semanticTask || semanticCompilation || routingDecision || authorityCompilation);

  return (
    <section className="assistant-runtime-card" aria-label="Agent run">
      <header>
        <span>Agent · {query.data.spec.profile}</span>
        <strong data-run-status={status}>{displayRunStatus(status)}</strong>
      </header>
      <p>{query.data.spec.task}</p>
      <small>{id}</small>
      <div role="group" className="assistant-runtime-metrics" aria-label="Run metrics">
        <div><strong>Duration</strong><span>{elapsed || 'not available'}</span></div>
        <div><strong>Input tokens</strong><span title={query.data.usage?.input_tokens_reported ? undefined : 'Not reported'}>{inputTokens}</span></div>
        <div><strong>Output tokens</strong><span title={query.data.usage?.output_tokens_reported ? undefined : 'Not reported'}>{outputTokens}</span></div>
      </div>
      {query.data.last_error ? <p className="assistant-runtime-error">{query.data.last_error}</p> : null}
      <UnsandboxedRunWarning events={runEvents} />
      {stallWarning && query.data.spec.profile === 'coding' && live ? (
        <StallWarning
          reason={stringField(stallWarning.reason)}
          pending={command.isPending}
          onRecover={() => command.mutate({
            type: 'resume',
            payload: { message: 'The user explicitly requested interruption and recovery. Resume the task from the current workspace state.' },
          })}
        />
      ) : null}
      {canSteer ? (
        <form
          className="assistant-runtime-steering"
          aria-label="Steer coding run"
          onSubmit={(event) => {
            event.preventDefault();
            submitSteering();
          }}
        >
          <div className="assistant-runtime-steering-heading">
            <strong>Steer this coding run</strong>
            <small>Send guidance without stopping the current work.</small>
          </div>
          <textarea
            aria-label="Steering guidance"
            value={steeringMessage}
            onChange={(event) => setSteeringMessage(event.currentTarget.value)}
            placeholder="Add guidance for the next safe checkpoint…"
            rows={3}
            disabled={command.isPending}
          />
          <div className="assistant-runtime-steering-footer">
            <small>Use the chat composer for a separate request after this run.</small>
            <button type="submit" disabled={command.isPending || !steeringMessage.trim()}>
              {command.isPending ? 'Sending…' : 'Steer run'}
            </button>
          </div>
        </form>
      ) : null}
      {status === 'waiting_for_input' ? (
        <section className="assistant-runtime-input-request" aria-live="polite" aria-label="Agent clarification">
          <strong>Waiting for your response</strong>
          <p>{stringField(clarificationQuestion?.payload.text) || 'The Agent needs clarification before it can continue.'}</p>
          <small>Reply in the chat composer below to continue this run.</small>
        </section>
      ) : null}
      {(requestMode || latestRevision || evidenceRequirements.length || evidence.data) ? (
        <details className="assistant-runtime-policy">
          <summary>Authority & evidence</summary>
          <div className="assistant-runtime-policy-grid">
            {requestMode ? <div><strong>Mode</strong><span>{stringField(requestMode.mode)} · {stringField(requestMode.source)}</span></div> : null}
            {latestRevision ? <div><strong>Task revision</strong><span>#{latestRevision.sequence} · {latestRevision.evidence_decision?.reason}</span></div> : null}
            <div><strong>Evidence</strong><span>{evidence.data?.passed ? 'satisfied' : evidenceRequirements.length ? 'required' : 'not required'}</span></div>
            {evidenceRequirements.map((requirement, index) => {
              const subject = asRecord(requirement.subject);
              const evaluation = evidence.data?.requirements?.find((row) => row.requirement_id === stringField(requirement.id));
              return (
                <div key={stringField(requirement.id) || `requirement-${index}`}>
                  <strong>{stringField(requirement.source_class) || 'evidence'}</strong>
                  <span>
                    {evaluation?.status ?? 'pending'}
                    {subject ? ` · ${stringField(subject.display_name) || stringField(subject.canonical_id)}` : ''}
                    {requirement.freshness ? ` · ${String(requirement.freshness)}` : ''}
                  </span>
                </div>
              );
            })}
            {(receipts.data ?? []).slice(-5).map((receipt) => (
              <div key={receipt.receipt_id}>
                <strong>Receipt · {receipt.source_class}</strong>
                <span>{receipt.provider ?? receipt.origin ?? receipt.capability_id} · {receipt.trust_level}</span>
              </div>
            ))}
            {attributionRefs.slice(-5).map((reference) => (
              <div key={`attribution-${reference}`}>
                <strong>Source reference</strong>
                <span>{reference}</span>
              </div>
            ))}
            {query.data.superseded_by_run_id ? <div><strong>Superseded by</strong><span>{query.data.superseded_by_run_id}</span></div> : null}
            {query.data.spec.supersedes_run_id ? <div><strong>Supersedes</strong><span>{query.data.spec.supersedes_run_id}</span></div> : null}
          </div>
        </details>
      ) : null}

      {showRouting ? (
        <details className="assistant-runtime-policy">
          <summary>Routing & compiler</summary>
          <div className="assistant-runtime-policy-grid">
            {turnPlan ? (
              <div>
                <strong>Turn plan</strong>
                <span>
                  {stringField(turnPlan.lane) || 'chat'}
                  {stringField(turnPlan.profile_id) ? ` · ${stringField(turnPlan.profile_id)}` : ''}
                  {stringField(turnPlan.disposition) ? ` · ${stringField(turnPlan.disposition)}` : ''}
                  {stringField(turnPlan.run_action) ? ` · ${stringField(turnPlan.run_action)}` : ''}
                  {turnPlanAuthorityDelta.length ? ` · authority=${turnPlanAuthorityDelta.join(', ')}` : ''}
                </span>
              </div>
            ) : null}
            {semanticTask ? (
              <div>
                <strong>Semantic task</strong>
                <span>
                  {stringField(semanticTask.reason_code) || stringField(semanticTask.intent) || 'parsed'}
                  {semanticTask.ambiguity ? ` · ${String(semanticTask.ambiguity)}` : ''}
                </span>
              </div>
            ) : null}
            {semanticCompilation ? (
              <div>
                <strong>Compiled domain</strong>
                <span>
                  {stringField(semanticCompilation.profile_id) || stringField(semanticCompilation.lane) || 'chat'}
                  {semanticActions.length ? ` · ${semanticActions.join(', ')}` : ''}
                </span>
              </div>
            ) : null}
            {parserDiagnostics ? (
              <div>
                <strong>Semantic parser</strong>
                <span>
                  {stringField(parserDiagnostics.model) || stringField(parserDiagnostics.provider) || 'configured model'}
                  {parserDiagnostics.latency_ms != null ? ` · ${String(parserDiagnostics.latency_ms)}ms` : ''}
                  {parserDiagnostics.cache_hit === true ? ' · cache hit' : ''}
                </span>
              </div>
            ) : null}
            {authorityCompilation ? (
              <div>
                <strong>Issued authority</strong>
                <span>
                  {issuedLocal.length ? `local=${issuedLocal.join(', ')}` : 'local=none'}
                  {issuedExternal.length ? ` · external=${issuedExternal.join(', ')}` : ' · external=none'}
                  {deniedActions.length ? ` · denied=${deniedActions.join(', ')}` : ''}
                </span>
              </div>
            ) : null}
            {routingDecision ? (
              <div>
                <strong>Production route</strong>
                <span>
                  {productionRouter}
                  {productionLane ? ` · lane=${productionLane}` : ''}
                </span>
              </div>
            ) : null}
            {semanticAnomalies.map((anomaly, index) => (
              <div key={`semantic-anomaly-${index}`}>
                <strong>Semantic anomaly</strong>
                <span>{stringField(anomaly.code)}{anomaly.detail ? ` · ${String(anomaly.detail)}` : ''}</span>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {activity.length ? (
        <section className="assistant-runtime-thinking" data-live={thinkingLive ? 'true' : 'false'} aria-label="Agent thinking and tools">
          <div className="assistant-runtime-thinking-heading">
            <span className="assistant-runtime-thinking-indicator" aria-hidden="true" />
            <strong>Thinking</strong>
            {totalToolCalls ? (
              <small>{totalToolCalls === 1 ? '1 total tool call' : `${totalToolCalls} total tool calls`}</small>
            ) : null}
          </div>
          {activity.length > ACTIVITY_RENDER_LIMIT ? (
            <div className="assistant-runtime-actions">
              <button
                type="button"
                aria-expanded={showAllActivity}
                onClick={() => setShowAllActivity((value) => !value)}
              >
                {showAllActivity
                  ? `Show latest ${ACTIVITY_RENDER_LIMIT} activity items`
                  : `Show earlier activity${hiddenToolCalls ? ` (${hiddenToolCalls} tool calls)` : ''}`}
              </button>
            </div>
          ) : null}
          <div role="group" className="assistant-runtime-thinking-stream" aria-label="Agent activity">
            {sections.map((section) => {
              if (section.kind === 'thinking') {
                return (
                  <div className="assistant-runtime-thinking-entry" key={section.key}>
                    <p className="assistant-runtime-thinking-message">{section.text}</p>
                    {section.tools.length ? <ToolCallGroup tools={section.tools} /> : null}
                  </div>
                );
              }
              if (section.kind === 'status') {
                const item = section;
                return (
                  <div
                    className="assistant-runtime-thinking-status"
                    data-tone={section.tone}
                    key={section.key}
                  >
                    <span aria-hidden="true">
                      {item.tone === 'success' ? '✓' : item.tone === 'failure' ? '✕' : '·'}
                    </span>
                    <span>{section.label}</span>
                  </div>
                );
              }
              if (section.kind === 'tool-group') {
                return <ToolCallGroup key={section.key} tools={section.tools} />;
              }
              return null;
            })}
          </div>
        </section>
      ) : null}

      {finalSummary ? (
        <section className="assistant-runtime-completion" aria-label="Coding agent completion summary">
          {elapsed ? <div className="assistant-runtime-elapsed">Worked for {elapsed}</div> : null}
          <div
            className="assistant-runtime-final-summary"
            dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(finalSummary) }}
          />
          {changedFiles.length ? (
            <div className="assistant-runtime-changed-files">
              <header>
                <strong>Edited {changedFiles.length} {changedFiles.length === 1 ? 'file' : 'files'}</strong>
                <span><b>+{totalAdditions}</b> <i>-{totalDeletions}</i></span>
              </header>
              {changedFiles.slice(0, 3).map((file) => (
                <div className="assistant-runtime-changed-file" key={file.path}>
                  <code title={file.path}>{file.path}</code>
                  <span><b>+{file.additions}</b> <i>-{file.deletions}</i></span>
                </div>
              ))}
              {changedFiles.length > 3 ? (
                <details>
                  <summary>Show {changedFiles.length - 3} more {changedFiles.length - 3 === 1 ? 'file' : 'files'}</summary>
                  {changedFiles.slice(3).map((file) => (
                    <div className="assistant-runtime-changed-file" key={file.path}>
                      <code title={file.path}>{file.path}</code>
                      <span><b>+{file.additions}</b> <i>-{file.deletions}</i></span>
                    </div>
                  ))}
                </details>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {(diffPreview || tests.length) ? (
        <div className="assistant-runtime-evidence">
          {diffPreview ? (
            <details>
              <summary>View diff</summary>
              <pre>{diffPreview}</pre>
            </details>
          ) : null}
          {tests.length ? (
            <details>
              <summary>View tests</summary>
              <div className="assistant-runtime-test-list">
                {tests.map((test) => (
                  <div key={test.id}>
                    <strong>{test.status}</strong>
                    <div>
                      <code>{test.command}</code>
                      {test.detail ? <pre className="assistant-runtime-test-output">{test.detail}</pre> : null}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </div>
      ) : null}

      <div className="assistant-runtime-actions">
        {status === 'paused'
          ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'resume' })}>Resume</button>
          : !TERMINAL.has(status) && status !== 'waiting_for_approval' && status !== 'waiting_for_input'
            ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'pause' })}>Pause</button>
            : null}
        {!TERMINAL.has(status) ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'cancel' })}>Cancel</button> : null}
      </div>
      {approvals.data?.map((approval) => (
        <div className="assistant-runtime-approval" key={approval.approval_id}>
          <div>
            <span>Permission: {approval.capability_id}</span>
            {typeof approval.request_payload?.command === 'string'
              ? <code className="assistant-runtime-approval-command">{approval.request_payload.command}</code>
              : typeof approval.request_payload?.path === 'string'
                ? <code className="assistant-runtime-approval-command">{approval.request_payload.path}</code>
              : null}
          </div>
          <div>
            <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'approve', payload: { approval_id: approval.approval_id } })}>Approve</button>
            <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'reject', payload: { approval_id: approval.approval_id } })}>Reject</button>
          </div>
        </div>
      ))}
    </section>
  );
}

function TaskGraphRunCard({ initial }: { initial: Metadata }) {
  const id = runId(initial);
  const queryClient = useQueryClient();
  const initialGraph = asRecord(initial.graph);
  const initialNodes = Array.isArray(initialGraph?.nodes) ? initialGraph.nodes : [];
  const initialStates = Array.isArray(initial.node_states) ? initial.node_states : [];
  const query = useQuery<TaskGraphRunView>({
    queryKey: ['task-graph-run', id],
    queryFn: () => assistantApiClient.getTaskGraphRun(id),
    initialData: {
      run_id: id,
      status: String(initial.status ?? 'running'),
      revision: Number(initial.revision ?? 1),
      result: initial.result,
      last_error: typeof initial.last_error === 'string' ? initial.last_error : null,
      graph: {
        graph_id: stringField(initialGraph?.graph_id),
        revision: Number(initialGraph?.revision ?? 1),
        nodes: initialNodes
          .map(asRecord)
          .filter((value): value is Metadata => Boolean(value))
          .map((node) => ({
            id: stringField(node.id),
            kind: stringField(node.kind),
            profile_id: stringField(node.profile_id) || null,
            objective: stringField(node.objective),
          })),
        output_contract: asRecord(initialGraph?.output_contract) ?? {},
      },
      node_states: initialStates
        .map(asRecord)
        .filter((value): value is Metadata => Boolean(value))
        .map((state) => ({
          node_id: stringField(state.node_id),
          status: String(state.status ?? 'pending'),
          child_run_id: stringField(state.child_run_id) || null,
          last_error: stringField(state.last_error) || null,
          output: asRecord(state.output) ?? {},
        })),
    },
    refetchInterval: (state) =>
      TERMINAL.has(String(state.state.data?.status ?? '')) ? false : 1500,
  });
  const command = useMutation({
    mutationFn: (input: { type: 'cancel' | 'approve' | 'reject'; nodeId?: string; approvalId?: string }) =>
      assistantApiClient.commandTaskGraphRun(
        id,
        input.type,
        input.nodeId,
        input.approvalId,
      ),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['task-graph-run', id] }),
  });
  const status = query.data.status;
  const nodeStates = query.data.node_states ?? [];
  const waiting = nodeStates.find(
    (state) => state.status === 'waiting_for_approval',
  );
  const completed = nodeStates.filter(
    (state) => state.status === 'completed' || state.status === 'skipped',
  ).length;
  const result = query.data.result;
  const pendingApprovals = waiting
    ? (
        Array.isArray(waiting.output?.pending_approvals)
          ? waiting.output?.pending_approvals
          : []
      )
        .map(asRecord)
        .filter((value): value is Metadata => Boolean(value))
    : [];
  return (
    <section className="assistant-runtime-card" aria-label="Task graph run">
      <header>
        <span>Agent · Task graph</span>
        <strong data-run-status={status}>{status}</strong>
      </header>
      <small>{completed}/{nodeStates.length} nodes complete · {id}</small>
      {query.data.last_error ? <p className="assistant-runtime-error">{query.data.last_error}</p> : null}
      {typeof result === 'string' && result.trim()
        ? <p data-task-graph-result="true">{result}</p>
        : result != null && TERMINAL.has(status)
          ? <pre data-task-graph-result="true">{JSON.stringify(result, null, 2)}</pre>
          : null}
      {status === 'waiting_for_approval' && waiting ? (
        <div className="assistant-runtime-approval">
          <div>
            <span>
              Permission needed for {waiting.node_id}
            </span>
          </div>
          {pendingApprovals.length ? pendingApprovals.map((approval, index) => {
            const approvalId = stringField(approval.approval_id);
            const capability = stringField(approval.capability_id) || 'child action';
            const request = asRecord(approval.request_payload);
            return (
              <div key={approvalId || `graph-approval-${index}`}>
                <span>{capability}</span>
                {typeof request?.command === 'string'
                  ? <code className="assistant-runtime-approval-command">{String(request.command)}</code>
                  : typeof request?.path === 'string'
                    ? <code className="assistant-runtime-approval-command">{String(request.path)}</code>
                    : null}
                <button
                  type="button"
                  disabled={command.isPending}
                  onClick={() => command.mutate({
                    type: 'approve',
                    nodeId: waiting.node_id,
                    approvalId: approvalId || undefined,
                  })}
                >Approve</button>
                <button
                  type="button"
                  disabled={command.isPending}
                  onClick={() => command.mutate({
                    type: 'reject',
                    nodeId: waiting.node_id,
                    approvalId: approvalId || undefined,
                  })}
                >Reject</button>
              </div>
            );
          }) : <>
            <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'approve', nodeId: waiting.node_id })}>Approve</button>
            <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'reject', nodeId: waiting.node_id })}>Reject</button>
          </>}
        </div>
      ) : null}
      <div className="assistant-runtime-actions">
        {!TERMINAL.has(status) ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'cancel' })}>Cancel</button> : null}
      </div>
    </section>
  );
}

function WorkflowRunCard({ initial }: { initial: Metadata }) {
  const id = runId(initial);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['workflow-run', id],
    queryFn: () => assistantApiClient.getWorkflowRun(id),
    initialData: {
      run_id: id,
      workflow_id: String(initial.workflow_id ?? 'workflow'),
      workflow_version: Number(initial.workflow_version ?? 1),
      status: String(initial.status ?? 'running'),
      current_step_id: typeof initial.current_step_id === 'string' ? initial.current_step_id : null,
      input_payload: asRecord(initial.input_payload) ?? {},
      revision: Number(initial.revision ?? 1),
    },
    refetchInterval: (state) => TERMINAL.has(String(state.state.data?.status ?? '')) ? false : 1500,
  });
  const command = useMutation({
    mutationFn: (input: { type: 'pause' | 'resume' | 'cancel' | 'approve' | 'reject'; stepId?: string }) =>
      assistantApiClient.commandWorkflowRun(id, input.type, input.stepId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workflow-run', id] }),
  });
  const status = query.data.status;
  const stepId = query.data.current_step_id ?? undefined;
  return (
    <section className="assistant-runtime-card" aria-label="Workflow run">
      <header>
        <span>Workflow · {query.data.workflow_id}</span>
        <strong data-run-status={status}>{status}</strong>
      </header>
      <small>{stepId ? `Current step: ${stepId}` : id}</small>
      <div className="assistant-runtime-actions">
        {status === 'waiting_for_approval' && stepId ? <>
          <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'approve', stepId })}>Approve</button>
          <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'reject', stepId })}>Reject</button>
        </> : null}
        {status === 'paused'
          ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'resume' })}>Resume</button>
          : !TERMINAL.has(status) && status !== 'waiting_for_approval'
            ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'pause' })}>Pause</button>
            : null}
        {!TERMINAL.has(status) ? <button type="button" disabled={command.isPending} onClick={() => command.mutate({ type: 'cancel' })}>Cancel</button> : null}
      </div>
    </section>
  );
}
