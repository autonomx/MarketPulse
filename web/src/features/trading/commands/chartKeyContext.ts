/**
 * Whether the chart area has the keyboard.
 *
 * Plain chart keys (letters, digits, arrows, `/`, `.`, Delete) would take
 * keys from other parts of the page, so they fire only while the chart area
 * has the keyboard:
 * - `chart`: nothing else has focus (or focus is inside the chart area), and
 *   the last click was not outside the chart area. This holds when the page
 *   opens, so typing a symbol works straight away.
 * - `chartClicked`: the last click was in the chart area and focus is on the
 *   page or the chart area itself. Tab needs this stricter rule so keyboard
 *   users still move focus with it; Escape forgets the click.
 *
 * Clicks inside a modal dialog (symbol search, palette) belong to the dialog
 * and leave the context alone; closing symbol search returns the keyboard to
 * the chart.
 */
export type ChartKeyContext = 'chart' | 'chartClicked';

/** The chart grid's container in the trading workspace. It is focusable (tabindex 0). */
export const CHART_AREA_SELECTOR = '.trading-chart-shell';

let lastPointer: 'none' | 'chart' | 'outside' = 'none';

export function noteTradingPointerDown(target: EventTarget | null): void {
  const owner = target instanceof Node ? target.ownerDocument : null;
  if (owner?.querySelector('[role="dialog"][aria-modal="true"]')) return;
  lastPointer = target instanceof Element && target.closest(CHART_AREA_SELECTOR) ? 'chart' : 'outside';
}

/** Escape: Tab moves focus again instead of switching charts. */
export function forgetChartClick(): void {
  if (lastPointer === 'chart') lastPointer = 'none';
}

/** After a chart action in another surface (symbol search), chart keys work again without a click. */
export function returnKeyboardToChart(): void {
  if (lastPointer === 'outside') lastPointer = 'none';
}

/** For tests and when the workspace unmounts. */
export function resetTradingPointerContext(): void {
  lastPointer = 'none';
}

export function chartKeyContextActive(context: ChartKeyContext, event?: Event): boolean {
  const target = event?.target instanceof Node ? event.target : null;
  const owner = target?.ownerDocument ?? (typeof document === 'undefined' ? null : document);
  const focused = owner?.activeElement ?? null;
  const nothingFocused = focused === null || focused === owner?.body;
  if (context === 'chartClicked') return lastPointer === 'chart' && (nothingFocused || focused?.matches(CHART_AREA_SELECTOR) === true);
  return lastPointer !== 'outside' && (nothingFocused || focused?.closest(CHART_AREA_SELECTOR) != null);
}
