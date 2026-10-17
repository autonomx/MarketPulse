import { useMemo, useState } from 'react';
import { executeLiveAgentToolProposal, type LiveAgentToolProposal } from './assistantToolConfigClient';

type Props = { proposal: LiveAgentToolProposal; sessionId?: string | null; onOpenTools: () => void };

export function LiveAgentToolProposalCard({ onOpenTools, proposal, sessionId }: Props) {
  const [input, setInput] = useState<Record<string, unknown>>(() => ({ ...proposal.input }));
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [completed, setCompleted] = useState(false);
  const missing = useMemo(() => requiredCalendarFields(proposal.action_id, input), [input, proposal.action_id]);
  const canExecute = !proposal.connection_required && missing.length === 0 && !pending && !completed;

  function update(key: string, value: unknown): void {
    setInput((current) => ({ ...current, [key]: value }));
    setStatus(null);
  }

  async function execute(): Promise<void> {
    setPending(true);
    setStatus(null);
    try {
      const result = await executeLiveAgentToolProposal(proposal, input, sessionId);
      if (result.execution_result.error) {
        setStatus(result.execution_result.error);
        return;
      }
      setCompleted(true);
      setStatus(result.execution_result.result_summary || 'Calendar action completed.');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Calendar action failed.');
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="assistant-tool-proposal" aria-label={proposal.title}>
      <header><div><small>Hermes proposal · {proposal.risk_level} risk</small><strong>{proposal.title}</strong></div><span>{completed ? 'Completed' : 'Review required'}</span></header>
      <p>{proposal.summary}</p>
      <div className="assistant-tool-proposal-fields">
        {proposal.action_id === 'calendar.create_event' ? <label>Title<input aria-label="Calendar event title" value={stringValue(input.title)} onChange={(event) => update('title', event.currentTarget.value)} /></label> : null}
        <label>Start<input aria-label="Calendar event start" type="datetime-local" value={datetimeLocalValue(input.start_time ?? input.start)} onChange={(event) => update('start_time', event.currentTarget.value)} /></label>
        <label>End<input aria-label="Calendar event end" type="datetime-local" value={datetimeLocalValue(input.end_time ?? input.end)} onChange={(event) => update('end_time', event.currentTarget.value)} /></label>
        <label>Timezone<input aria-label="Calendar event timezone" value={stringValue(input.timezone)} placeholder="America/Vancouver" onChange={(event) => update('timezone', event.currentTarget.value)} /></label>
        {proposal.action_id === 'calendar.create_event' ? <label>Attendees<input aria-label="Calendar event attendees" value={attendeeValue(input.attendees)} placeholder="name@example.com" onChange={(event) => update('attendees', event.currentTarget.value.split(',').map((value) => value.trim()).filter(Boolean))} /></label> : null}
        {proposal.action_id === 'calendar.create_event' ? <label>Reminder minutes<input aria-label="Calendar reminder minutes" min="0" max="40320" type="number" value={stringValue(input.reminder_minutes)} onChange={(event) => update('reminder_minutes', event.currentTarget.value === '' ? null : Number(event.currentTarget.value))} /></label> : null}
      </div>
      {proposal.connection_required ? <p role="status">Google Calendar must be connected before this proposal can run.</p> : null}
      {missing.length ? <p role="status">Complete: {missing.join(', ')}.</p> : null}
      {status ? <p role={completed ? 'status' : 'alert'}>{status}</p> : null}
      <footer>
        {proposal.connection_required ? <button type="button" onClick={onOpenTools}>Configure Google Calendar</button> : null}
        <button type="button" disabled={!canExecute} onClick={() => void execute()}>{pending ? 'Running…' : proposal.action_id === 'calendar.create_event' ? 'Approve and create' : 'Run availability check'}</button>
      </footer>
    </section>
  );
}

export function liveAgentToolProposals(metadata: Record<string, unknown> | undefined): LiveAgentToolProposal[] {
  const value = metadata?.assistant_tool_proposals;
  return Array.isArray(value) ? value.filter(isLiveAgentToolProposal) : [];
}

function isLiveAgentToolProposal(value: unknown): value is LiveAgentToolProposal {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return typeof record.proposal_id === 'string' && typeof record.tool_id === 'string' && typeof record.action_id === 'string' && record.executes === false;
}

function requiredCalendarFields(actionId: string, input: Record<string, unknown>): string[] {
  const fields = actionId === 'calendar.create_event' ? ['title', 'start_time', 'end_time', 'timezone'] : ['start_time', 'end_time', 'timezone'];
  return fields.filter((field) => !stringValue(input[field] ?? input[field.replace('_time', '')]).trim());
}

function stringValue(value: unknown): string { return value == null ? '' : String(value); }
function attendeeValue(value: unknown): string { return Array.isArray(value) ? value.join(', ') : stringValue(value); }
function datetimeLocalValue(value: unknown): string { return stringValue(value).slice(0, 16); }
