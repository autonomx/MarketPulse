import { describe, expect, it } from 'vitest';
import {
  applyWatchlistCommand,
  clickWatchlistRow,
  watchlistGridRows,
  watchlistRowTabStop,
  isWatchlistKeyTargetIgnored,
  watchlistKeyCommand,
  type WatchlistSelection,
} from './useTradingWatchlistSelection';

const order = ['a', 'b', 'c', 'd'];
const none: WatchlistSelection = { ids: [], cursor: null, anchor: null };
const key = (value: string, modifiers: Partial<Record<'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey', boolean>> = {}) => ({
  key: value,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...modifiers,
});

describe('watchlist key commands', () => {
  it('maps the navigation keys', () => {
    expect(watchlistKeyCommand(key('ArrowDown'))).toBe('next');
    expect(watchlistKeyCommand(key('ArrowUp'))).toBe('previous');
    expect(watchlistKeyCommand(key(' '))).toBe('next');
    expect(watchlistKeyCommand(key(' ', { shiftKey: true }))).toBe('previous');
    expect(watchlistKeyCommand(key('ArrowDown', { shiftKey: true }))).toBe('extendNext');
    expect(watchlistKeyCommand(key('ArrowUp', { shiftKey: true }))).toBe('extendPrevious');
    expect(watchlistKeyCommand(key('a', { ctrlKey: true }))).toBe('selectAll');
    expect(watchlistKeyCommand(key('A', { metaKey: true }))).toBe('selectAll');
  });

  it('leaves other keys and modified arrows alone', () => {
    expect(watchlistKeyCommand(key('a'))).toBeNull();
    expect(watchlistKeyCommand(key('ArrowDown', { ctrlKey: true }))).toBeNull();
    expect(watchlistKeyCommand(key('ArrowDown', { altKey: true }))).toBeNull();
    expect(watchlistKeyCommand(key('Enter'))).toBeNull();
  });

  it('ignores keys typed into fields and menus', () => {
    const input = document.createElement('input');
    const menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    const menuItem = document.createElement('button');
    menu.append(menuItem);
    expect(isWatchlistKeyTargetIgnored(input)).toBe(true);
    expect(isWatchlistKeyTargetIgnored(menuItem)).toBe(true);
    expect(isWatchlistKeyTargetIgnored(document.createElement('button'))).toBe(false);
  });
});

describe('watchlist tree grid rows', () => {
  it('makes sections parent rows of their symbols', () => {
    expect(watchlistGridRows([
      { kind: 'symbol', instrumentId: 'a', itemIndex: 0, sectionId: null },
      { kind: 'section', section: { type: 'section', id: 's', name: 'S', collapsed: false }, itemIndex: 1, symbolCount: 1 },
      { kind: 'symbol', instrumentId: 'b', itemIndex: 2, sectionId: 's' },
    ])).toEqual([
      { key: 'symbol:a', instrumentId: 'a', sectionId: null, collapsed: false, parentKey: null },
      { key: 'section:s', instrumentId: null, sectionId: 's', collapsed: false, parentKey: null },
      { key: 'symbol:b', instrumentId: 'b', sectionId: null, collapsed: false, parentKey: 'section:s' },
    ]);
  });

  it('gives each row its share of the single tab stop', () => {
    expect(watchlistRowTabStop({ rowKey: 'symbol:a', cell: null }, 'symbol:a')).toBeNull();
    expect(watchlistRowTabStop({ rowKey: 'symbol:a', cell: 2 }, 'symbol:a')).toBe(2);
    expect(watchlistRowTabStop({ rowKey: 'symbol:a', cell: 2 }, 'symbol:b')).toBe(false);
  });
});

describe('watchlist selection commands', () => {
  it('starts from the chart symbol and shows each symbol it moves to', () => {
    const first = applyWatchlistCommand(none, order, 'next', 'b');
    expect(first).toEqual({ selection: { ids: ['c'], cursor: 'c', anchor: 'c' }, show: 'c' });
    expect(applyWatchlistCommand(first.selection, order, 'previous').show).toBe('b');
    expect(applyWatchlistCommand(none, order, 'previous').selection.cursor).toBe('d');
    expect(applyWatchlistCommand({ ids: ['d'], cursor: 'd', anchor: 'd' }, order, 'next').selection.cursor).toBe('d');
  });

  it('extends from the anchor in both directions without changing the chart', () => {
    let state = applyWatchlistCommand(none, order, 'next', 'b').selection;
    let result = applyWatchlistCommand(state, order, 'extendNext');
    expect(result).toEqual({ selection: { ids: ['c', 'd'], cursor: 'd', anchor: 'c' }, show: null });
    state = applyWatchlistCommand(result.selection, order, 'extendPrevious').selection;
    result = applyWatchlistCommand(state, order, 'extendPrevious');
    expect(result.selection).toEqual({ ids: ['b', 'c'], cursor: 'b', anchor: 'c' });
  });

  it('selects every visible symbol', () => {
    expect(applyWatchlistCommand({ ids: ['b'], cursor: 'b', anchor: 'b' }, order, 'selectAll')).toEqual({
      selection: { ids: order, cursor: 'b', anchor: 'b' },
      show: null,
    });
    expect(applyWatchlistCommand(none, [], 'next')).toEqual({ selection: none, show: null });
  });

  it('handles plain, Shift and Ctrl clicks', () => {
    const plain = clickWatchlistRow(none, order, 'b', { shiftKey: false, ctrlKey: false, metaKey: false });
    expect(plain.show).toBe('b');
    const range = clickWatchlistRow(plain.selection, order, 'd', { shiftKey: true, ctrlKey: false, metaKey: false });
    expect(range).toEqual({ selection: { ids: ['b', 'c', 'd'], cursor: 'd', anchor: 'b' }, show: null });
    const toggled = clickWatchlistRow(range.selection, order, 'c', { shiftKey: false, ctrlKey: true, metaKey: false });
    expect(toggled.selection.ids).toEqual(['b', 'd']);
  });
});
