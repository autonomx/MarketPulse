import type { PaperAccountSnapshot, PaperOrder, PaperOrderInput } from './paperTypes';
import { tradingReplayApi, type ReplayExecutionMarketBar } from './tradingReplayApi';

export type ReplayResult = {
  snapshot: PaperAccountSnapshot;
  order: PaperOrder;
};

function finite(value: string | number | null | undefined): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function decimal(value: number): string {
  if (!Number.isFinite(value)) return '0';
  return value.toFixed(12).replace(/\.?(0+)$/, '') || '0';
}

/**
 * Create detached replay state without making any execution decision.
 *
 * Fill/trigger/slippage/liquidity semantics intentionally do not live in the
 * browser anymore. advanceReplaySnapshot/placeReplayOrder delegate to the
 * server paper-execution-v2 kernel.
 */
export function createReplaySnapshot(source: PaperAccountSnapshot): PaperAccountSnapshot {
  return {
    account: { ...source.account },
    balances: source.balances.map((balance) => ({
      ...balance,
      available: decimal(finite(balance.available) + finite(balance.reserved)),
      reserved: '0',
    })),
    positions: source.positions.map((position) => ({ ...position, reserved_quantity: '0' })),
    open_orders: [],
    order_history: [],
    recent_fills: [],
    recent_ledger: [],
    // Replay trades on cash alone (no leverage or margin calls), so it carries no margin status.
    margin_status: null,
  };
}

/** Advance replay through the shared server-authoritative paper execution policy. */
export async function advanceReplaySnapshot(
  source: PaperAccountSnapshot,
  bar: ReplayExecutionMarketBar,
): Promise<PaperAccountSnapshot> {
  return tradingReplayApi.advanceExecution(source, bar);
}

/**
 * Place a detached replay order through the shared server execution kernel.
 * Pass `advanceBar: false` when `source` has already been advanced through
 * `bar`, so working orders are not filled against it twice.
 */
export async function placeReplayOrder(
  source: PaperAccountSnapshot,
  input: PaperOrderInput,
  bar: ReplayExecutionMarketBar,
  { advanceBar = true }: { advanceBar?: boolean } = {},
): Promise<ReplayResult> {
  return tradingReplayApi.placeExecutionOrder(source, input, bar, advanceBar);
}
