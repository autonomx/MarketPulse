import { useEffect, useRef, useState } from 'react';
import { playAlertSound } from './alertSounds';
import { useTradingAlerts, useTradingAlertTriggers } from './useTradingAlerts';
import { tradingWindowPresence } from './windowPresence';
import './TradingAlertToastLayer.css';

type Toast = { triggerId: string; title: string; message: string };

function symbol(instrumentId: string): string {
  return instrumentId.split(':').at(-1)?.replace('-', '/') ?? instrumentId;
}

export function TradingAlertToastLayer() {
  const alertsQuery = useTradingAlerts({ poll: true });
  const triggersQuery = useTradingAlertTriggers({ poll: true });
  const seen = useRef<Set<string> | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    if (!triggersQuery.data) return;
    const current = new Set(triggersQuery.data.map((trigger) => trigger.trigger_id));
    if (!seen.current) {
      seen.current = current;
      return;
    }
    const fresh = triggersQuery.data.filter((item) => !seen.current?.has(item.trigger_id));
    seen.current = current;
    // With several Trading windows open, only the most recently focused one rings and shows the toast (TVP-4.3).
    if (!tradingWindowPresence().isAlertWindow()) return;
    const alertOf = (alertId: string) => alertsQuery.data?.find((item) => item.alert_id === alertId);
    const channelsOf = (alertId: string) => alertOf(alertId)?.parameters.notification_channels ?? ['app', 'toast'];
    // The Sound channel plays the alert's sound (chime unless it chose another): once per poll, for the first
    // new trigger that asks for one, so alerts firing together don't stack.
    const sounding = fresh.find((item) => channelsOf(item.alert_id).includes('sound'));
    if (sounding) playAlertSound(alertOf(sounding.alert_id)?.parameters.delivery?.sound?.name);
    const trigger = fresh.find((item) => !alertOf(item.alert_id) || channelsOf(item.alert_id).includes('toast'));
    if (!trigger) return;
    const alert = alertOf(trigger.alert_id);
    // The server fills the message's placeholders when the alert triggers (TVP-1.5).
    const rendered = typeof trigger.payload?.message === 'string' ? trigger.payload.message : '';
    const message = rendered || alert?.parameters.message || `${symbol(trigger.instrument_id)} crossed ${trigger.threshold}`;
    setToast({ triggerId: trigger.trigger_id, title: 'Alert triggered', message });
    const timer = window.setTimeout(() => setToast((currentToast) => currentToast?.triggerId === trigger.trigger_id ? null : currentToast), 6_000);
    return () => window.clearTimeout(timer);
  }, [alertsQuery.data, triggersQuery.data]);

  if (!toast) return null;
  return <div className="trading-alert-toast-layer" role="status" aria-live="polite"><strong>{toast.title}</strong><span>{toast.message}</span><button type="button" onClick={() => setToast(null)} aria-label="Dismiss alert notification">×</button></div>;
}
