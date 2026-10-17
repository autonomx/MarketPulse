/**
 * A condition's target or channel bound (TVP-1.3): a value, or another line — a price field or one of the chart's
 * indicator lines — so an alert can be "Close crossing SMA 50" or "RSI entering a channel of two lines".
 */
import type { AlertIndicatorChoice } from './alertIndicatorSources';
import { PRICE_LINE_OPTIONS, choiceLine, choiceMatchesLine, type DraftLine } from './alertConditionDrafts';

const VALUE = 'value';
const STORED = 'stored';

type LineOption = { key: string; label: string; line: DraftLine; group: 'price' | 'indicator'; matches: (line: DraftLine) => boolean };

/** The lines a target can be: the price fields, then every line of the chart indicators the server can evaluate. */
export function lineTargetOptions(choices: readonly AlertIndicatorChoice[]): LineOption[] {
  const price = PRICE_LINE_OPTIONS.map((option): LineOption => ({
    key: `price:${option.value}`, label: option.label, line: { kind: 'price', field: option.value }, group: 'price',
    matches: (line) => line.kind === 'price' && line.field === option.value,
  }));
  // Script plots and signal markers aren't offered as targets: a target is a continuous indicator line.
  const indicators = choices.filter((choice) => !choice.unavailable && !choice.script).flatMap((choice, at) => choice.outputs
    .filter((output) => !output.signal)
    .map((output, index): LineOption => ({
      key: `indicator:${at}:${index}`, label: `${choice.label}: ${output.title}`, line: choiceLine(choice, output.key), group: 'indicator',
      matches: (line) => choiceMatchesLine(choice, line, output.key),
    })));
  return [...price, ...indicators];
}

function selectedKey(options: readonly LineOption[], line: DraftLine | undefined): string {
  if (!line) return VALUE;
  return options.find((option) => option.matches(line))?.key ?? STORED;
}

function storedLabel(line: DraftLine): string {
  return line.kind === 'price' ? line.field.toUpperCase() : `${line.indicatorId}: ${line.output} (as saved)`;
}

export function AlertLineField({
  label,
  value,
  line,
  choices,
  placeholder,
  withInput = true,
  onValue,
  onLine,
}: {
  /** The value input's accessible name; the select is "<label> target". */
  label: string;
  value: string;
  line: DraftLine | undefined;
  choices: readonly AlertIndicatorChoice[];
  placeholder?: string;
  /** False when the caller shows its own value input (the dialog's first condition). */
  withInput?: boolean;
  onValue: (value: string) => void;
  onLine: (line: DraftLine | undefined) => void;
}) {
  const options = lineTargetOptions(choices);
  const selected = selectedKey(options, line);
  const indicators = options.filter((option) => option.group === 'indicator');
  return (
    <>
      <select
        aria-label={`${label} target`}
        value={selected}
        onChange={(event) => {
          if (event.target.value === VALUE) return onLine(undefined);
          const option = options.find((item) => item.key === event.target.value);
          if (option) onLine(option.line);
        }}
      >
        <option value={VALUE}>Value</option>
        <optgroup label="Price">
          {options.filter((option) => option.group === 'price').map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
        </optgroup>
        {indicators.length > 0 ? (
          <optgroup label="Chart indicators">
            {indicators.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </optgroup>
        ) : null}
        {selected === STORED && line ? <option value={STORED}>{storedLabel(line)}</option> : null}
      </select>
      {line || !withInput ? null : <input aria-label={label} inputMode="decimal" placeholder={placeholder} value={value} onChange={(event) => onValue(event.target.value)} />}
    </>
  );
}
