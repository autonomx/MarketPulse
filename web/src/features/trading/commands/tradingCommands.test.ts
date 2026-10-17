import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import {
  TRADING_COMMANDS,
  commandKeys,
  findKeyClashes,
  findKeyConflicts,
  isRebindable,
  matchesHotkey,
  matchesKeyPattern,
  normalizeHotkey,
  rebindProblem,
  resolveCommand,
  tradingCommandDefinition,
  type RegisteredCommand,
  type TradingCommandDefinition,
} from './tradingCommands';
import {
  canRunTradingCommand,
  runTradingCommand,
  setTradingCommandAvailability,
  setTradingCommandKeyOverrides,
  useTradingCommand,
  useTradingCommandDispatcher,
} from './useTradingCommands';
import { chartKeyContextActive, noteTradingPointerDown, resetTradingPointerContext, returnKeyboardToChart } from './chartKeyContext';
import { hotkeyFromEvent } from './hotkeys';
import { formatCommandKeys, formatHotkey } from './hotkeyLabels';
import { watchlistKeyCommand } from '../useTradingWatchlistSelection';

function key(init: KeyboardEventInit, target?: EventTarget): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  if (target) Object.defineProperty(event, 'target', { value: target });
  return event;
}

function command(definition: TradingCommandDefinition, active = true): RegisteredCommand & { run: ReturnType<typeof vi.fn> } {
  const run = vi.fn();
  return { definition, handler: { run, isActive: () => active }, run };
}

const chartUndo = tradingCommandDefinition('drawing.undo')!;

