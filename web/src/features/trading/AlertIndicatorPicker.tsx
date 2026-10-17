import { AlertLineField } from './AlertLineField';
import { ALERT_INDICATOR_OPERATORS, ALERT_SIGNAL_OPERATORS, operatorForOutput, type AlertIndicatorChoice, type AlertIndicatorOperator, type AlertIndicatorSelection } from './alertIndicatorSources';

/** The chart's indicators, their output lines, the comparison and its target (a value or another line), for an indicator alert (TVP-1.3). */
export function AlertIndicatorPicker({
  choices, selection, onChange,
}: {
  choices: readonly AlertIndicatorChoice[];
  selection: AlertIndicatorSelection | undefined;
  onChange: (selection: AlertIndicatorSelection) => void;
}) {
  const choice = choices.find((item) => item.key === selection?.key);
  const pick = (key: string) => {
    const next = choices.find((item) => item.key === key);
    if (next && !next.unavailable) onChange({ key, output: next.outputs[0].key, operator: operatorForOutput(next, next.outputs[0].key, selection?.operator) });
  };
  return (
    <>
      <div className="trading-alert-condition-row">
        <select aria-label="Alert chart indicator" value={selection?.key ?? ''} onChange={(event) => pick(event.target.value)}>
          {selection ? null : <option value="" disabled>Choose an indicator</option>}
          {choices.map((item) => (
            <option key={item.key} value={item.key} disabled={Boolean(item.unavailable)} title={item.unavailable}>
              {item.label}{item.unavailable ? ` — ${item.unavailable}` : ''}
            </option>
          ))}
        </select>
        <select
          aria-label="Alert indicator line"
          value={selection?.output ?? ''}
          disabled={!choice}
          onChange={(event) => selection && onChange({ ...selection, output: event.target.value, operator: operatorForOutput(choice, event.target.value, selection.operator) })}
        >
          {(choice?.outputs ?? []).map((output) => <option key={output.key} value={output.key}>{output.title}</option>)}
        </select>
      </div>
      <div className="trading-alert-condition-row">
        <select
          aria-label="Alert indicator comparison"
          value={selection?.operator ?? 'crossing'}
          disabled={!selection}
          onChange={(event) => selection && onChange({ ...selection, operator: event.target.value as AlertIndicatorOperator })}
        >
          {(selection?.operator === 'appears' ? ALERT_SIGNAL_OPERATORS : ALERT_INDICATOR_OPERATORS).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        {selection && selection.operator !== 'appears' ? (
          <AlertLineField
            label="Alert indicator"
            value=""
            line={selection.target}
            choices={choices}
            withInput={false}
            onValue={() => undefined}
            onLine={(target) => onChange({ ...selection, target })}
          />
        ) : null}
      </div>
    </>
  );
}
