import type { HermesRpgSequenceResponse } from './api/hermesRpgSequenceClient';
import type { HermesSequencePreviewItem, HermesSequencePreviewState } from './HermesSequencePreview';

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map((item) => item.trim()) : [];
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function itemValue(value: unknown, gateDecisions: Record<string, Record<string, unknown>>): HermesSequencePreviewItem {
  const item = recordValue(value);
  const itemId = stringValue(item.item_id);
  const gate = itemId ? gateDecisions[itemId] ?? {} : {};
  return {
    item_id: itemId,
    statement: stringValue(item.statement),
    expected_effect: stringValue(item.expected_effect),
    guards: stringList(item.guards),
    user_gate: booleanValue(item.user_gate),
    status: stringValue(item.status),
    gate_reason: stringValue(gate.reason),
    gate_allowed: booleanValue(gate.allowed),
  };
}

export function hermesSequencePreviewModel(response: HermesRpgSequenceResponse | null | undefined): HermesSequencePreviewState | null {
  const sequence = recordValue(response?.sequence);
  if (!response || Object.keys(sequence).length === 0) return null;
  const validation = recordValue(response.validation);
  const gate = recordValue(response.gate);
  const checkpoint = recordValue(response.checkpoint);
  const loopGuard = recordValue(response.loop_guard);
  const hasGate = Object.keys(gate).length > 0;
  const validationErrors = stringList(validation.errors);
  const gateDecisions = Array.isArray(gate.decisions)
    ? Object.fromEntries(
      gate.decisions
        .map(recordValue)
        .map((decision) => [stringValue(decision.item_id), decision])
        .filter((entry): entry is [string, Record<string, unknown>] => Boolean(entry[0])),
    )
    : {};
  const items = Array.isArray(sequence.items) ? sequence.items.map((item) => itemValue(item, gateDecisions)) : [];
  const blockedReasons = items.map((item) => item.gate_reason).filter((reason): reason is string => Boolean(reason));
  const checkpointReason = stringValue(checkpoint.reason);
  const loopStopReason = stringValue(loopGuard.stop_reason);
  const gateBlockedCount = numberValue(gate.blocked_count);
  const firstUsableCommand = items.find((item) => item.gate_allowed !== false && item.statement?.trim())?.statement?.trim();
  const reviewStatus = validationErrors.length
    ? 'invalid'
    : items.length === 0
      ? 'empty'
      : response.ok
        ? 'ready'
        : gateBlockedCount > 0 || blockedReasons.length || checkpointReason || loopStopReason
          ? 'blocked'
          : 'invalid';
  return {
    sequence_id: stringValue(sequence.sequence_id),
    objective: stringValue(sequence.objective),
    domain: stringValue(sequence.domain),
    state_owner: stringValue(sequence.state_owner),
    risk: stringValue(sequence.risk),
    user_gate: booleanValue(sequence.user_gate),
    status: stringValue(sequence.status),
    review_status: reviewStatus,
    validation_status: validationErrors.length ? `${validationErrors.length} issue${validationErrors.length === 1 ? '' : 's'}` : validation.ok === true ? 'valid' : 'not checked',
    validation_errors: validationErrors,
    gate_status: gate.allowed === true ? 'ready' : gateBlockedCount > 0 ? `${gateBlockedCount} blocked` : hasGate ? 'blocked' : 'not checked',
    blocked_reason: validationErrors[0] ?? checkpointReason ?? loopStopReason ?? blockedReasons[0],
    first_usable_command: firstUsableCommand,
    items,
  };
}
