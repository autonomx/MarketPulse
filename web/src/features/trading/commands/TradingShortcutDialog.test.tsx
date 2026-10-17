import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { hotkeyFromEvent } from './hotkeys';
import { KEY_OVERRIDES_STORAGE_KEY, loadStoredKeyOverrides, sanitizeKeyOverrides } from './keyOverridesStorage';
import { TradingShortcutDialog } from './TradingShortcutDialog';
import { setTradingCommandAvailability, setTradingCommandKeyOverrides, tradingCommandKeyOverrides } from './useTradingCommands';

afterEach(() => {
  act(() => {
    setTradingCommandKeyOverrides({});
    setTradingCommandAvailability('browser');
  });
  window.localStorage.clear();
});

const row = (label: string) => screen.getAllByRole('rowheader').find((header) => header.textContent?.startsWith(label))!.closest('tr') as HTMLElement;

function capture(label: string, init: KeyboardEventInit) {
  fireEvent.click(screen.getByRole('button', { name: `Change keys for ${label}` }));
  const button = screen.getByRole('button', { name: `Press new keys for ${label}` });
  expect(button).toHaveTextContent('Press keys…');
  fireEvent.keyDown(button, init);
}

describe('TradingShortcutDialog (TVP-2.4)', () => {
  it('lists every command by context and filters by name, context or key', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    for (const group of ['General', 'Chart', 'Drawings', 'Layout', 'Watchlist', 'Tabs']) {
      expect(screen.getByRole('heading', { name: group })).toBeInTheDocument();
    }
    expect(within(row('Reset chart view')).getByText('Alt+R')).toBeInTheDocument();
    expect(within(row('New tab')).getByText('Alt+Shift+T')).toBeInTheDocument();
    expect(within(row('New tab')).getByText('Ctrl+T · app')).toBeInTheDocument();
    expect(within(row('Next symbol')).getByText('Watchlist grid')).toBeInTheDocument();
    expect(within(row('Change symbol')).getByText('Fixed')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search shortcuts' }), { target: { value: 'zoom' } });
    expect(screen.getAllByRole('rowheader').map((header) => header.textContent)).toEqual(['Zoom in', 'Zoom out']);
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search shortcuts' }), { target: { value: 'alt+9' } });
    expect(screen.getAllByRole('rowheader')).toHaveLength(1);
  });

  it('rebinds a command, saves it and resets it', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('Reset chart view', { key: '≈', code: 'KeyX', altKey: true });
    expect(within(row('Reset chart view')).getByText('Alt+X')).toBeInTheDocument();
    expect(tradingCommandKeyOverrides()).toEqual({ 'chart.reset': ['alt+x'] });
    expect(JSON.parse(window.localStorage.getItem(KEY_OVERRIDES_STORAGE_KEY)!)).toEqual({ 'chart.reset': ['alt+x'] });
    expect(loadStoredKeyOverrides()).toEqual({ 'chart.reset': ['alt+x'] });

    fireEvent.click(screen.getByRole('button', { name: 'Reset Reset chart view' }));
    expect(within(row('Reset chart view')).getByText('Alt+R')).toBeInTheDocument();
    expect(window.localStorage.getItem(KEY_OVERRIDES_STORAGE_KEY)).toBeNull();
  });

  it('shows conflicts and clears every change with Reset all', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    capture('Zoom in', { key: 'ArrowDown', code: 'ArrowDown', ctrlKey: true });
    expect(screen.getByRole('alert')).toHaveTextContent('1 key is used by more than one command');
    expect(within(row('Zoom in')).getByText('Same key as Zoom out')).toBeInTheDocument();
    expect(within(row('Zoom out')).getByText('Same key as Zoom in')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Reset all' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(tradingCommandKeyOverrides()).toEqual({});
    expect(screen.getByRole('button', { name: 'Reset all' })).toBeDisabled();
  });

  it('cancels with Escape, ignores lone modifiers and warns about browser keys', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('Save layout (workspace)', { key: 'Control', code: 'ControlLeft', ctrlKey: true });
    const waiting = screen.getByRole('button', { name: 'Press new keys for Save layout (workspace)' });
    fireEvent.keyDown(waiting, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Change keys for Save layout (workspace)' })).toBeInTheDocument();
    expect(tradingCommandKeyOverrides()).toEqual({});

    capture('Save layout (workspace)', { key: 't', code: 'KeyT', ctrlKey: true });
    expect(screen.getByRole('status')).toHaveTextContent('only works in the installed app');
  });

  it('closes with Escape outside a key capture', () => {
    let closed = false;
    render(<TradingShortcutDialog open onClose={() => { closed = true; }} />);
    fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Search shortcuts' }), { key: 'Escape' });
    expect(closed).toBe(true);
  });
});

