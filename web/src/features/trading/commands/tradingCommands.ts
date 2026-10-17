import { chartKeyContextActive, type ChartKeyContext } from './chartKeyContext';
import { isBrowserReservedHotkey, isPlainHotkey, matchesHotkey, normalizeHotkey, parseHotkey } from './hotkeys';

export { matchesHotkey, normalizeHotkey } from './hotkeys';

/** Where a command applies. When one key matches in several scopes, the most specific scope wins. */
export type CommandScope = 'workspace' | 'chart' | 'watchlist' | 'drawing';

const SCOPE_PRIORITY: Record<CommandScope, number> = { workspace: 0, chart: 1, watchlist: 2, drawing: 3 };

/**
 * Where Omnix runs. Browsers keep some keys for themselves (Ctrl+T, Ctrl+W,
 * Ctrl+N, Ctrl+Tab, Ctrl+PgUp/PgDn, Ctrl+1-9) and a page can't override them,
 * so those TradingView keys only work in the installed app (TVP-4.5). The
 * browser is the default.
 */
export type TradingCommandAvailability = 'browser' | 'installed';

/** Keys matched by a pattern rather than a hotkey string. Pattern keys can't be rebound. */
export type CommandKeyPattern = 'letter' | 'interval';

export type TradingCommandDefinition = {
  id: string;
  label: string;
  group: string;
  scope: CommandScope;
  /** Hotkey strings (see `hotkeys.ts`), e.g. `mod+z`, `alt+t`, `shift+alt+b`. `mod` is Ctrl on Windows/Linux and ⌘ on macOS. */
  defaultKeys: readonly string[];
  /** TradingView's keys that browsers keep for themselves; they only work in the installed app, alongside `defaultKeys`. */
  installedKeys?: readonly string[];
  /** Runs even while a text field has focus (for keys such as Escape). */
  allowInInputs?: boolean;
  /** Matches typed characters instead of hotkeys: `letter` is A–Z, `interval` is 0–9 and comma. */
  keyPattern?: CommandKeyPattern;
  /** The keys only fire while the chart area has the keyboard (see `chartKeyContext`). Keys without Ctrl, ⌘ or Alt always need it. */
  keyContext?: ChartKeyContext;
  /** Keys a component's own keyboard pattern reads (the watchlist tree grid). Listed in the shortcut dialog; never dispatched or rebound. */
  handledLocally?: boolean;
  /** Fires again while the key is held (moving and zooming the chart); other commands run once per press. */
  repeatable?: boolean;
  /**
   * Commands this one deliberately takes a key from while it is active, as TradingView does: the arrow keys move a
   * selected drawing instead of the chart; Shift+B/S trade instead of typing a symbol while the paper ticket is open.
   * Not reported as a clash for the default keys.
   */
  shadows?: readonly string[];
  /** Catalogued ahead of its action (it arrives with another work package): listed as not available yet, never dispatched or rebound. */
  planned?: boolean;
};

export type TradingCommandHandler = {
  /** The key event, or nothing when the command palette runs the command. */
  run: (event?: KeyboardEvent) => void;
  isActive: () => boolean;
};

export type KeyOverrides = Readonly<Record<string, readonly string[]>>;

