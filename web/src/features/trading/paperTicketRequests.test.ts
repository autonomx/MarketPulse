import { describe, expect, it, vi } from 'vitest';
import { dispatchDrawingActionRequest } from './drawings/drawingActions';
import { PREFILL_LIFETIME_MS, applyPaperTicketPrefill, parsePaperTicketRequest, requestPaperTicket, takePaperTicketPrefill, type PaperTicketForm } from './paperTicketRequests';
import { renderHook } from '@testing-library/react';
import { usePaperTicketRequests } from './paperTicketRequests';

function form(): PaperTicketForm & Record<string, ReturnType<typeof vi.fn>> {
  const names = ['setTicketTab', 'setSide', 'setOrderType', 'setTriggerPrice', 'setLimitPrice', 'setStopLossEnabled', 'setStopLoss', 'setTakeProfitEnabled', 'setTakeProfit', 'setQuantity'];
  return Object.fromEntries(names.map((name) => [name, vi.fn()])) as unknown as PaperTicketForm & Record<string, ReturnType<typeof vi.fn>>;
}

describe('order ticket from a position drawing (TVP-3.6)', () => {
  it('a risk-managed buy: limit price at the entry, stop and target on, quantity left to the risk rule', () => {
    const fields = form();
    const prefill = { instrumentId: 'crypto:BTC', side: 'buy' as const, orderType: 'limit' as const, entry: 100, stop: 95, target: 110, quantity: 4 };
    expect(applyPaperTicketPrefill(prefill, 'crypto:BTC', fields, { riskManaged: true, riskPercent: '0.35' }).message).toContain('0.35% risk rule');
    // The panel's "Limit price" field (and the order's limit_price) is the trigger price for a limit order.
    expect(fields.setTriggerPrice).toHaveBeenCalledWith('100');
    expect(fields.setLimitPrice).toHaveBeenCalledWith('');
    expect(fields.setStopLossEnabled).toHaveBeenCalledWith(true);
    expect(fields.setStopLoss).toHaveBeenCalledWith('95');
    expect(fields.setTakeProfit).toHaveBeenCalledWith('110');
    expect(fields.setQuantity).not.toHaveBeenCalled();
  });

  it('a sell (or any replay order) gets the quantity and no protection it could not send', () => {
    const fields = form();
    const prefill = { instrumentId: 'crypto:BTC', side: 'sell' as const, orderType: 'limit' as const, entry: 100, stop: 105, target: 90, quantity: 1.234567891 };
    expect(applyPaperTicketPrefill(prefill, 'crypto:BTC', fields, { riskManaged: false, riskPercent: '0.35' }).message).toContain('turn shorting on in the account settings');
    expect(fields.setSide).toHaveBeenCalledWith('sell');
    expect(fields.setTriggerPrice).toHaveBeenCalledWith('100');
    expect(fields.setStopLossEnabled).toHaveBeenCalledWith(false);
    expect(fields.setTakeProfitEnabled).toHaveBeenCalledWith(false);
    expect(fields.setQuantity).toHaveBeenCalledWith('1.23457');
    const other = form();
    expect(applyPaperTicketPrefill(prefill, 'crypto:ETH', other, { riskManaged: false, riskPercent: '1' }, () => 'BTC'))
      .toEqual({ kind: 'error', message: 'The order is for BTC; open that chart to trade it.' });
    expect(other.setSide).not.toHaveBeenCalled();
  });

  it('a stop order from the chart fills the stop price (TVP-7.3)', () => {
    const fields = form();
    const prefill = parsePaperTicketRequest({ instrumentId: 'crypto:BTC', side: 'buy', orderType: 'stop', entry: 120 });
    expect(prefill).toMatchObject({ orderType: 'stop', entry: 120 });
    expect(parsePaperTicketRequest({ instrumentId: 'crypto:BTC', side: 'buy', orderType: 'stop' })).toBeNull();
    const result = applyPaperTicketPrefill({ ...prefill!, source: 'chart' }, 'crypto:BTC', fields, { riskManaged: true, riskPercent: '0.35' });
    expect(result.message).toContain('Ticket filled from the chart.');
    expect(fields.setOrderType).toHaveBeenCalledWith('stop');
    expect(fields.setTriggerPrice).toHaveBeenCalledWith('120');
  });

  it('drops a pre-fill nobody took in time', () => {
    requestPaperTicket({ instrumentId: 'crypto:BTC', side: 'buy', orderType: 'limit' as const, entry: 100, stop: null, target: null, quantity: null }, 0);
    expect(takePaperTicketPrefill(PREFILL_LIFETIME_MS + 1)).toBeNull();
  });

  it('the workspace opens the paper panel and leaves the pre-fill for it', () => {
    const open = vi.fn();
    const hook = renderHook(() => usePaperTicketRequests(open));
    dispatchDrawingActionRequest({ type: 'order-ticket', payload: { instrumentId: 'crypto:BTC', side: 'buy', orderType: 'limit' as const, entry: 100, stop: 95, target: 110, quantity: 4 } });
    expect(open).toHaveBeenCalledTimes(1);
    expect(takePaperTicketPrefill()).toMatchObject({ entry: 100, quantity: 4 });
    expect(takePaperTicketPrefill()).toBeNull();
    // Malformed requests are ignored.
    dispatchDrawingActionRequest({ type: 'order-ticket', payload: { side: 'buy' } });
    expect(open).toHaveBeenCalledTimes(1);
    hook.unmount();
  });
});
