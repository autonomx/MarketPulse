import { AlertEmailSetup, AlertPushSetup } from './AlertChannelSetup';
import type { AlertDeliveryEditor } from './alertDelivery';
import { ALERT_SOUNDS, playAlertSound, type AlertSoundName } from './alertSounds';
import type { TradingAlertNotificationChannel } from './tradingTypes';
import { isDesktopApp } from '../../shared/desktopBridge';

const CHANNELS: Array<{ value: TradingAlertNotificationChannel; label: string }> = [
  { value: 'app', label: 'App' },
  { value: 'toast', label: 'Toasts' },
  { value: 'sound', label: 'Sound' },
  { value: 'webhook', label: 'Webhook' },
  { value: 'email', label: 'Email' },
  { value: 'push', label: 'Push' },
];

// Inside the desktop shell the app's own notifications replace web push (MarketPulse S-6).
const DESKTOP_CHANNELS: Array<{ value: TradingAlertNotificationChannel; label: string }> = [
  ...CHANNELS.filter((channel) => channel.value !== 'push'),
  { value: 'desktop', label: 'Desktop' },
];

/**
 * The alert's channels (TVP-1.5): in-app, toast, sound with its sound, webhook with its URL and optional signing
 * secret, and email and push (TVP-0.5b/c) with their setup. The stored URL and secret are never shown; typing new ones
 * replaces them.
 */
export function AlertDeliveryFields({
  editor, onChange,
}: {
  editor: AlertDeliveryEditor & { notifications: TradingAlertNotificationChannel[] };
  onChange: (patch: Partial<AlertDeliveryEditor> & { notifications?: TradingAlertNotificationChannel[] }) => void;
}) {
  const toggle = (channel: TradingAlertNotificationChannel) => onChange({
    notifications: editor.notifications.includes(channel)
      ? editor.notifications.filter((item) => item !== channel)
      : [...editor.notifications, channel],
  });
  const stored = editor.webhookDisplay ?? null;
  return (
    <>
      {(isDesktopApp() ? DESKTOP_CHANNELS : CHANNELS).map((option) => <label key={option.value}><input type="checkbox" checked={editor.notifications.includes(option.value)} onChange={() => toggle(option.value)} />{option.label}</label>)}
      {editor.notifications.includes('sound') ? (
        <select aria-label="Alert sound" value={editor.sound ?? 'chime'} onChange={(event) => { const sound = event.target.value as AlertSoundName; playAlertSound(sound); onChange({ sound }); }}>
          {ALERT_SOUNDS.map((sound) => <option key={sound} value={sound}>{sound[0].toUpperCase() + sound.slice(1)}</option>)}
        </select>
      ) : null}
      {editor.notifications.includes('webhook') ? (
        <span className="trading-alert-webhook">
          <input
            aria-label="Webhook URL"
            type="url"
            inputMode="url"
            autoComplete="off"
            required={!stored}
            maxLength={2000}
            placeholder={stored ? `${stored} (saved; type to replace)` : 'https://…'}
            value={editor.webhookUrl ?? ''}
            onChange={(event) => onChange({ webhookUrl: event.target.value })}
          />
          <input
            aria-label="Webhook signing secret"
            type="password"
            autoComplete="new-password"
            maxLength={500}
            placeholder={editor.webhookHasSecret ? 'Secret saved; type to replace' : 'Signing secret (optional)'}
            value={editor.webhookSecret ?? ''}
            onChange={(event) => onChange({ webhookSecret: event.target.value })}
          />
        </span>
      ) : null}
      {editor.notifications.includes('email') ? <AlertEmailSetup /> : null}
      {editor.notifications.includes('push') ? <AlertPushSetup /> : null}
    </>
  );
}