export const TRADING_COMMANDS = [
  // Chart (TVP-2.1)
  { id: 'chart.symbolSearch', label: 'Change symbol (type a letter)', group: 'Chart', scope: 'chart', defaultKeys: [], keyPattern: 'letter', keyContext: 'chart' },
  { id: 'chart.intervalInput', label: 'Change interval (type a number or comma)', group: 'Chart', scope: 'chart', defaultKeys: [], keyPattern: 'interval', keyContext: 'chart' },
  { id: 'chart.indicators', label: 'Open indicators', group: 'Chart', scope: 'chart', defaultKeys: ['/'], keyContext: 'chart' },
  { id: 'chart.moveLeft', label: 'Move chart one bar left', group: 'Chart', scope: 'chart', defaultKeys: ['arrowleft'], keyContext: 'chart', repeatable: true },
  { id: 'chart.moveRight', label: 'Move chart one bar right', group: 'Chart', scope: 'chart', defaultKeys: ['arrowright'], keyContext: 'chart', repeatable: true },
  { id: 'chart.moveFurtherLeft', label: 'Move chart further left', group: 'Chart', scope: 'chart', defaultKeys: ['mod+arrowleft'], keyContext: 'chart', repeatable: true },
  { id: 'chart.moveFurtherRight', label: 'Move chart further right', group: 'Chart', scope: 'chart', defaultKeys: ['mod+arrowright'], keyContext: 'chart', repeatable: true },
  { id: 'chart.zoomIn', label: 'Zoom in', group: 'Chart', scope: 'chart', defaultKeys: ['mod+arrowup'], keyContext: 'chart', repeatable: true },
  { id: 'chart.zoomOut', label: 'Zoom out', group: 'Chart', scope: 'chart', defaultKeys: ['mod+arrowdown'], keyContext: 'chart', repeatable: true },
  { id: 'chart.reset', label: 'Reset chart view', group: 'Chart', scope: 'chart', defaultKeys: ['alt+r'] },
  { id: 'chart.invertScale', label: 'Invert price scale', group: 'Chart', scope: 'chart', defaultKeys: ['alt+i'] },
  { id: 'chart.logScale', label: 'Logarithmic price scale', group: 'Chart', scope: 'chart', defaultKeys: ['alt+l'] },
  { id: 'chart.percentScale', label: 'Percent price scale', group: 'Chart', scope: 'chart', defaultKeys: ['alt+p'] },
  { id: 'chart.snapshot', label: 'Chart snapshot: copy its link', group: 'Chart', scope: 'chart', defaultKeys: ['alt+s'] },
  { id: 'chart.snapshotDownload', label: 'Save chart image (download PNG)', group: 'Chart', scope: 'chart', defaultKeys: ['mod+alt+s'] },
  { id: 'chart.addAlert', label: 'Add alert at the last price', group: 'Chart', scope: 'chart', defaultKeys: ['alt+a'] },
  { id: 'chart.goToDate', label: 'Go to date', group: 'Chart', scope: 'chart', defaultKeys: ['alt+g'] },
  // Bar replay (TVP-8.1), TradingView's keys.
  { id: 'replay.playPause', label: 'Replay: play or pause', group: 'Replay', scope: 'chart', defaultKeys: ['shift+arrowdown'], keyContext: 'chart' },
  { id: 'replay.stepForward', label: 'Replay: step forward', group: 'Replay', scope: 'chart', defaultKeys: ['shift+arrowright'], keyContext: 'chart', repeatable: true },
  { id: 'replay.stepBack', label: 'Replay: step back', group: 'Replay', scope: 'chart', defaultKeys: ['shift+arrowleft'], keyContext: 'chart', repeatable: true },
  // Drawings
  { id: 'drawing.undo', label: 'Undo drawing change', group: 'Drawings', scope: 'chart', defaultKeys: ['mod+z'] },
  { id: 'drawing.redo', label: 'Redo drawing change', group: 'Drawings', scope: 'chart', defaultKeys: ['mod+shift+z', 'mod+y'] },
  // TVP-7.4: trading hotkeys fill the paper order ticket; the user places the order. They take Shift+B/S from typing.
  { id: 'trading.buyMarket', label: 'Buy at market (fills the order ticket)', group: 'Trading', scope: 'chart', defaultKeys: ['shift+b'], keyContext: 'chart', shadows: ['chart.symbolSearch'] },
  { id: 'trading.sellMarket', label: 'Sell at market (fills the order ticket)', group: 'Trading', scope: 'chart', defaultKeys: ['shift+s'], keyContext: 'chart', shadows: ['chart.symbolSearch'] },
  { id: 'trading.buyLimit', label: 'Buy limit at the crosshair price (fills the order ticket)', group: 'Trading', scope: 'chart', defaultKeys: ['alt+shift+b'], keyContext: 'chart' },
  { id: 'trading.sellLimit', label: 'Sell limit at the crosshair price (fills the order ticket)', group: 'Trading', scope: 'chart', defaultKeys: ['alt+shift+s'], keyContext: 'chart' },
  { id: 'drawing.delete', label: 'Delete selected drawings', group: 'Drawings', scope: 'drawing', defaultKeys: ['delete', 'backspace'], keyContext: 'chart' },
  // TVP-2.2: drawing tools, clipboard, nudge and hide-all.
  { id: 'drawing.trendLine', label: 'Trend line tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+t'] },
  { id: 'drawing.horizontalLine', label: 'Horizontal line tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+h'] },
  { id: 'drawing.verticalLine', label: 'Vertical line tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+v'] },
  { id: 'drawing.crossLine', label: 'Cross line tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+c'] },
  { id: 'drawing.fibRetracement', label: 'Fib retracement tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+f'] },
  { id: 'drawing.rectangle', label: 'Rectangle tool', group: 'Drawings', scope: 'chart', defaultKeys: ['alt+shift+r'] },
  { id: 'drawing.copy', label: 'Copy selected drawings', group: 'Drawings', scope: 'drawing', defaultKeys: ['mod+c'], keyContext: 'chart' },
  { id: 'drawing.paste', label: 'Paste drawings', group: 'Drawings', scope: 'chart', defaultKeys: ['mod+v'], keyContext: 'chart' },
  // TradingView's Ctrl+Alt+H (Ctrl on macOS too, where ⌘⌥H hides other apps); see decision TVP-2.2 (keys).
  { id: 'drawing.hideAll', label: 'Hide or show all drawings', group: 'Drawings', scope: 'chart', defaultKeys: ['ctrl+alt+h'] },
  { id: 'drawing.nudgeLeft', label: 'Move selected drawings one bar left', group: 'Drawings', scope: 'drawing', defaultKeys: ['arrowleft'], keyContext: 'chart', repeatable: true, shadows: ['chart.moveLeft'] },
  { id: 'drawing.nudgeRight', label: 'Move selected drawings one bar right', group: 'Drawings', scope: 'drawing', defaultKeys: ['arrowright'], keyContext: 'chart', repeatable: true, shadows: ['chart.moveRight'] },
  { id: 'drawing.nudgeUp', label: 'Move selected drawings up', group: 'Drawings', scope: 'drawing', defaultKeys: ['arrowup'], keyContext: 'chart', repeatable: true },
  { id: 'drawing.nudgeDown', label: 'Move selected drawings down', group: 'Drawings', scope: 'drawing', defaultKeys: ['arrowdown'], keyContext: 'chart', repeatable: true },
  // General and layouts. Omnix calls a saved TradingView layout a workspace.
  { id: 'workspace.commandPalette', label: 'Open command palette', group: 'General', scope: 'workspace', defaultKeys: ['mod+k'] },
  { id: 'workspace.shortcuts', label: 'Keyboard shortcuts', group: 'General', scope: 'workspace', defaultKeys: ['mod+/'] },
  { id: 'layout.save', label: 'Save layout (workspace)', group: 'Layout', scope: 'workspace', defaultKeys: ['mod+s'], allowInInputs: true },
  { id: 'layout.load', label: 'Load layout (workspace)', group: 'Layout', scope: 'workspace', defaultKeys: ['.'], keyContext: 'chart' },
  // Layout and watchlist (TVP-2.3)
  { id: 'layout.nextChart', label: 'Next chart in the layout', group: 'Layout', scope: 'chart', defaultKeys: ['tab'], keyContext: 'chartClicked' },
  { id: 'layout.previousChart', label: 'Previous chart in the layout', group: 'Layout', scope: 'chart', defaultKeys: ['shift+tab'], keyContext: 'chartClicked' },
  { id: 'layout.maximizeChart', label: 'Maximise or restore the active chart', group: 'Layout', scope: 'workspace', defaultKeys: ['alt+enter'] },
  { id: 'watchlist.addActiveSymbol', label: 'Add the chart symbol to the open watchlist', group: 'Watchlist', scope: 'workspace', defaultKeys: ['alt+w'] },
  // Tabs (TVP-2.3). In the browser they use Alt, because the browser keeps Ctrl+T, W, N, Tab, PgUp/PgDn and 1-9.
  { id: 'tab.new', label: 'New tab, copying this one (browser Alt+Shift+T, app Ctrl+T)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+shift+t'], installedKeys: ['mod+t', 'mod+u'] },
  { id: 'tab.close', label: 'Close tab (browser Alt+Shift+W, app Ctrl+W)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+shift+w'], installedKeys: ['mod+w'] },
  { id: 'tab.next', label: 'Next tab (browser Alt+Page Down, app Ctrl+Tab)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+pagedown'], installedKeys: ['mod+tab', 'mod+pagedown'] },
  { id: 'tab.previous', label: 'Previous tab (browser Alt+Page Up, app Ctrl+Shift+Tab)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+pageup'], installedKeys: ['mod+shift+tab', 'mod+pageup'] },
  { id: 'tab.goTo1', label: 'Go to tab 1 (browser Alt+1, app Ctrl+1)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+1'], installedKeys: ['mod+1'] },
  { id: 'tab.goTo2', label: 'Go to tab 2 (browser Alt+2, app Ctrl+2)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+2'], installedKeys: ['mod+2'] },
  { id: 'tab.goTo3', label: 'Go to tab 3 (browser Alt+3, app Ctrl+3)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+3'], installedKeys: ['mod+3'] },
  { id: 'tab.goTo4', label: 'Go to tab 4 (browser Alt+4, app Ctrl+4)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+4'], installedKeys: ['mod+4'] },
  { id: 'tab.goTo5', label: 'Go to tab 5 (browser Alt+5, app Ctrl+5)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+5'], installedKeys: ['mod+5'] },
  { id: 'tab.goTo6', label: 'Go to tab 6 (browser Alt+6, app Ctrl+6)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+6'], installedKeys: ['mod+6'] },
  { id: 'tab.goTo7', label: 'Go to tab 7 (browser Alt+7, app Ctrl+7)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+7'], installedKeys: ['mod+7'] },
  { id: 'tab.goTo8', label: 'Go to tab 8 (browser Alt+8, app Ctrl+8)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+8'], installedKeys: ['mod+8'] },
  { id: 'tab.goToLast', label: 'Go to the last tab (browser Alt+9, app Ctrl+9)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+9'], installedKeys: ['mod+9'] },
  { id: 'tab.reopenClosed', label: 'Reopen closed tab (browser Alt+Shift+Z, app Ctrl+Shift+T)', group: 'Tabs', scope: 'workspace', defaultKeys: ['alt+shift+z'], installedKeys: ['mod+shift+t'] },
  // The watchlist tree grid reads these keys itself (TVP-5.3); they stay fixed so the grid keeps its ARIA keyboard pattern.
  { id: 'watchlist.next', label: 'Next symbol', group: 'Watchlist', scope: 'watchlist', defaultKeys: ['arrowdown', 'space'], handledLocally: true },
  { id: 'watchlist.previous', label: 'Previous symbol', group: 'Watchlist', scope: 'watchlist', defaultKeys: ['arrowup', 'shift+space'], handledLocally: true },
  { id: 'watchlist.extendNext', label: 'Extend the selection down', group: 'Watchlist', scope: 'watchlist', defaultKeys: ['shift+arrowdown'], handledLocally: true },
  { id: 'watchlist.extendPrevious', label: 'Extend the selection up', group: 'Watchlist', scope: 'watchlist', defaultKeys: ['shift+arrowup'], handledLocally: true },
  { id: 'watchlist.selectAll', label: 'Select all symbols', group: 'Watchlist', scope: 'watchlist', defaultKeys: ['mod+a'], handledLocally: true },
] as const satisfies readonly TradingCommandDefinition[];

