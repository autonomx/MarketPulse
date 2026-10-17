export type HermesSequencePreviewItem = {
  item_id?: string;
  statement?: string;
  expected_effect?: string;
  guards?: string[];
  user_gate?: boolean;
  status?: string;
  gate_reason?: string;
  gate_allowed?: boolean;
};

export type HermesSequencePreviewState = {
  sequence_id?: string;
  objective?: string;
  domain?: string;
  state_owner?: string;
  risk?: string;
  user_gate?: boolean;
  status?: string;
  review_status?: 'ready' | 'blocked' | 'empty' | 'invalid';
  validation_status?: string;
  validation_errors?: string[];
  gate_status?: string;
  blocked_reason?: string;
  first_usable_command?: string;
  items?: HermesSequencePreviewItem[];
};

interface HermesSequencePreviewProps {
  sequence?: HermesSequencePreviewState | null;
  onApprove?: () => void;
  onReject?: () => void;
  onUseFirstItem?: (statement: string) => void;
}

export function HermesSequencePreview({ sequence, onApprove, onReject, onUseFirstItem }: HermesSequencePreviewProps) {
  const items = sequence?.items ?? [];
  if (!sequence) {
    return <p className="rpg-empty-state">No Hermes sequence is ready for review.</p>;
  }
  const firstCommand = sequence.first_usable_command?.trim() ?? '';
  const blockedReason = sequence.blocked_reason ?? (sequence.review_status === 'empty' ? 'No sequence items were supplied.' : 'No blocking reason reported.');

  return (
    <section className="rpg-card" aria-label="Hermes sequence preview">
      <div className="rpg-section-heading">
        <p className="eyebrow">Hermes sequence</p>
        <span>{sequence.review_status ?? sequence.status ?? 'draft'}</span>
      </div>
      <h3>{sequence.objective || 'Untitled objective'}</h3>
      <div className="rpg-resource-grid">
        <div>
          <span>Items</span>
          <strong>{items.length}</strong>
        </div>
        <div>
          <span>Validation</span>
          <strong>{sequence.validation_status ?? 'not checked'}</strong>
        </div>
        <div>
          <span>Gate</span>
          <strong>{sequence.gate_status ?? 'not checked'}</strong>
        </div>
        <div>
          <span>First command</span>
          <strong>{firstCommand || 'none'}</strong>
        </div>
      </div>
      {sequence.review_status === 'blocked' || sequence.review_status === 'invalid' || sequence.review_status === 'empty' ? (
        <div className="rpg-hermes-sequence-alert" role="status">
          <strong>{sequence.review_status === 'invalid' ? 'Needs review' : 'Blocked'}</strong>
          <span>{blockedReason}</span>
        </div>
      ) : null}
      {sequence.validation_errors?.length ? (
        <ul className="rpg-hermes-sequence-issues" aria-label="Hermes sequence validation issues">
          {sequence.validation_errors.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      <div className="rpg-list-stack">
        {items.length === 0 ? <p className="rpg-empty-state">This reviewed sequence has no items.</p> : null}
        {items.map((item, index) => (
          <article className="rpg-list-row" key={item.item_id ?? `${index}-${item.statement ?? 'item'}`}>
            <span className="rpg-icon-tile" aria-hidden="true">{index + 1}</span>
            <div>
              <strong>{item.statement || 'No statement supplied'}</strong>
              <span>{item.expected_effect || 'Expected effect not provided.'}</span>
            </div>
            <span className="rpg-pill">{item.gate_allowed === false ? item.gate_reason ?? 'blocked' : item.user_gate === false ? 'safe' : 'review'}</span>
          </article>
        ))}
      </div>
      <div role="group" className="rpg-survival-actions" aria-label="Hermes sequence actions">
        <button className="rpg-secondary-button" disabled={!firstCommand || !onUseFirstItem} onClick={() => firstCommand && onUseFirstItem?.(firstCommand)} type="button">
          Use first item
        </button>
        <button className="rpg-secondary-button" disabled={!onApprove} onClick={() => onApprove?.()} type="button">
          Approve sequence
        </button>
        <button className="rpg-secondary-button" disabled={!onReject} onClick={() => onReject?.()} type="button">
          Reject
        </button>
      </div>
      <small>Preview only. The RPG simulation still owns state changes.</small>
    </section>
  );
}
