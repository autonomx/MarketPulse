import type { HermesRpgApprovedFlowResponse } from './api/hermesRpgApprovedFlowClient';

interface RpgHermesExecutionResultProps {
  result?: HermesRpgApprovedFlowResponse | null;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function listValue(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())) : [];
}

export function RpgHermesExecutionResult({ result }: RpgHermesExecutionResultProps) {
  if (!result) return null;
  const readout = recordValue(result.readout);
  const flow = recordValue(result.flow);
  const flowResult = recordValue(flow.result);
  const rpgResult = recordValue(flowResult.rpg_result);
  const ledgerEntry = recordValue(result.ledger_entry);
  const command = stringValue(readout.command_text) ?? stringValue(ledgerEntry.command_text) ?? 'No command recorded';
  const turn = rpgResult.turn ?? readout.turn ?? ledgerEntry.turn ?? 'not reported';
  const narration = stringValue(rpgResult.narration) ?? stringValue(rpgResult.summary) ?? stringValue(rpgResult.response) ?? stringValue(result.error) ?? 'No result text reported.';
  const systems = listValue(readout.systems).concat(listValue(rpgResult.affected_systems));
  const status = result.ok ? 'success' : result.error ? 'failure' : 'blocked';

  return (
    <section className="rpg-card" aria-label="Hermes execution result">
      <div className="rpg-section-heading">
        <p className="eyebrow">Hermes result</p>
        <span>{status}</span>
      </div>
      <div className="rpg-resource-grid">
        <div>
          <span>Command</span>
          <strong>{command}</strong>
        </div>
        <div>
          <span>Turn</span>
          <strong>{String(turn)}</strong>
        </div>
        <div>
          <span>State</span>
          <strong>{result.state_changed ? 'changed' : 'unchanged'}</strong>
        </div>
        <div>
          <span>Ledger</span>
          <strong>{stringValue(ledgerEntry.execution_id) ?? 'pending'}</strong>
        </div>
      </div>
      <p className="rpg-scene-copy">{narration}</p>
      <small>{systems.length ? systems.join(', ') : 'Affected systems not reported.'}</small>
    </section>
  );
}