describe('TradingShortcutDialog review fixes', () => {
  it('refuses keys that would break typing, buttons or focus, and says why', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('Reset chart view', { key: 'x', code: 'KeyX' });
    expect(screen.getByRole('status')).toHaveTextContent('Add a modifier');
    fireEvent.keyDown(screen.getByRole('button', { name: 'Press new keys for Reset chart view' }), { key: 'Enter', code: 'Enter', ctrlKey: false });
    expect(screen.getByRole('status')).toHaveTextContent('Enter, Space, Tab and Escape');
    expect(tradingCommandKeyOverrides()).toEqual({});
    fireEvent.keyDown(screen.getByRole('button', { name: 'Press new keys for Reset chart view' }), { key: 'Escape' });

    capture('Save layout (workspace)', { key: 't', code: 'KeyT', ctrlKey: true });
    expect(tradingCommandKeyOverrides()).toEqual({});
    expect(within(row('Save layout (workspace)')).getByText('Ctrl+S')).toBeInTheDocument();
  });

  it('allows plain keys for chart commands and shows the clash with typing', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('Move chart one bar left', { key: 'h', code: 'KeyH' });
    expect(tradingCommandKeyOverrides()).toEqual({ 'chart.moveLeft': ['h'] });
    expect(within(row('Move chart one bar left')).getByText(/Clashes with typing: Change symbol/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('typing on the chart');
  });

  it('shows a workspace key shadowed by a chart key', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('Save layout (workspace)', { key: 'r', code: 'KeyR', altKey: true });
    expect(within(row('Save layout (workspace)')).getByText('Same key, other context: Reset chart view')).toBeInTheDocument();
  });

  it('keeps the installed-app keys visible after a rebind', () => {
    render(<TradingShortcutDialog open onClose={() => undefined} />);
    capture('New tab, copying this one (browser Alt+Shift+T, app Ctrl+T)', { key: 'N', code: 'KeyN', altKey: true, shiftKey: true });
    expect(within(row('New tab')).getByText('Alt+Shift+N')).toBeInTheDocument();
    expect(within(row('New tab')).getByText('Ctrl+T · app')).toBeInTheDocument();
    expect(within(row('Go to date')).getByText('Alt+G')).toBeInTheDocument();
  });

  it('keeps Tab inside the dialog, closes with Escape from any control and returns focus', () => {
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    const view = render(<TradingShortcutDialog open onClose={() => view.rerender(<TradingShortcutDialog open={false} onClose={() => undefined} />)} />);
    const search = screen.getByRole('searchbox', { name: 'Search shortcuts' });
    expect(search).toHaveFocus();
    const close = screen.getByRole('button', { name: 'Close keyboard shortcuts' });
    const last = screen.getAllByRole('button').at(-1)!;
    close.focus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Change keys for Zoom in' }), { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});

describe('stored key overrides', () => {
  it('keeps only known, rebindable commands and survives bad storage', () => {
    expect(sanitizeKeyOverrides({
      'chart.reset': ['Alt+X', 'alt+x', 7],
      'chart.symbolSearch': ['mod+q'],
      'watchlist.next': ['j'],
      'nope.command': ['mod+j'],
      'layout.save': 'mod+e',
      'tab.close': ['k'],
    })).toEqual({ 'chart.reset': ['alt+x'] });
    window.localStorage.setItem(KEY_OVERRIDES_STORAGE_KEY, '{not json');
    expect(loadStoredKeyOverrides()).toEqual({});
  });

  it('turns key presses into hotkey strings', () => {
    expect(hotkeyFromEvent({ key: 'K', code: 'KeyK', ctrlKey: true, metaKey: false, altKey: false, shiftKey: true })).toBe('mod+shift+k');
    expect(hotkeyFromEvent({ key: '†', code: 'KeyT', ctrlKey: false, metaKey: false, altKey: true, shiftKey: false })).toBe('alt+t');
    expect(hotkeyFromEvent({ key: '!', code: 'Digit1', ctrlKey: false, metaKey: false, altKey: false, shiftKey: true })).toBe('!');
    expect(hotkeyFromEvent({ key: 'ArrowLeft', code: 'ArrowLeft', ctrlKey: false, metaKey: true, altKey: false, shiftKey: false })).toBe('mod+arrowleft');
    expect(hotkeyFromEvent({ key: 'Shift', code: 'ShiftLeft', ctrlKey: false, metaKey: false, altKey: false, shiftKey: true })).toBeNull();
  });
});
