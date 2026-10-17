import { tradingPaperApi } from './tradingPaperApi';
import { emitOmnixEvent, PAPER_POSITION_PROTECTION_CHANGED_EVENT } from '../../events/bus';

/** Take-profit and stop-loss prices the chart overlay draws for a paper position. */
export type PositionProtectionLevels = {
  takeProfit: number | null;
  stopLoss: number | null;
};


type TrailSettings = { trail_amount: string | null; trail_percent: string | null };

const cache = new Map<string, PositionProtectionLevels>();
// A trailing stop-loss leg's trail (TVP-7.1). The chart edits only levels, so
// it sends the trail back unchanged; omitting it would turn trailing off.
const trails = new Map<string, TrailSettings>();
const inflight = new Set<string>();

function entryKey(accountId: string, instrumentId: string): string {
  return `${accountId}:${instrumentId}`;
}

function validPrice(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function normalized(value: PositionProtectionLevels): PositionProtectionLevels {
  return { takeProfit: validPrice(value.takeProfit), stopLoss: validPrice(value.stopLoss) };
}

function equal(left: PositionProtectionLevels, right: PositionProtectionLevels): boolean {
  return left.takeProfit === right.takeProfit && left.stopLoss === right.stopLoss;
}

function notify(accountId: string, instrumentId: string): void {
  if (typeof window === 'undefined') return;
  emitOmnixEvent(PAPER_POSITION_PROTECTION_CHANGED_EVENT, { accountId, instrumentId });
}

async function hydrate(accountId: string, instrumentId: string): Promise<void> {
  const key = entryKey(accountId, instrumentId);
  if (inflight.has(key)) return;
  inflight.add(key);
  try {
    const value = await tradingPaperApi.protection(accountId, instrumentId);
    const next: PositionProtectionLevels = value
      ? { takeProfit: validPrice(value.take_profit), stopLoss: validPrice(value.stop_loss) }
      : { takeProfit: null, stopLoss: null };
    const previous = cache.get(key) ?? { takeProfit: null, stopLoss: null };
    cache.set(key, next);
    if (value && (value.trail_amount || value.trail_percent)) {
      trails.set(key, { trail_amount: value.trail_amount ?? null, trail_percent: value.trail_percent ?? null });
    } else {
      trails.delete(key);
    }
    if (!equal(previous, next)) notify(accountId, instrumentId);
  } catch {
    // Keep the last rendered cache value. The server remains authority and the
    // next refresh will reconcile it; no browser persistence is used.
  } finally {
    inflight.delete(key);
  }
}

export function readPaperPositionProtection(accountId: string, instrumentId: string): PositionProtectionLevels {
  if (typeof window !== 'undefined') void hydrate(accountId, instrumentId);
  return cache.get(entryKey(accountId, instrumentId)) ?? { takeProfit: null, stopLoss: null };
}

export function writePaperPositionProtection(
  accountId: string,
  instrumentId: string,
  protection: PositionProtectionLevels,
): void {
  const next = normalized(protection);
  cache.set(entryKey(accountId, instrumentId), next);
  notify(accountId, instrumentId);
  if (typeof window === 'undefined') return;

  const persist = async () => {
    if (next.takeProfit === null && next.stopLoss === null) {
      try {
        await tradingPaperApi.clearProtection(accountId, instrumentId);
      } catch {
        // Clearing an already absent row is effectively idempotent for the UI.
      }
    } else {
      // A trail needs a stop loss; without one the leg stops trailing.
      const trail = next.stopLoss === null ? undefined : trails.get(entryKey(accountId, instrumentId));
      await tradingPaperApi.setProtection(accountId, {
        instrument_id: instrumentId,
        take_profit: next.takeProfit === null ? null : String(next.takeProfit),
        stop_loss: next.stopLoss === null ? null : String(next.stopLoss),
        ...(trail ?? {}),
      });
    }
    await hydrate(accountId, instrumentId);
  };
  void persist().catch(() => void hydrate(accountId, instrumentId));
}
