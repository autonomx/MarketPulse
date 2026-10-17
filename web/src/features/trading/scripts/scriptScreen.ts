/**
 * The script screener's filters (TVP-11.6): conditions on a script's outputs at the last bar, checked on the values
 * the screen returned, so changing a filter needs no new run.
 */
import type { ScriptScreenRow } from './scriptsApi';

export type ScreenOperator = 'above' | 'below' | 'crosses-above' | 'crosses-below' | 'true';
export type ScreenFilter = { id: string; output: string; operator: ScreenOperator; value: number };

export const SCREEN_OPERATORS: ReadonlyArray<{ id: ScreenOperator; label: string; needsValue: boolean }> = [
  { id: 'above', label: 'is above', needsValue: true },
  { id: 'below', label: 'is below', needsValue: true },
  { id: 'crosses-above', label: 'crosses above', needsValue: true },
  { id: 'crosses-below', label: 'crosses below', needsValue: true },
  { id: 'true', label: 'is true (fires)', needsValue: false },
];

/** Whether one filter holds on a row (crossing: the bar before was on the other side, as Pine's crossover). */
export function filterHolds(row: ScriptScreenRow, filter: ScreenFilter): boolean {
  const last = row.last?.[filter.output];
  const previous = row.previous?.[filter.output];
  if (last === null || last === undefined) return false;
  switch (filter.operator) {
    case 'above': return last > filter.value;
    case 'below': return last < filter.value;
    case 'crosses-above': return previous !== null && previous !== undefined && previous < filter.value && last > filter.value;
    case 'crosses-below': return previous !== null && previous !== undefined && previous > filter.value && last < filter.value;
    case 'true': return last !== 0;
  }
}

/** Rows that ran and meet every filter. */
export function screenMatches(rows: readonly ScriptScreenRow[], filters: readonly ScreenFilter[]): ScriptScreenRow[] {
  return rows.filter((row) => !row.error && filters.every((filter) => filterHolds(row, filter)));
}