export type TradingCommandId = typeof TRADING_COMMANDS[number]['id'];

const definitionsById = new Map<string, TradingCommandDefinition>(TRADING_COMMANDS.map((command) => [command.id, command]));

export function tradingCommandDefinition(id: string): TradingCommandDefinition | undefined {
  return definitionsById.get(id);
}

/** Pattern keys, the watchlist grid keys and planned commands are fixed; every other command can be rebound. */
export function isRebindable(definition: TradingCommandDefinition): boolean {
  return !definition.keyPattern && !definition.handledLocally && !definition.planned;
}

/**
 * A command's keys: the user's keys (or the defaults) plus, in the installed
 * app, TradingView's browser-reserved keys. Rebinding replaces the browser
 * keys only, so the installed app keeps TradingView's keys.
 */
export function commandKeys(
  definition: TradingCommandDefinition,
  overrides: KeyOverrides = {},
  availability: TradingCommandAvailability = 'browser',
): readonly string[] {
  const override = isRebindable(definition) ? overrides[definition.id] : undefined;
  // A key rebound in the installed app may be one the browser keeps; it can't fire here, so fall back to the defaults.
  const usable = availability === 'browser' ? override?.filter((key) => !isBrowserReservedHotkey(key)) : override;
  const keys = usable && usable.length > 0 ? usable : definition.defaultKeys;
  return availability === 'installed' && definition.installedKeys ? [...keys, ...definition.installedKeys] : keys;
}

