import { act, fireEvent, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { takePaperTicketPrefill, usePaperTicketPresence } from '../paperTicketRequests';
import { useTradingCommandDispatcher } from './useTradingCommands';
import { hotkeyPrefill, useTradingOrderHotkeys } from './useTradingOrderHotkeys';

function mount(crosshairPrice: number | null, ticketOpen = true) {
  let listener: ((point: { time: number; price: number } | null) => void) | null = null;
  const adapter = { onCrosshair: (next: typeof listener) => { listener = next; return () => undefined; } } as unknown as TradingChartAdapter;
  const hook = renderHook(() => {
    useTradingCommandDispatcher();
    if (ticketOpen) usePaperTicketPresence();
    useTradingOrderHotkeys({ active: true, adapter, instrumentId: 'crypto:BTC', lastPrice: () => 100 });
  });
  if (crosshairPrice !== null) act(() => listener?.({ time: 0, price: crosshairPrice }));
  return hook;
}

const press = (init: KeyboardEventInit) => act(() => { fireEvent.keyDown(document.body, init); });

afterEach(() => { takePaperTicketPrefill(); });

describe('trading hotkeys (TVP-7.4)', () => {
  it('Shift+B and Shift+S fill a market order ticket', () => {
    const hook = mount(null);
    press({ key: 'B', code: 'KeyB', shiftKey: true });
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'buy', orderType: 'market', entry: null, source: 'hotkey' });
    press({ key: 'S', code: 'KeyS', shiftKey: true });
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'sell', orderType: 'market' });
    hook.unmount();
  });

  it('Shift+Alt+B and Shift+Alt+S fill a limit at the crosshair, else at the last price', () => {
    const atCrosshair = mount(97.5);
    press({ key: 'B', code: 'KeyB', shiftKey: true, altKey: true });
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'buy', orderType: 'limit', entry: 97.5 });
    atCrosshair.unmount();
    const atLast = mount(null);
    press({ key: 'S', code: 'KeyS', shiftKey: true, altKey: true });
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'sell', orderType: 'limit', entry: 100 });
    atLast.unmount();
  });

  it('leaves Shift+letters to symbol typing while no paper ticket is open', () => {
    const hook = mount(null, false);
    press({ key: 'B', code: 'KeyB', shiftKey: true });
    expect(takePaperTicketPrefill()).toBeNull();
    hook.unmount();
  });

  it('never asks for a limit without a price', () => {
    expect(hotkeyPrefill('crypto:BTC', 'buy', 'limit', null)).toBeNull();
    expect(hotkeyPrefill('crypto:BTC', 'buy', 'limit', Number.NaN)).toBeNull();
  });
});
