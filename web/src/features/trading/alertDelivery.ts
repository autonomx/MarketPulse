// What the alert dialog edits about delivery (TVP-1.5): the alert's name, the
// Sound channel's sound and the webhook. A webhook URL and secret are
// write-only: the dialog shows the stored host and whether a secret is set,
// and sends them only when the user types new ones (the server keeps the
// stored ones otherwise).
import { alertSoundName, soundChange, type AlertSoundName } from './alertSounds';
import type { TradingAlert, TradingAlertNotificationChannel } from './tradingTypes';
import { isDesktopApp } from '../../shared/desktopBridge';

export type AlertDeliveryEditor = {
  /** The alert's name ({{alert_name}}). */
  name?: string;
  /** The Sound channel's sound (chime when unset). */
  sound?: AlertSoundName;
  /** The sound name the alert has stored, which may be one this client doesn't know. */
  storedSound?: string;
  /** A new webhook URL; empty keeps the stored one. */
  webhookUrl?: string;
  /** A new signing secret; empty keeps the stored one. */
  webhookSecret?: string;
  /** The stored webhook's scheme and host, when it has one. */
  webhookDisplay?: string | null;
  webhookHasSecret?: boolean;
};

/** The delivery fields of an alert being edited. */
/**
 * The channels a new alert starts with: in-app and toast, plus the desktop app's notifications inside the desktop
 * shell (MarketPulse S-6).
 */
export function defaultAlertNotifications(): TradingAlertNotificationChannel[] {
  return isDesktopApp() ? ['app', 'toast', 'desktop'] : ['app', 'toast'];
}

export function deliveryEditorOf(alert: TradingAlert): AlertDeliveryEditor {
  const delivery = alert.parameters.delivery;
  return {
    name: alert.parameters.name ?? '',
    sound: alertSoundName(delivery?.sound?.name),
    storedSound: delivery?.sound?.name,
    webhookDisplay: delivery?.webhook?.display_url ?? null,
    webhookHasSecret: delivery?.webhook?.has_secret ?? false,
  };
}

/** The update fields for `chartAlertUpdateInput`. */
export function deliveryUpdatePatch(editor: AlertDeliveryEditor & { notifications: readonly string[] }) {
  // A URL or secret typed and then unticked is not saved: it would be delivered to once the channel is ticked again.
  const webhook = editor.notifications.includes('webhook');
  return {
    name: editor.name?.trim() ?? undefined,
    sound_name: soundChange(editor),
    webhook_url: webhook ? editor.webhookUrl?.trim() || undefined : undefined,
    webhook_secret: webhook ? editor.webhookSecret?.trim() || undefined : undefined,
  };
}

/** The create fields for `chartAlertCreateInput`. */
export function deliveryCreateFields(editor: AlertDeliveryEditor) {
  return {
    name: editor.name?.trim() || undefined,
    soundName: editor.sound ?? 'chime',
    webhookUrl: editor.webhookUrl?.trim() || undefined,
    webhookSecret: editor.webhookSecret?.trim() || undefined,
  };
}

/** The placeholders a message can use, for the dialog's hint. */
export const MESSAGE_PLACEHOLDERS = [
  '{{ticker}}', '{{exchange}}', '{{interval}}', '{{open}}', '{{high}}', '{{low}}', '{{close}}', '{{volume}}',
  '{{time}}', '{{timenow}}', '{{alert_name}}', '{{plot_0}}', '{{plot("rsi:14")}}',
] as const;
