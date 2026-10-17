import { useEffect, useState } from 'react';
import { usePaperNotifications, type PaperNotification } from './paperNotifications';
import './TradingAlertToastLayer.css';

const TITLES: Record<PaperNotification['kind'], string> = {
  fill: 'Order filled', partial: 'Order partly filled', reject: 'Order rejected', cancel: 'Order cancelled', modify: 'Order modified', expire: 'Order expired', closed: 'Order closed',
};

/** The newest paper trading notification over the chart for a few seconds (TVP-7.4); the dock keeps the log. */
export function TradingOrderToastLayer() {
  const notifications = usePaperNotifications();
  const latest = notifications[0] ?? null;
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [expired, setExpired] = useState<string | null>(null);
  useEffect(() => {
    if (!latest) return;
    const timer = window.setTimeout(() => setExpired(latest.id), 6_000);
    return () => window.clearTimeout(timer);
  }, [latest]);
  if (!latest || latest.id === dismissed || latest.id === expired) return null;
  return (
    <div className={`trading-alert-toast-layer trading-order-toast ${latest.kind}`} role={latest.kind === 'reject' ? 'alert' : 'status'} aria-live="polite">
      <strong>{TITLES[latest.kind]}</strong>
      <span>{latest.message}</span>
      <button type="button" onClick={() => setDismissed(latest.id)} aria-label="Dismiss order notification">×</button>
    </div>
  );
}
