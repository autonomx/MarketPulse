import { useEffect, useState } from 'react';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';
import { usePaperOrderNotifications } from './paperNotifications';
import type { PaperAccountSnapshot } from './paperTypes';
import { tradingPaperApi } from './tradingPaperApi';
import { useTradingReplayStore } from './tradingReplayStore';
import { useTradingStore } from './tradingStore';

/**
 * Renders nothing. Polls the paper account (the preferred one, else the first) and notices fills, rejections,
 * cancellations and expiries (TVP-7.4), whatever panels are open; in replay it watches the replay account.
 */
export function PaperOrderNotificationsWatch({ accountId }: { accountId: string | null }) {
  const replayMode = useTradingStore((state) => state.replayMode);
  const replaySessionId = useTradingStore((state) => state.replaySessionId);
  const replaySnapshot = useTradingReplayStore((state) => state.snapshot);
  const [live, setLive] = useState<PaperAccountSnapshot | null>(null);
  useEffect(() => {
    if (replayMode) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const id = accountId ?? (await tradingPaperApi.accounts()).find((account) => account.enabled)?.account_id;
        if (!id || cancelled) return;
        const snapshot = await tradingPaperApi.snapshot(id);
        if (!cancelled) setLive(snapshot);
      } catch {
        // The next poll tries again; the dock reports account errors.
      }
    };
    void poll();
    const stop = startPolling(poll, POLL_INTERVALS_MS.paperAccount);
    return () => {
      cancelled = true;
      stop();
    };
  }, [accountId, replayMode]);
  usePaperOrderNotifications(replayMode ? replaySnapshot : live, replayMode ? `replay:${replaySessionId}` : 'live');
  return null;
}