/** Typed-character matching for pattern commands: no Ctrl, ⌘ or Alt. Shift is allowed (capital letters; the AZERTY digit row). */
export function matchesKeyPattern(pattern: CommandKeyPattern, event: KeyboardEvent): boolean {
  if (event.ctrlKey || event.metaKey || event.altKey) return false;
  return pattern === 'letter' ? /^[a-z]$/i.test(event.key) : /^[0-9,]$/.test(event.key);
}

export function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]') !== null;
}

const COMPOSITE_WIDGETS = [
  'listbox', 'menu', 'menubar', 'grid', 'treegrid', 'tree', 'tablist', 'radiogroup', 'slider', 'spinbutton', 'combobox',
].map((role) => `[role="${role}"]`).join(', ');

/** Widgets that use plain keys (arrows, letters, Tab) for their own navigation. */
export function isCompositeWidgetTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(COMPOSITE_WIDGETS) !== null;
}

export const MODAL_DIALOG_SELECTOR = '[role="dialog"][aria-modal="true"]';

function modalDialogOpen(event: KeyboardEvent): boolean {
  const target = event.target instanceof Node ? event.target : null;
  const owner = target?.ownerDocument ?? (typeof document === 'undefined' ? null : document);
  return owner?.querySelector(MODAL_DIALOG_SELECTOR) != null;
}

