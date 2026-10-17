import { act, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test/renderWithProviders';
import { tradingApi } from '../tradingApi';
import { useTradingStore, type TradingTabState } from '../tradingStore';
import type { CanonicalInstrument } from '../tradingTypes';
import { TradingKeyboardLayer } from './TradingKeyboardLayer';
import { noteTradingPointerDown } from './chartKeyContext';
import { KEY_OVERRIDES_STORAGE_KEY } from './keyOverridesStorage';
import { setTradingCommandAvailability, setTradingCommandKeyOverrides, useTradingCommandDispatcher } from './useTradingCommands';

const initialStore = useTradingStore.getState();

const bitcoin = {
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  asset_class: 'crypto',
  instrument_type: 'spot',
  venue: 'BINANCE',
  venue_symbol: 'BTCUSDT',
  display_symbol: 'BTC/USDT',
} as CanonicalInstrument;

function Harness(props: Parameters<typeof TradingKeyboardLayer>[0]) {
  useTradingCommandDispatcher();
  return <TradingKeyboardLayer {...props} />;
}

function setup(status: 'saved' | 'conflict' = 'saved') {
  const persistence = {
    status,
    workspaces: [
      { workspaceId: 'main', name: 'Main Workspace', revision: 1 },
      { workspaceId: 'swing', name: 'Swing setups', revision: 1 },
    ],
    activeWorkspaceId: 'main',
    selectWorkspace: vi.fn(async () => undefined),
    saveNow: vi.fn(async () => undefined),
  };
  const onOpenSymbolSearch = vi.fn();
  const onCloseTab = vi.fn((tab: TradingTabState) => useTradingStore.getState().removeTab(tab.tabId));
  renderWithProviders(
    <Harness persistence={persistence} supportedIntervals={['1m', '1h', '4h', '1d']} onOpenSymbolSearch={onOpenSymbolSearch} onCloseTab={onCloseTab} />,
  );
  return { persistence, onOpenSymbolSearch, onCloseTab };
}

const press = (init: KeyboardEventInit, target: Element = document.body) => act(() => { fireEvent.keyDown(target, init); });

beforeEach(() => {
  vi.spyOn(tradingApi, 'instruments').mockResolvedValue([bitcoin]);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  act(() => setTradingCommandKeyOverrides({}));
  act(() => useTradingStore.setState(initialStore, true));
});

describe('chart typing shortcuts (TVP-2.1)', () => {
  it('opens symbol search with the typed letter', () => {
    const { onOpenSymbolSearch } = setup();
    press({ key: 'b' });
    expect(onOpenSymbolSearch).toHaveBeenCalledWith('b');
  });

  it('ignores typing in text fields', () => {
    const { onOpenSymbolSearch } = setup();
    const input = document.createElement('input');
    document.body.append(input);
    press({ key: 'b' }, input);
    press({ key: '5' }, input);
    expect(onOpenSymbolSearch).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'Change interval' })).not.toBeInTheDocument();
    input.remove();
  });

  it('opens the interval box with the typed digit and applies a valid interval', () => {
    setup();
    press({ key: '1' });
    const box = screen.getByRole('textbox', { name: 'Interval' });
    expect(box).toHaveValue('1');
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: '5s' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(within(screen.getByRole('dialog', { name: 'Change interval' })).getByRole('alert')).toHaveTextContent('trade-level data');
    fireEvent.change(box, { target: { value: '4h' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(screen.queryByRole('textbox', { name: 'Interval' })).not.toBeInTheDocument();
    expect(useTradingStore.getState().charts[0].interval).toBe('4h');
  });

  it('opens an empty interval box with a comma and rejects unknown text', () => {
    setup();
    press({ key: ',' });
    const box = screen.getByRole('textbox', { name: 'Interval' });
    expect(box).toHaveValue('');
    fireEvent.change(box, { target: { value: '7q' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(within(screen.getByRole('dialog', { name: 'Change interval' })).getByRole('alert')).toHaveTextContent('not an interval');
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: 'Interval' })).not.toBeInTheDocument();
  });
});

describe('command palette and layouts (TVP-2.1)', () => {
  it('lists catalogued commands with their keys and runs quick actions', async () => {
    setup();
    press({ key: 'k', ctrlKey: true });
    const input = screen.getByRole('combobox', { name: 'Command palette' });
    expect(screen.getByRole('option', { name: /Reset chart view.*not available now.*Alt\+R/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Load layout Swing setups/ })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'interval 4 hours' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(useTradingStore.getState().charts[0].interval).toBe('4h');

    press({ key: 'k', ctrlKey: true });
    fireEvent.change(screen.getByRole('combobox', { name: 'Command palette' }), { target: { value: 'btc' } });
    expect(await screen.findByRole('option', { name: /BTC\/USDT · BINANCE/ })).toBeInTheDocument();
  });

  it('opens the layout list with a full stop and saves with Ctrl+S', () => {
    const { persistence } = setup();
    press({ key: '.' });
    const input = screen.getByRole('combobox', { name: 'Load layout' });
    fireEvent.change(input, { target: { value: 'swing' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(persistence.selectWorkspace).toHaveBeenCalledWith('swing');
    press({ key: 's', ctrlKey: true });
    expect(persistence.saveNow).toHaveBeenCalledTimes(1);
  });
});

describe('tab and layout shortcuts (TVP-2.3)', () => {
  const store = useTradingStore.getState;
  const tabIds = () => store().tabs.map((tab) => tab.tabId);
  const alt = (code: string, extra: KeyboardEventInit = {}) => press({ key: code.replace(/^(Key|Digit)/, '').toLowerCase(), code, altKey: true, ...extra });

  it('opens, switches, closes and reopens tabs with the browser keys', () => {
    const { onCloseTab } = setup();
    alt('KeyT', { shiftKey: true });
    alt('KeyT', { shiftKey: true });
    const [first, second, third] = tabIds();
    expect(tabIds()).toHaveLength(3);
    expect(store().activeTabId).toBe(third);

    alt('PageDown', { key: 'PageDown' });
    expect(store().activeTabId).toBe(first);
    alt('PageUp', { key: 'PageUp' });
    expect(store().activeTabId).toBe(third);
    alt('Digit2');
    expect(store().activeTabId).toBe(second);
    alt('Digit9');
    expect(store().activeTabId).toBe(third);
    alt('Digit8');
    expect(store().activeTabId).toBe(third);

    alt('KeyW', { shiftKey: true });
    expect(onCloseTab).toHaveBeenCalledWith(expect.objectContaining({ tabId: third }));
    expect(tabIds()).toEqual([first, second]);
    alt('KeyZ', { shiftKey: true });
    expect(tabIds()).toEqual([first, second, third]);
    expect(store().activeTabId).toBe(third);
  });

  it('keeps the browser-reserved keys for the installed app', () => {
    setup();
    press({ key: 't', ctrlKey: true });
    expect(tabIds()).toHaveLength(1);
    setTradingCommandAvailability('installed');
    press({ key: 't', ctrlKey: true });
    expect(tabIds()).toHaveLength(2);
    press({ key: '1', ctrlKey: true });
    expect(store().activeTabId).toBe(tabIds()[0]);
    setTradingCommandAvailability('browser');
  });

  it('switches charts with Tab only after a click in the chart area', () => {
    setup();
    act(() => store().setChartCount(3));
    const [chartOne, chartTwo] = store().charts.map((chart) => chart.chartId);
    act(() => store().setActiveChart(chartOne));
    press({ key: 'Tab' });
    expect(store().activeChartId).toBe(chartOne);

    const shell = document.createElement('section');
    shell.className = 'trading-chart-shell';
    document.body.append(shell);
    noteTradingPointerDown(shell);
    press({ key: 'Tab' });
    expect(store().activeChartId).toBe(chartTwo);
    press({ key: 'Tab', shiftKey: true });
    expect(store().activeChartId).toBe(chartOne);
    shell.remove();
  });

  it('lets Tab leave past the last chart, and Escape forgets the chart click', () => {
    setup();
    act(() => store().setChartCount(3));
    const [chartOne, chartTwo, chartThree] = store().charts.map((chart) => chart.chartId);
    act(() => store().setActiveChart(chartTwo));
    const shell = document.createElement('section');
    shell.className = 'trading-chart-shell';
    shell.tabIndex = 0;
    document.body.append(shell);
    noteTradingPointerDown(shell);
    shell.focus();

    press({ key: 'Tab' }, shell);
    expect(store().activeChartId).toBe(chartThree);
    const leaving = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => { shell.dispatchEvent(leaving); });
    expect(leaving.defaultPrevented).toBe(false);
    expect(store().activeChartId).toBe(chartThree);

    press({ key: 'Escape' }, shell);
    press({ key: 'Tab', shiftKey: true }, shell);
    expect(store().activeChartId).toBe(chartThree);
    expect(chartOne).toBeDefined();
    shell.remove();
  });
});

describe('dialog focus (TVP-2.4 review)', () => {
  it('keeps Tab in the palette, closes it with Escape and returns focus to the opener', () => {
    setup();
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    press({ key: 'k', ctrlKey: true }, opener);
    const input = screen.getByRole('combobox', { name: 'Command palette' });
    expect(input).toHaveFocus();
    expect(within(screen.getByRole('dialog', { name: 'Command palette' })).getByRole('status')).toHaveTextContent(/\d+ results/);
    fireEvent.keyDown(input, { key: 'Tab' });
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    press({ key: 'k', ctrlKey: true }, opener);
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    opener.remove();
  });

  it('keeps Tab in the interval box, points its description at the error, and Escape returns focus', () => {
    setup();
    press({ key: '0' });
    const box = screen.getByRole('textbox', { name: 'Interval' });
    expect(box).toHaveAttribute('aria-describedby', 'trading-interval-box-hint');
    fireEvent.keyDown(box, { key: 'Tab', shiftKey: true });
    expect(box).toHaveFocus();
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box).toHaveAttribute('aria-describedby', 'trading-interval-box-error');
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Change interval' })).not.toBeInTheDocument();
    expect(document.body).toHaveFocus();
  });

  it('marks commands no mounted chart can run as not available in the palette', () => {
    setup();
    press({ key: 'k', ctrlKey: true });
    expect(screen.getByRole('option', { name: /Go to date.*not available now/ })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('Ctrl+S (TVP-2.4 review)', () => {
  it('saves from text fields without the browser Save dialog', () => {
    const { persistence } = setup();
    const input = document.createElement('input');
    document.body.append(input);
    const event = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true });
    act(() => { input.dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(true);
    expect(persistence.saveNow).toHaveBeenCalledTimes(1);
    input.remove();
  });

  it('explains a save conflict instead of saving', () => {
    const { persistence } = setup('conflict');
    press({ key: 's', ctrlKey: true });
    expect(persistence.saveNow).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Reload server or Overwrite server');
  });
});

describe('shortcut dialog entry points (TVP-2.4)', () => {
  it('opens with Ctrl+/ and from the toolbar button', () => {
    setup();
    press({ key: '/', ctrlKey: true });
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close keyboard shortcuts' }));
    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: 'Shortcuts' });
    expect(trigger).toHaveAttribute('title', 'Keyboard shortcuts (Ctrl+/)');
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
  });

  it('applies keys rebound on an earlier visit', () => {
    window.localStorage.setItem(KEY_OVERRIDES_STORAGE_KEY, JSON.stringify({ 'layout.save': ['mod+e'] }));
    const { persistence } = setup();
    press({ key: 's', ctrlKey: true });
    expect(persistence.saveNow).not.toHaveBeenCalled();
    press({ key: 'e', ctrlKey: true });
    expect(persistence.saveNow).toHaveBeenCalledTimes(1);
  });
});
