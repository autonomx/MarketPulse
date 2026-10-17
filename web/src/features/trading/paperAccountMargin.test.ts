import { describe, expect, it } from 'vitest';
import { fixture } from '../../test/fixture';
import { commissionFromAccount, commissionInput, isMarginCallOrder, leverageFromAccount, longMarginFraction, marginFromLeverage } from './paperAccountMargin';
import type { PaperAccount } from './paperTypes';

const leverage = { stocks: '2:1', futures: '1:1', forex: '50:1', crypto: '500:1', others: '20:1' };
const account = (fields: Partial<PaperAccount>): PaperAccount => fixture({ account_id: 'a', name: 'A', base_currency: 'USD', commission_bps: '0', enabled: true, revision: 1, ...fields });

describe('paper leverage and commission settings (TVP-7.2b)', () => {
  it('asks for margin per asset class from the leverage ratios, only with margin control on', () => {
    expect(marginFromLeverage(false, leverage)).toEqual({});
    expect(marginFromLeverage(true, leverage)).toEqual({
      equity: { long_pct: '50', short_pct: '50' },
      forex: { long_pct: '2', short_pct: '2' },
      crypto: { long_pct: '0.2', short_pct: '0.2' },
    });
  });

  it('reads the ratios back from the account, and 1:1 without margin', () => {
    const leveraged = account({ margin: { equity: { long_pct: '50', short_pct: '50' }, crypto: { long_pct: '0.2', short_pct: '0.2' } } as never });
    expect(leverageFromAccount(leveraged, leverage)).toEqual({ marginControl: true, leverage: { ...leverage, stocks: '2:1', futures: '1:1', forex: '1:1', crypto: '500:1', others: '1:1' } });
    expect(leverageFromAccount(account({ margin: {} as never }), leverage)).toEqual({ marginControl: false, leverage: { ...leverage, others: '1:1' } });
    expect(commissionInput({ othersCommission: true, commission: '0.07', commissionType: 'Percent' }).commission_bps).toBe('7');
    expect(longMarginFraction(leveraged, 'equity:NASDAQ:AAPL')).toBe(0.5);
    expect(longMarginFraction(leveraged, 'forex:EUR-USD')).toBe(1);
  });

  it('sends a percentage as bps or a fixed amount per order', () => {
    expect(commissionInput({ othersCommission: true, commission: '0.1', commissionType: 'Percent' })).toEqual({ commission_type: 'percent', commission_bps: '10', commission_fixed: '0' });
    expect(commissionInput({ othersCommission: true, commission: '2.5', commissionType: 'Fixed' })).toEqual({ commission_type: 'fixed_per_order', commission_bps: '0', commission_fixed: '2.5' });
    expect(commissionInput({ othersCommission: false, commission: '2.5', commissionType: 'Fixed' }).commission_fixed).toBe('0');
    expect(commissionFromAccount(account({ commission_type: 'fixed_per_order', commission_fixed: '2.5' }))).toEqual({ othersCommission: true, commission: '2.5', commissionType: 'Fixed' });
    expect(commissionFromAccount(account({ commission_bps: '10' }))).toEqual({ othersCommission: true, commission: '0.1', commissionType: 'Percent' });
  });

  it('recognises margin call orders', () => {
    expect(isMarginCallOrder('paper-margin-call-abc')).toBe(true);
    expect(isMarginCallOrder('paper-protection-abc')).toBe(false);
  });
});
