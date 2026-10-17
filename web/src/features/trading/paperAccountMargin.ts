/**
 * The paper account dialog's leverage and commission settings, as the server holds them (TVP-7.2b).
 *
 * The dialog shows TradingView's leverage ratios per market; the server holds a margin percentage per asset class:
 * N:1 is 100/N %, for longs and shorts alike. Leverage applies with "Margin control" on; off, every position is held
 * in full (1:1), as accounts were before. Futures map to the commodity class; "Others" has no class and stays 1:1.
 */
import type { components } from './api/generated';
import type { PaperAccount } from './paperTypes';

type MarginInput = NonNullable<components['schemas']['PaperAccountSettings']['margin']>;

export type LeverageSettings = { stocks: string; futures: string; forex: string; crypto: string; others: string };
export type CommissionSettings = { othersCommission: boolean; commission: string; commissionType: 'Percent' | 'Fixed' };

/** The dialog's markets and the server's asset classes. */
export const LEVERAGE_CLASSES = { stocks: 'equity', futures: 'commodity', forex: 'forex', crypto: 'crypto' } as const;

function ratio(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

/** The margin per asset class the leverage selects ask for: none with margin control off, or at 1:1. */
export function marginFromLeverage(marginControl: boolean, leverage: LeverageSettings): MarginInput {
  if (!marginControl) return {};
  const margin: MarginInput = {};
  for (const [market, assetClass] of Object.entries(LEVERAGE_CLASSES) as Array<[keyof typeof LEVERAGE_CLASSES, string]>) {
    const n = ratio(leverage[market]);
    if (n <= 1) continue;
    const pct = String(Math.round((100 / n) * 1_000) / 1_000);
    margin[assetClass] = { long_pct: pct, short_pct: pct };
  }
  return margin;
}

/** The leverage selects for an account's margin: N:1 from its long margin %, 1:1 where none is set. */
export function leverageFromAccount(account: PaperAccount | null | undefined, fallback: LeverageSettings): { marginControl: boolean; leverage: LeverageSettings } {
  const margin = (account?.margin ?? {}) as Record<string, { long_pct: string | number }>;
  // "Others" has no asset class on the server: always 1:1.
  if (!account || Object.keys(margin).length === 0) return { marginControl: false, leverage: { ...fallback, others: '1:1' } };
  const leverage = { ...fallback, others: '1:1' };
  for (const [market, assetClass] of Object.entries(LEVERAGE_CLASSES) as Array<[keyof typeof LEVERAGE_CLASSES, string]>) {
    const pct = Number(margin[assetClass]?.long_pct ?? 100);
    leverage[market] = `${Math.round(100 / pct)}:1`;
  }
  return { marginControl: true, leverage };
}

/** The commission the dialog asks for: a percentage (as bps) or a fixed amount per order; none when unticked. */
export function commissionInput(settings: CommissionSettings): { commission_type: 'percent' | 'fixed_per_order'; commission_bps: string; commission_fixed: string } {
  const value = Number(settings.commission);
  const amount = settings.othersCommission && Number.isFinite(value) && value > 0 ? value : 0;
  return settings.commissionType === 'Fixed'
    ? { commission_type: 'fixed_per_order', commission_bps: '0', commission_fixed: String(amount) }
    : { commission_type: 'percent', commission_bps: String(Number((amount * 100).toFixed(8))), commission_fixed: '0' };
}

/** The dialog's commission for an account. */
export function commissionFromAccount(account: PaperAccount): CommissionSettings {
  if (account.commission_type === 'fixed_per_order') {
    const fixed = Number(account.commission_fixed ?? 0);
    return { othersCommission: fixed > 0, commission: String(fixed), commissionType: 'Fixed' };
  }
  const percent = Number(account.commission_bps) / 100;
  return { othersCommission: percent > 0, commission: percent > 0 ? String(percent) : '0.005', commissionType: 'Percent' };
}

/** Whether an order was placed by a margin call (the paper monitor's ids carry this prefix). */
export function isMarginCallOrder(orderId: string): boolean {
  return orderId.startsWith('paper-margin-call-');
}

/** The margin share (1 = none borrowed) an account holds on an instrument's longs: its asset class's, else 1. */
export function longMarginFraction(account: PaperAccount | null | undefined, instrumentId: string): number {
  const margin = (account?.margin ?? {}) as Record<string, { long_pct: string | number }>;
  const pct = Number(margin[instrumentId.split(':', 1)[0].toLowerCase()]?.long_pct ?? 100);
  return Number.isFinite(pct) && pct > 0 ? pct / 100 : 1;
}