export type RegisteredCommand = { definition: TradingCommandDefinition; handler: TradingCommandHandler };

/**
 * The command a key event triggers, or null. Text fields and open modal
 * dialogs are skipped unless a command allows inputs, and plain keys are
 * skipped in widgets that navigate with them. A key without Ctrl, ⌘ or Alt
 * only fires while the chart area has the keyboard. Inactive handlers are
 * skipped. A hotkey beats a typed-character pattern; then the most specific
 * scope wins, then the latest registration.
 */
const CLAIMED_BROWSER_KEYS = [...new Set(TRADING_COMMANDS.flatMap((command) => ('installedKeys' in command ? command.installedKeys : [])))]
  .filter((key) => isBrowserReservedHotkey(key));

/** Whether the key is one of the browser's that the installed app claims (Ctrl+T/W/Tab/Shift+T...). */
export function claimsBrowserKey(event: KeyboardEvent): boolean {
  return CLAIMED_BROWSER_KEYS.some((key) => matchesHotkey(key, event));
}

export function resolveCommand(
  registered: readonly RegisteredCommand[],
  event: KeyboardEvent,
  overrides: KeyOverrides = {},
  availability: TradingCommandAvailability = 'browser',
): RegisteredCommand | null {
  const blocked = isEditableTarget(event.target) || modalDialogOpen(event)
    || (!event.ctrlKey && !event.metaKey && !event.altKey && isCompositeWidgetTarget(event.target));
  let best: RegisteredCommand | null = null;
  let bestIsPattern = true;
  for (const candidate of registered) {
    const { definition, handler } = candidate;
    if (definition.handledLocally || definition.planned || (blocked && !definition.allowInInputs)) continue;
    const pattern = definition.keyPattern ? matchesKeyPattern(definition.keyPattern, event) : false;
    const hotkey = pattern ? null : commandKeys(definition, overrides, availability).find((key) => matchesHotkey(key, event));
    if (!pattern && !hotkey) continue;
    const context = definition.keyContext ?? (hotkey && !definition.allowInInputs && isPlainHotkey(hotkey) ? 'chart' : undefined);
    if (context && !chartKeyContextActive(context, event)) continue;
    if (!handler.isActive()) continue;
    const better = !best
      || (bestIsPattern && !pattern)
      || (bestIsPattern === pattern && SCOPE_PRIORITY[definition.scope] >= SCOPE_PRIORITY[best.definition.scope]);
    if (better) {
      best = candidate;
      bestIsPattern = pattern;
    }
  }
  return best;
}

const FOCUS_KEYS = new Set(['enter', 'space', 'tab', 'escape']);

/**
 * Why a key can't be bound to a command, or null when it can. Enter, Space,
 * Tab and Escape work buttons, focus and dialogs, so they need Ctrl, ⌘ or
 * Alt. Other keys without a modifier are typing keys and are only allowed
 * for commands that fire while the chart has the keyboard. Keys the browser
 * keeps are refused in the browser, so the command keeps working.
 */
