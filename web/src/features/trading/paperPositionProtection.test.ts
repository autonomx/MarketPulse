import { afterEach, describe, expect, it, vi } from 'vitest';

const paperApi = vi.hoisted(() => ({
  protection: vi.fn(),
  setProtection: vi.fn(),
  clearProtection: vi.fn(),
}));

vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));

import { readPaperPositionProtection, writePaperPositionProtection } from './paperPositionProtection';

const trailingLeg = {
  account_id: 'paper-1', instrument_id: 'crypto:BINANCE:spot:SOL-USDT', take_profit: '80', stop_loss: '73.5',
  status: 'active', trail_amount: '1.5', trail_percent: null, trail_water_mark: '75',
};

describe('paperPositionProtection', () => {
  afterEach(() => vi.clearAllMocks());

  it('sends a trailing leg its trail back when the chart moves a level', async () => {
    paperApi.protection.mockResolvedValue(trailingLeg);
    paperApi.setProtection.mockResolvedValue(trailingLeg);
    readPaperPositionProtection('paper-1', trailingLeg.instrument_id);
    await vi.waitFor(() => expect(readPaperPositionProtection('paper-1', trailingLeg.instrument_id).stopLoss).toBe(73.5));

    writePaperPositionProtection('paper-1', trailingLeg.instrument_id, { takeProfit: 82, stopLoss: 73.5 });
    await vi.waitFor(() => expect(paperApi.setProtection).toHaveBeenCalledWith('paper-1', {
      instrument_id: trailingLeg.instrument_id,
      take_profit: '82',
      stop_loss: '73.5',
      trail_amount: '1.5',
      trail_percent: null,
    }));

    // Without a stop loss there is nothing to trail.
    writePaperPositionProtection('paper-1', trailingLeg.instrument_id, { takeProfit: 82, stopLoss: null });
    await vi.waitFor(() => expect(paperApi.setProtection).toHaveBeenLastCalledWith('paper-1', {
      instrument_id: trailingLeg.instrument_id,
      take_profit: '82',
      stop_loss: null,
    }));
  });

  it('sends no trail fields for a fixed stop', async () => {
    const fixed = { ...trailingLeg, instrument_id: 'crypto:BINANCE:spot:ETH-USDT', trail_amount: null, trail_water_mark: null };
    paperApi.protection.mockResolvedValue(fixed);
    paperApi.setProtection.mockResolvedValue(fixed);
    readPaperPositionProtection('paper-1', fixed.instrument_id);
    await vi.waitFor(() => expect(readPaperPositionProtection('paper-1', fixed.instrument_id).stopLoss).toBe(73.5));

    writePaperPositionProtection('paper-1', fixed.instrument_id, { takeProfit: 81, stopLoss: 73 });
    await vi.waitFor(() => expect(paperApi.setProtection).toHaveBeenCalledWith('paper-1', {
      instrument_id: fixed.instrument_id,
      take_profit: '81',
      stop_loss: '73',
    }));
  });
});
