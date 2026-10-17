/** The dialog's condition rows (TVP-1.6): source, operator and its value or line, channel or move, each removable. */
import type { AlertIndicatorChoice } from './alertIndicatorSources';
import { AlertLineField } from './AlertLineField';
import {
  OPERATOR_OPTIONS,
  choiceMatchesDraft,
  SOURCE_OPTIONS,
  indicatorDraftPatch,
  operatorTakes,
  type ConditionDraft,
  type ConditionSourceKind,
} from './alertConditionDrafts';

/** The indicator select's value for a stored indicator the chart doesn't offer as is. */
const STORED = '__stored__';

function ConditionRow({
  draft,
  number,
  choices,
  onChange,
  onRemove,
}: {
  draft: ConditionDraft;
  number: number;
  choices: readonly AlertIndicatorChoice[];
  onChange: (draft: ConditionDraft) => void;
  onRemove?: () => void;
}) {
  const takes = operatorTakes(draft.operator);
  const available = choices.filter((choice) => !choice.unavailable);
  // The chart's indicator only when it is the stored one (same inputs, draws the line); else the stored one shows as is.
  const choice = available.find((item) => choiceMatchesDraft(item, draft));
  const update = (patch: Partial<ConditionDraft>) => onChange({ ...draft, ...patch });
  const setSource = (source: ConditionSourceKind) => {
    if (source !== 'indicator') return update({ source });
    const first = available[0];
    return update({ source, ...(first ? indicatorDraftPatch(first) : {}) });
  };
  return (
    <fieldset className="trading-alert-condition-extra" aria-label={`Condition ${number}`}>
      <div className="trading-alert-condition-row">
        <select aria-label={`Condition ${number} source`} value={draft.source} onChange={(event) => setSource(event.target.value as ConditionSourceKind)}>
          {SOURCE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value} disabled={option.value === 'indicator' && available.length === 0 && draft.source !== 'indicator'}>{option.label}</option>
          ))}
        </select>
        <select aria-label={`Condition ${number} operator`} value={draft.operator} onChange={(event) => update({ operator: event.target.value as ConditionDraft['operator'] })}>
          {OPERATOR_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        {onRemove ? <button type="button" className="trading-alert-condition-remove" aria-label={`Remove condition ${number}`} onClick={onRemove}>×</button> : null}
      </div>
      {draft.source === 'change' ? (
        <label className="trading-alert-inline-field">Bars back<input aria-label={`Condition ${number} bars back`} inputMode="numeric" value={draft.lookback} onChange={(event) => update({ lookback: event.target.value })} /></label>
      ) : null}
      {draft.source === 'indicator' ? (
        <div className="trading-alert-condition-row">
          <select aria-label={`Condition ${number} indicator`} value={choice ? choice.key : STORED} onChange={(event) => {
            const picked = available.find((item) => item.key === event.target.value);
            if (picked) update(indicatorDraftPatch(picked));
          }}>
            {choice ? null : <option value={STORED}>{draft.indicatorId ? `${draft.indicatorId} (as saved)` : 'Choose'}</option>}
            {available.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
          <select aria-label={`Condition ${number} line`} value={draft.output ?? ''} onChange={(event) => (choice ? update(indicatorDraftPatch(choice, event.target.value)) : update({ output: event.target.value }))}>
            {choice ? null : <option value={draft.output ?? ''}>{draft.output ?? 'Line'}</option>}
            {(choice?.outputs ?? []).map((output) => <option key={output.key} value={output.key}>{output.title}</option>)}
          </select>
        </div>
      ) : null}
      {takes === 'value' ? (
        <div className="trading-alert-value-row">
          <span>Target</span>
          <AlertLineField label={`Condition ${number} value`} value={draft.value} line={draft.targetLine} choices={choices} onValue={(value) => update({ value })} onLine={(targetLine) => update({ targetLine })} />
        </div>
      ) : takes === 'channel' ? (
        <>
          <div className="trading-alert-value-row">
            <span>Upper</span>
            <AlertLineField label={`Condition ${number} upper`} placeholder="Upper" value={draft.upper} line={draft.upperLine} choices={choices} onValue={(upper) => update({ upper })} onLine={(upperLine) => update({ upperLine })} />
          </div>
          <div className="trading-alert-value-row">
            <span>Lower</span>
            <AlertLineField label={`Condition ${number} lower`} placeholder="Lower" value={draft.lower} line={draft.lowerLine} choices={choices} onValue={(lower) => update({ lower })} onLine={(lowerLine) => update({ lowerLine })} />
          </div>
        </>
      ) : (
        <div className="trading-alert-value-row">
          <span>By</span>
          <input aria-label={`Condition ${number} amount`} inputMode="decimal" placeholder="Amount" value={draft.amount} onChange={(event) => update({ amount: event.target.value })} />
          <span>in bars</span>
          <input aria-label={`Condition ${number} bars`} inputMode="numeric" value={draft.bars} onChange={(event) => update({ bars: event.target.value })} />
        </div>
      )}
    </fieldset>
  );
}

/** Condition rows numbered from `firstNumber` (the dialog's own first condition is 1 for a legacy alert). */
export function AlertConditionRows({
  drafts,
  firstNumber,
  choices,
  minimum,
  onChange,
}: {
  drafts: readonly ConditionDraft[];
  firstNumber: number;
  choices: readonly AlertIndicatorChoice[];
  /** Rows that can't be removed (a conditions alert keeps at least one). */
  minimum: number;
  onChange: (drafts: ConditionDraft[]) => void;
}) {
  return (
    <>
      {drafts.map((draft, index) => (
        <ConditionRow
          key={index}
          draft={draft}
          number={firstNumber + index}
          choices={choices}
          onChange={(next) => onChange(drafts.map((item, at) => (at === index ? next : item)))}
          onRemove={drafts.length > minimum ? () => onChange(drafts.filter((_, at) => at !== index)) : undefined}
        />
      ))}
    </>
  );
}