export function rebindProblem(
  definition: TradingCommandDefinition,
  hotkey: string,
  availability: TradingCommandAvailability = 'browser',
): string | null {
  if (!isRebindable(definition)) return `${definition.label} can't be rebound.`;
  const plain = isPlainHotkey(hotkey);
  const parsed = parseHotkey(hotkey);
  const { key } = parsed;
  if (!key) return 'Press a key.';
  // A command's own default (reviewed with the catalogue: Tab between charts, TradingView's Ctrl+Alt+H) can always be restored.
  const isDefault = definition.defaultKeys.some((key) => normalizeHotkey(key) === normalizeHotkey(hotkey));
  if (isDefault) return null;
  if (plain && FOCUS_KEYS.has(key)) return 'Enter, Space, Tab and Escape need Ctrl, Alt or ⌘: on their own they work buttons, focus and dialogs.';
  if (plain && !definition.keyContext) return 'Keys without Ctrl, Alt or ⌘ are only for commands that act on the chart. Add a modifier.';
  if ((parsed.mod || parsed.ctrl) && parsed.alt) return 'Ctrl+Alt is AltGr on many keyboards, which types characters such as @ or {. Use Ctrl or Alt, not both.';
  if (availability === 'browser' && isBrowserReservedHotkey(hotkey)) return 'The browser keeps this key for itself, so it only works in the installed app. The current keys stay.';
  return null;
}

export type KeyConflict = { hotkey: string; scope: CommandScope; ids: string[] };

/** Commands that share a key in the same scope; the shortcut dialog shows these and a test keeps the defaults free of them. */
export function findKeyConflicts(
  definitions: readonly TradingCommandDefinition[],
  overrides: KeyOverrides = {},
  availability: TradingCommandAvailability = 'browser',
): KeyConflict[] {
  const byKey = new Map<string, KeyConflict>();
  for (const definition of definitions) {
    for (const hotkey of commandKeys(definition, overrides, availability)) {
      const normalized = normalizeHotkey(hotkey);
      const slot = `${definition.scope}|${normalized}`;
      const entry = byKey.get(slot) ?? { hotkey: normalized, scope: definition.scope, ids: [] };
      if (!entry.ids.includes(definition.id)) entry.ids.push(definition.id);
      byKey.set(slot, entry);
    }
  }
  return [...byKey.values()].filter((entry) => entry.ids.length > 1);
}

/**
 * - `conflict`: same key, same scope (`findKeyConflicts`);
 * - `shadowed`: same key in different scopes, so the more specific scope wins while it is active;
 * - `typing`: a plain letter, digit or comma that typing on the chart already uses (symbol search, interval box).
 */
export type KeyClash = { hotkey: string; ids: string[]; kind: 'conflict' | 'shadowed' | 'typing' };

/** Every way two shortcuts can get in each other's way; the shortcut dialog lists them all. */
export function findKeyClashes(
  definitions: readonly TradingCommandDefinition[],
  overrides: KeyOverrides = {},
  availability: TradingCommandAvailability = 'browser',
): KeyClash[] {
  const clashes: KeyClash[] = findKeyConflicts(definitions, overrides, availability)
    .map(({ hotkey, ids }) => ({ hotkey, ids, kind: 'conflict' }));
  const users = new Map<string, TradingCommandDefinition[]>();
  const patterns = new Map(definitions.filter((definition) => definition.keyPattern).map((definition) => [definition.keyPattern, definition.id]));
  for (const definition of definitions) {
    if (definition.handledLocally || definition.keyPattern) continue;
    for (const hotkey of commandKeys(definition, overrides, availability)) {
      const normalized = normalizeHotkey(hotkey);
      users.set(normalized, [...(users.get(normalized) ?? []), definition]);
      const parsed = parseHotkey(normalized);
      const typed = !isPlainHotkey(normalized) ? null
        : /^[a-z]$/.test(parsed.key) ? patterns.get('letter')
          : /^[0-9,]$/.test(parsed.key) ? patterns.get('interval') : null;
      const intended = definition.shadows?.includes(typed ?? '') && definition.defaultKeys.some((key) => normalizeHotkey(key) === normalized);
      if (typed && !intended) clashes.push({ hotkey: normalized, ids: [definition.id, typed], kind: 'typing' });
    }
  }
  const intended = (left: TradingCommandDefinition, right: TradingCommandDefinition) => (
    left.scope === right.scope || Boolean(left.shadows?.includes(right.id) || right.shadows?.includes(left.id))
  );
  for (const [hotkey, sharing] of users) {
    const unintended = sharing.some((left) => sharing.some((right) => !intended(left, right)));
    if (new Set(sharing.map((definition) => definition.scope)).size > 1 && unintended) {
      clashes.push({ hotkey, ids: [...new Set(sharing.map((definition) => definition.id))], kind: 'shadowed' });
    }
  }
  return clashes;
}