describe('trading command matching', () => {
  it('treats mod as Ctrl or Command and requires the exact modifiers', () => {
    expect(matchesHotkey('mod+z', key({ key: 'z', ctrlKey: true }))).toBe(true);
    expect(matchesHotkey('mod+z', key({ key: 'z', metaKey: true }))).toBe(true);
    expect(matchesHotkey('mod+z', key({ key: 'z', ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(matchesHotkey('mod+z', key({ key: 'z', ctrlKey: true, altKey: true }))).toBe(false);
    expect(matchesHotkey('mod+shift+z', key({ key: 'Z', ctrlKey: true, shiftKey: true }))).toBe(true);
  });

  it('matches Alt shortcuts by the typed letter, or the physical key when Option or the layout changes the character', () => {
    expect(matchesHotkey('alt+t', key({ key: '†', code: 'KeyT', altKey: true }))).toBe(true);
    expect(matchesHotkey('alt+t', key({ key: 't', code: 'KeyY', altKey: true }))).toBe(true);
    expect(matchesHotkey('alt+y', key({ key: 't', code: 'KeyY', altKey: true }))).toBe(false);
  });

  it('follows the layout for Alt letters on AZERTY and Dvorak', () => {
    // AZERTY: the key labelled Z sits where QWERTY has W.
    const azertyZ = key({ key: 'Z', code: 'KeyW', altKey: true, shiftKey: true });
    expect(matchesHotkey('alt+shift+z', azertyZ)).toBe(true);
    expect(matchesHotkey('alt+shift+w', azertyZ)).toBe(false);
    expect(hotkeyFromEvent(key({ key: 'a', code: 'KeyQ', altKey: true }))).toBe('alt+a');
    // Dvorak: P sits where QWERTY has R.
    const dvorakP = key({ key: 'p', code: 'KeyR', altKey: true });
    expect(matchesHotkey('alt+p', dvorakP)).toBe(true);
    expect(matchesHotkey('alt+r', dvorakP)).toBe(false);
    // AZERTY digit row types "&" for 1 without Shift.
    expect(matchesHotkey('alt+1', key({ key: '&', code: 'Digit1', altKey: true }))).toBe(true);
  });

  it('matches Ctrl letters on non-Latin layouts by the physical key', () => {
    const russianCtrlS = key({ key: 'ы', code: 'KeyS', ctrlKey: true });
    expect(matchesHotkey('mod+s', russianCtrlS)).toBe(true);
    expect(hotkeyFromEvent(russianCtrlS)).toBe('mod+s');
    expect(matchesHotkey('mod+z', key({ key: 'я', code: 'KeyZ', ctrlKey: true }))).toBe(true);
  });

  it('normalizes modifier order for conflict detection', () => {
    expect(normalizeHotkey('Shift+Alt+B')).toBe('alt+shift+b');
    expect(normalizeHotkey('alt+shift+b')).toBe('alt+shift+b');
  });
});

describe('trading command resolution', () => {
  it('ignores key presses in text fields unless the command allows it', () => {
    const input = document.createElement('input');
    const undo = command(chartUndo);
    expect(resolveCommand([undo], key({ key: 'z', ctrlKey: true }, input))).toBeNull();
    const escape = command({ id: 'test.escape', label: 'Close', group: 'Test', scope: 'workspace', defaultKeys: ['escape'], allowInInputs: true });
    expect(resolveCommand([escape], key({ key: 'Escape' }, input))).toBe(escape);
  });

  it('skips inactive handlers and prefers the most specific scope', () => {
    const inactive = command(chartUndo, false);
    expect(resolveCommand([inactive], key({ key: 'z', ctrlKey: true }))).toBeNull();
    const workspace = command({ id: 'test.a', label: 'A', group: 'Test', scope: 'workspace', defaultKeys: ['delete'] });
    const drawing = command(tradingCommandDefinition('drawing.delete')!);
    expect(resolveCommand([drawing, workspace], key({ key: 'Delete' }))).toBe(drawing);
  });

  it('uses key overrides instead of the defaults', () => {
    const undo = command(chartUndo);
    const overrides = { 'drawing.undo': ['alt+u'] };
    expect(resolveCommand([undo], key({ key: 'z', ctrlKey: true }), overrides)).toBeNull();
    expect(resolveCommand([undo], key({ key: 'u', code: 'KeyU', altKey: true }), overrides)).toBe(undo);
  });

  it('has no conflicting default keys', () => {
    expect(findKeyConflicts(TRADING_COMMANDS)).toEqual([]);
    const conflicting = findKeyConflicts(TRADING_COMMANDS, { 'drawing.redo': ['mod+z'] });
    expect(conflicting).toEqual([{ hotkey: 'mod+z', scope: 'chart', ids: ['drawing.undo', 'drawing.redo'] }]);
  });
});

describe('trading command dispatcher', () => {
  it('runs a registered command once and stops after unmount', () => {
    const run = vi.fn();
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    const second = renderHook(() => useTradingCommandDispatcher());
    const binding = renderHook(() => useTradingCommand('drawing.undo', run));

    const event = key({ key: 'z', ctrlKey: true });
    window.dispatchEvent(event);
    expect(run).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);

    binding.unmount();
    window.dispatchEvent(key({ key: 'z', ctrlKey: true }));
    expect(run).toHaveBeenCalledTimes(1);
    second.unmount();
    dispatcher.unmount();
  });

  it('does not handle events another handler already consumed, and honours overrides', () => {
    const run = vi.fn();
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    const binding = renderHook(() => useTradingCommand('drawing.undo', run));

    const consumed = key({ key: 'z', ctrlKey: true });
    consumed.preventDefault();
    window.dispatchEvent(consumed);
    expect(run).not.toHaveBeenCalled();

    setTradingCommandKeyOverrides({ 'drawing.undo': ['mod+u'] });
    window.dispatchEvent(key({ key: 'z', ctrlKey: true }));
    window.dispatchEvent(key({ key: 'u', ctrlKey: true }));
    expect(run).toHaveBeenCalledTimes(1);
    setTradingCommandKeyOverrides({});

    binding.unmount();
    dispatcher.unmount();
  });
});

afterEach(() => {
  resetTradingPointerContext();
  document.body.replaceChildren();
});

const symbolSearch = tradingCommandDefinition('chart.symbolSearch')!;
const intervalInput = tradingCommandDefinition('chart.intervalInput')!;
const moveLeft = tradingCommandDefinition('chart.moveLeft')!;

describe('typed-character commands (TVP-2.1)', () => {
  it('matches letters and interval characters without Ctrl, Command or Alt', () => {
    expect(matchesKeyPattern('letter', key({ key: 'a' }))).toBe(true);
    expect(matchesKeyPattern('letter', key({ key: 'A', shiftKey: true }))).toBe(true);
    expect(matchesKeyPattern('letter', key({ key: 'a', ctrlKey: true }))).toBe(false);
    expect(matchesKeyPattern('letter', key({ key: '5' }))).toBe(false);
    expect(matchesKeyPattern('interval', key({ key: '5' }))).toBe(true);
    expect(matchesKeyPattern('interval', key({ key: ',' }))).toBe(true);
    expect(matchesKeyPattern('interval', key({ key: '1', altKey: true }))).toBe(false);
  });

  it('lets a hotkey win over a typed-character pattern', () => {
    const letter = command(symbolSearch);
    const exact = command({ id: 'test.b', label: 'B', group: 'Test', scope: 'workspace', defaultKeys: ['b'] });
    expect(resolveCommand([exact, letter], key({ key: 'b' }))).toBe(exact);
    expect(resolveCommand([exact, letter], key({ key: 'c' }))).toBe(letter);
  });

  it('keeps pattern commands fixed even when an override names them', () => {
    const letter = command(symbolSearch);
    expect(resolveCommand([letter], key({ key: 'q' }), { 'chart.symbolSearch': ['mod+q'] })).toBe(letter);
  });
});

describe('where plain keys fire', () => {
  it('skips plain keys in widgets that navigate with them, but not modified keys', () => {
    const list = document.createElement('div');
    list.setAttribute('role', 'listbox');
    const option = document.createElement('div');
    list.append(option);
    document.body.append(list);
    expect(resolveCommand([command(intervalInput)], key({ key: '5' }, option))).toBeNull();
    const undo = command(chartUndo);
    expect(resolveCommand([undo], key({ key: 'z', ctrlKey: true }, option))).toBe(undo);
  });

  it('skips every command while a modal dialog is open, unless it allows inputs', () => {
    const dialog = document.createElement('section');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.append(dialog);
    expect(resolveCommand([command(chartUndo)], key({ key: 'z', ctrlKey: true }, document.body))).toBeNull();
    const escape = command({ id: 'test.escape', label: 'Close', group: 'Test', scope: 'workspace', defaultKeys: ['escape'], allowInInputs: true });
    expect(resolveCommand([escape], key({ key: 'Escape' }, document.body))).toBe(escape);
  });

  it('fires chart keys only while the chart area has the keyboard', () => {
    const shell = document.createElement('section');
    shell.className = 'trading-chart-shell';
    const outside = document.createElement('aside');
    document.body.append(shell, outside);
    const left = command(moveLeft);
    expect(resolveCommand([left], key({ key: 'ArrowLeft' }, document.body))).toBe(left);
    noteTradingPointerDown(outside);
    expect(resolveCommand([left], key({ key: 'ArrowLeft' }, document.body))).toBeNull();
    noteTradingPointerDown(shell);
    expect(resolveCommand([left], key({ key: 'ArrowLeft' }, document.body))).toBe(left);
    const button = document.createElement('button');
    outside.append(button);
    button.focus();
    expect(resolveCommand([left], key({ key: 'ArrowLeft' }, button))).toBeNull();
  });
});

describe('running commands without keys', () => {
  it('runs the latest active registration and reports when none can run', () => {
    const first = vi.fn();
    const second = vi.fn();
    const one = renderHook(() => useTradingCommand('chart.reset', first));
    const two = renderHook(() => useTradingCommand('chart.reset', second, () => false));
    expect(runTradingCommand('chart.reset')).toBe(true);
    expect(first).toHaveBeenCalledWith(undefined);
    expect(second).not.toHaveBeenCalled();
    one.unmount();
    expect(canRunTradingCommand('chart.reset')).toBe(false);
    expect(runTradingCommand('chart.reset')).toBe(false);
    two.unmount();
  });
});

describe('hotkey labels', () => {
  it('formats keys for Windows and macOS', () => {
    expect(formatHotkey('mod+shift+z', false)).toBe('Ctrl+Shift+Z');
    expect(formatHotkey('mod+arrowleft', false)).toBe('Ctrl+←');
    expect(formatHotkey('alt+pagedown', true)).toBe('⌥+Page Down');
    expect(formatCommandKeys(symbolSearch, [], false)).toEqual(['A–Z']);
  });
});

describe('browser and installed-app keys (TVP-2.3)', () => {
  const newTab = tradingCommandDefinition('tab.new')!;

  it('uses TradingView keys the browser keeps only in the installed app', () => {
    const tab = command(newTab);
    expect(resolveCommand([tab], key({ key: 't', ctrlKey: true }))).toBeNull();
    expect(resolveCommand([tab], key({ key: 'T', code: 'KeyT', altKey: true, shiftKey: true }))).toBe(tab);
    expect(resolveCommand([tab], key({ key: 't', ctrlKey: true }), {}, 'installed')).toBe(tab);
    expect(resolveCommand([tab], key({ key: 'T', code: 'KeyT', altKey: true, shiftKey: true }), {}, 'installed')).toBe(tab);
  });

  it('has no conflicting default keys in either mode', () => {
    expect(findKeyConflicts(TRADING_COMMANDS, {}, 'browser')).toEqual([]);
    expect(findKeyConflicts(TRADING_COMMANDS, {}, 'installed')).toEqual([]);
  });

  it('switches the dispatcher to installed-app keys', () => {
    const run = vi.fn();
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    const binding = renderHook(() => useTradingCommand('tab.new', run));
    window.dispatchEvent(key({ key: 't', ctrlKey: true }));
    expect(run).not.toHaveBeenCalled();
    setTradingCommandAvailability('installed');
    window.dispatchEvent(key({ key: 't', ctrlKey: true }));
    expect(run).toHaveBeenCalledTimes(1);
    setTradingCommandAvailability('browser');
    binding.unmount();
    dispatcher.unmount();
  });

  it('matches Alt+digit by physical key', () => {
    expect(matchesHotkey('alt+1', key({ key: '¡', code: 'Digit1', altKey: true }))).toBe(true);
    expect(matchesHotkey('alt+1', key({ key: '2', code: 'Digit2', altKey: true }))).toBe(false);
  });
});

describe('watchlist grid keys (TVP-2.3)', () => {
  const expected: Record<string, string> = {
    'watchlist.next': 'next',
    'watchlist.previous': 'previous',
    'watchlist.extendNext': 'extendNext',
    'watchlist.extendPrevious': 'extendPrevious',
    'watchlist.selectAll': 'selectAll',
  };
  const eventFor = (hotkey: string) => {
    const parts = hotkey.split('+');
    const name = parts.at(-1)!;
    const keyName = ({ arrowdown: 'ArrowDown', arrowup: 'ArrowUp', space: ' ' } as Record<string, string>)[name] ?? name;
    return { key: keyName, code: '', shiftKey: parts.includes('shift'), ctrlKey: parts.includes('mod'), metaKey: false, altKey: false };
  };
  const gridDefinitions = TRADING_COMMANDS.filter((definition: TradingCommandDefinition) => definition.handledLocally);

  it('lists every catalogued grid key as the command the grid runs, and never dispatches them', () => {
    expect(gridDefinitions.map((definition) => definition.id).sort()).toEqual(Object.keys(expected).sort());
    for (const definition of gridDefinitions) {
      for (const hotkey of definition.defaultKeys) expect(watchlistKeyCommand(eventFor(hotkey))).toBe(expected[definition.id]);
      expect(resolveCommand([command(definition)], key(eventFor(definition.defaultKeys[0]!)))).toBeNull();
    }
  });

  it('catalogues every key the grid reads', () => {
    const candidates = ['ArrowDown', 'ArrowUp', ' ', 'a', 'Home', 'End', 'Enter', 'ArrowLeft', 'ArrowRight', 'j']
      .flatMap((name) => [false, true].flatMap((shiftKey) => [false, true].flatMap((ctrlKey) => [false, true].map((metaKey) => (
        { key: name, code: '', shiftKey, ctrlKey, metaKey, altKey: false }
      )))));
    for (const event of candidates) {
      const gridCommand = watchlistKeyCommand(event);
      if (!gridCommand) continue;
      const id = Object.keys(expected).find((candidate) => expected[candidate] === gridCommand)!;
      const definition = tradingCommandDefinition(id)!;
      expect(definition.defaultKeys.some((hotkey) => matchesHotkey(hotkey, key(event))), `${JSON.stringify(event)} → ${gridCommand}`).toBe(true);
    }
  });
});

describe('one key rule for recording and matching', () => {
  const layouts: Array<[string, KeyboardEventInit]> = [
    ['US Ctrl+Z', { key: 'z', code: 'KeyZ', ctrlKey: true }],
    ['QWERTZ Ctrl+Z (physical Y)', { key: 'z', code: 'KeyY', ctrlKey: true }],
    ['QWERTZ / (Shift+7)', { key: '/', code: 'Digit7', shiftKey: true }],
    ['QWERTZ Ctrl+/ (Ctrl+Shift+7)', { key: '/', code: 'Digit7', ctrlKey: true, shiftKey: true }],
    ['AZERTY Ctrl+& (digit row)', { key: '&', code: 'Digit1', ctrlKey: true }],
    ['AZERTY Ctrl+Shift+1', { key: '1', code: 'Digit1', ctrlKey: true, shiftKey: true }],
    ['US Shift+1', { key: '!', code: 'Digit1', shiftKey: true }],
    ['macOS Option+T', { key: '†', code: 'KeyT', altKey: true }],
    ['Alt+Shift+T', { key: 'T', code: 'KeyT', altKey: true, shiftKey: true }],
    ['Ctrl+Shift+K', { key: 'K', code: 'KeyK', ctrlKey: true, shiftKey: true }],
    ['⌘+←', { key: 'ArrowLeft', code: 'ArrowLeft', metaKey: true }],
    ['Alt+Page Down', { key: 'PageDown', code: 'PageDown', altKey: true }],
    ['Shift+Tab', { key: 'Tab', code: 'Tab', shiftKey: true }],
    ['Ctrl++', { key: '+', code: 'Equal', ctrlKey: true, shiftKey: true }],
  ];

  it.each(layouts)('a recorded %s matches the same key press', (_name, init) => {
    const event = key(init);
    const hotkey = hotkeyFromEvent(event);
    expect(hotkey).not.toBeNull();
    expect(matchesHotkey(hotkey!, event)).toBe(true);
  });

  it('records and matches what the key types, so layouts keep TradingView defaults', () => {
    expect(hotkeyFromEvent(key({ key: 'z', code: 'KeyY', ctrlKey: true }))).toBe('mod+z');
    expect(matchesHotkey('mod+z', key({ key: 'z', code: 'KeyY', ctrlKey: true }))).toBe(true);
    expect(matchesHotkey('mod+z', key({ key: 'y', code: 'KeyZ', ctrlKey: true }))).toBe(false);
    expect(hotkeyFromEvent(key({ key: '&', code: 'Digit1', ctrlKey: true }))).toBe('mod+&');
    expect(matchesHotkey('mod+1', key({ key: '1', code: 'Digit1', ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(matchesHotkey('/', key({ key: '/', code: 'Digit7', shiftKey: true }))).toBe(true);
    expect(matchesHotkey('mod+/', key({ key: '/', code: 'Digit7', ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(hotkeyFromEvent(key({ key: '!', code: 'Digit1', shiftKey: true }))).toBe('!');
    expect(hotkeyFromEvent(key({ key: 'Shift', code: 'ShiftLeft', shiftKey: true }))).toBeNull();
    expect(hotkeyFromEvent(key({ key: 'Dead', code: 'BracketLeft' }))).toBeNull();
  });

  it('accepts a digit typed with Shift (AZERTY) for the interval box', () => {
    expect(matchesKeyPattern('interval', key({ key: '5', code: 'Digit5', shiftKey: true }))).toBe(true);
  });
});

describe('review fixes: overrides, repeat and composition', () => {
  afterEach(() => {
    setTradingCommandKeyOverrides({});
    setTradingCommandAvailability('browser');
  });

  it('falls back to the default keys in the browser when an override only has browser-reserved keys', () => {
    const newTab = tradingCommandDefinition('tab.new')!;
    expect(commandKeys(newTab, { 'tab.new': ['mod+t'] }, 'browser')).toEqual(newTab.defaultKeys);
    expect(commandKeys(newTab, { 'tab.new': ['mod+t'] }, 'installed')).toEqual(['mod+t', ...(newTab.installedKeys ?? [])]);
    expect(commandKeys(newTab, { 'tab.new': ['mod+t', 'alt+n'] }, 'browser')).toEqual(['alt+n']);
  });

  it('refuses Ctrl+Alt bindings because they are AltGr characters on many layouts', () => {
    const reset = tradingCommandDefinition('chart.reset')!;
    expect(rebindProblem(reset, 'mod+alt+e')).toMatch(/AltGr/);
    expect(rebindProblem(reset, 'alt+e')).toBeNull();
  });

  it('runs a command once per press but repeats moving and zooming while the key is held', () => {
    const newTab = vi.fn();
    const moveLeft = vi.fn();
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    const tabBinding = renderHook(() => useTradingCommand('tab.new', newTab));
    const moveBinding = renderHook(() => useTradingCommand('chart.moveLeft', moveLeft));

    window.dispatchEvent(key({ key: 'T', code: 'KeyT', altKey: true, shiftKey: true }));
    const held = key({ key: 'T', code: 'KeyT', altKey: true, shiftKey: true, repeat: true });
    window.dispatchEvent(held);
    expect(newTab).toHaveBeenCalledTimes(1);
    expect(held.defaultPrevented).toBe(true);

    expect(tradingCommandDefinition('chart.moveLeft')!.repeatable).toBe(true);
    expect(tradingCommandDefinition('tab.new')!.repeatable).toBeUndefined();

    tabBinding.unmount();
    moveBinding.unmount();
    dispatcher.unmount();
  });

  it('ignores key presses while an input method is composing', () => {
    const undo = vi.fn();
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    const binding = renderHook(() => useTradingCommand('drawing.undo', undo));
    window.dispatchEvent(key({ key: 'z', code: 'KeyZ', ctrlKey: true, isComposing: true }));
    expect(undo).not.toHaveBeenCalled();
    window.dispatchEvent(key({ key: 'z', code: 'KeyZ', ctrlKey: true }));
    expect(undo).toHaveBeenCalledTimes(1);
    binding.unmount();
    dispatcher.unmount();
  });
});

describe('chart key context for plain keys', () => {
  const outsideButton = () => {
    const button = document.createElement('button');
    document.body.append(button);
    noteTradingPointerDown(button);
    button.focus();
    return button;
  };

  it('keeps /, . and Delete on the chart', () => {
    const button = outsideButton();
    const commands = ['chart.indicators', 'layout.load', 'drawing.delete'].map((id) => command(tradingCommandDefinition(id)!));
    expect(resolveCommand(commands, key({ key: '/' }, button))).toBeNull();
    expect(resolveCommand(commands, key({ key: '.' }, button))).toBeNull();
    expect(resolveCommand(commands, key({ key: 'Backspace' }, button))).toBeNull();
    resetTradingPointerContext();
    button.blur();
    expect(resolveCommand(commands, key({ key: 'Backspace' }, document.body))).toBe(commands[2]);
  });

  it('gives any key without a modifier the chart context, even from stored overrides', () => {
    const reset = command(tradingCommandDefinition('chart.reset')!);
    const overrides = { 'chart.reset': ['x'] };
    expect(resolveCommand([reset], key({ key: 'x' }, document.body), overrides)).toBe(reset);
    const button = outsideButton();
    expect(resolveCommand([reset], key({ key: 'x' }, button), overrides)).toBeNull();
  });

  it('forgets a chart click on Escape, ignores clicks in modal dialogs and returns to the chart after symbol search', () => {
    const shell = document.createElement('section');
    shell.className = 'trading-chart-shell';
    shell.tabIndex = 0;
    document.body.append(shell);
    noteTradingPointerDown(shell);
    shell.focus();
    expect(chartKeyContextActive('chartClicked')).toBe(true);
    const dispatcher = renderHook(() => useTradingCommandDispatcher());
    window.dispatchEvent(key({ key: 'Escape' }));
    expect(chartKeyContextActive('chartClicked')).toBe(false);
    dispatcher.unmount();

    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    const result = document.createElement('button');
    dialog.append(result);
    document.body.append(dialog);
    shell.blur();
    noteTradingPointerDown(result);
    expect(chartKeyContextActive('chart')).toBe(true);
    dialog.remove();
    noteTradingPointerDown(document.body);
    expect(chartKeyContextActive('chart')).toBe(false);
    returnKeyboardToChart();
    expect(chartKeyContextActive('chart')).toBe(true);
  });

  it('never dispatches planned commands', () => {
    const planned = command({ ...tradingCommandDefinition('chart.goToDate')!, planned: true });
    expect(resolveCommand([planned], key({ key: 'g', code: 'KeyG', altKey: true }))).toBeNull();
    const goToDate = command(tradingCommandDefinition('chart.goToDate')!);
    expect(resolveCommand([goToDate], key({ key: 'g', code: 'KeyG', altKey: true }))?.definition.id).toBe('chart.goToDate');
  });
});

describe('rebinding rules (TVP-2.4)', () => {
  const definition = (id: string) => tradingCommandDefinition(id)!;

  it('refuses unmodified Enter, Space, Tab and Escape, and typing keys outside the chart', () => {
    for (const hotkey of ['enter', 'space', 'tab', 'escape', 'shift+tab']) {
      expect(rebindProblem(definition('chart.zoomIn'), hotkey)).toMatch(/Enter, Space, Tab and Escape/);
    }
    expect(rebindProblem(definition('chart.zoomIn'), 'mod+enter')).toBeNull();
    expect(rebindProblem(definition('chart.reset'), 'x')).toMatch(/Add a modifier/);
    expect(rebindProblem(definition('layout.save'), 'arrowup')).toMatch(/Add a modifier/);
    expect(rebindProblem(definition('chart.moveLeft'), 'h')).toBeNull();
    expect(rebindProblem(definition('chart.reset'), 'alt+x')).toBeNull();
  });

  it('refuses browser-reserved keys in the browser and keeps installed-app keys after a rebind', () => {
    expect(rebindProblem(definition('layout.save'), 'mod+t')).toMatch(/browser keeps/);
    expect(rebindProblem(definition('layout.save'), 'mod+t', 'installed')).toBeNull();
    const overrides = { 'tab.new': ['alt+shift+n'] };
    expect(commandKeys(definition('tab.new'), overrides, 'installed')).toEqual(['alt+shift+n', 'mod+t', 'mod+u']);
    expect(commandKeys(definition('tab.new'), overrides, 'browser')).toEqual(['alt+shift+n']);
  });

  it('refuses fixed and planned commands', () => {
    expect(rebindProblem(definition('chart.symbolSearch'), 'mod+q')).toMatch(/can't be rebound/);
    expect(rebindProblem({ ...definition('chart.goToDate'), planned: true }, 'mod+q')).toMatch(/can't be rebound/);
  });
});

describe('key clashes across scopes and with typing', () => {
  it('has none in the defaults', () => {
    expect(findKeyClashes(TRADING_COMMANDS, {}, 'browser')).toEqual([]);
    expect(findKeyClashes(TRADING_COMMANDS, {}, 'installed')).toEqual([]);
  });

  it('finds a workspace key shadowed by a chart key, and plain letters or digits typing already uses', () => {
    expect(findKeyClashes(TRADING_COMMANDS, { 'layout.save': ['alt+r'] })).toEqual([
      { hotkey: 'alt+r', ids: ['chart.reset', 'layout.save'], kind: 'shadowed' },
    ]);
    expect(findKeyClashes(TRADING_COMMANDS, { 'chart.moveLeft': ['h'], 'chart.moveRight': ['5'] })).toEqual([
      { hotkey: 'h', ids: ['chart.moveLeft', 'chart.symbolSearch'], kind: 'typing' },
      { hotkey: '5', ids: ['chart.moveRight', 'chart.intervalInput'], kind: 'typing' },
    ]);
  });
});


describe('defaults and rebinding (TVP-2.2 review)', () => {
  it('every rebindable default passes the rebind rules, so a user can always restore it', () => {
    for (const definition of TRADING_COMMANDS as readonly TradingCommandDefinition[]) {
      if (!isRebindable(definition)) continue;
      for (const key of definition.defaultKeys) expect([definition.id, rebindProblem(definition, key, 'installed')]).toEqual([definition.id, null]);
    }
  });

  it('still refuses Ctrl+Alt for keys other than the default', () => {
    expect(rebindProblem(tradingCommandDefinition('drawing.hideAll')!, 'ctrl+alt+j')).toMatch(/AltGr/);
  });
});
