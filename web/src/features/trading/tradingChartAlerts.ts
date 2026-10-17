import type {
  TradingAlert,
  TradingAlertCondition,
  TradingAlertConditionSpec,
  TradingAlertCreateInput,
  TradingAlertFrequency,
  TradingAlertNotificationChannel,
  TradingAlertParameters,
  TradingAlertTriggerPolicy,
  TradingAlertUpdateInput,
} from './tradingTypes';
import { emitOmnixEvent, TRADING_ALERTS_CHANGED_EVENT } from '../../events/bus';
import { isAppearsCondition } from './alertIndicatorSources';

export const TRADING_ALERT_TRIGGER_HIGHLIGHT_MS = 15_000;

export type TradingChartAlertState = 'active' | 'triggered' | 'disabled' | 'expired';
export type TradingAlertExpiration = 'never' | '1h' | '1d' | '1w';
export type { TradingAlertNotificationChannel, TradingAlertTriggerPolicy };

export function notifyTradingAlertsChanged(): void {
  emitOmnixEvent(TRADING_ALERTS_CHANGED_EVENT);
}

export function alertVisualState(alert: TradingAlert, now = Date.now()): TradingChartAlertState {
  if (alert.expires_at && Date.parse(alert.expires_at) <= now) return 'expired';
  if (!alert.enabled) return 'disabled';
  if (
    alert.last_triggered_at
    && now - Date.parse(alert.last_triggered_at) >= 0
    && now - Date.parse(alert.last_triggered_at) <= TRADING_ALERT_TRIGGER_HIGHLIGHT_MS
  ) return 'triggered';
  return 'active';
}

export function alertLastTriggeredLabel(alert: TradingAlert): string | null {
  return alert.last_triggered_at ? new Date(alert.last_triggered_at).toLocaleString() : null;
}

export function expirationTimestamp(expiration: TradingAlertExpiration, now = Date.now()): string | null {
  if (expiration === 'never') return null;
  const milliseconds = expiration === '1h'
    ? 60 * 60 * 1_000
    : expiration === '1d'
      ? 24 * 60 * 60 * 1_000
      : 7 * 24 * 60 * 60 * 1_000;
  return new Date(now + milliseconds).toISOString();
}

export function priceConditionForThreshold(threshold: number, latestPrice: number): TradingAlertCondition {
  return threshold >= latestPrice ? 'price_above' : 'price_below';
}

export function formatAlertThreshold(value: number | string): string {
  if (typeof value === 'string' && value.trim() === '') return '';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : String(value);
}

/** The alert's frequency; older responses only carried parameters.trigger_policy. */
export function alertFrequency(alert: TradingAlert): TradingAlertFrequency {
  return (alert.frequency ?? alert.parameters.trigger_policy ?? 'every_time') as TradingAlertFrequency;
}

const operatorLabels: Record<string, string> = {
  crossing: 'crossing',
  crossing_up: 'crossing up',
  crossing_down: 'crossing down',
  greater_than: 'greater than',
  less_than: 'less than',
  entering_channel: 'entering channel',
  exiting_channel: 'exiting channel',
  inside_channel: 'inside channel',
  outside_channel: 'outside channel',
  moving_up: 'moving up',
  moving_down: 'moving down',
  moving_up_percent: 'moving up %',
  moving_down_percent: 'moving down %',
};

type ConditionPart = TradingAlertConditionSpec['source'] | NonNullable<TradingAlertConditionSpec['target']>;

function conditionPartLabel(part: ConditionPart): string {
  switch (part.kind) {
    case 'price': return part.field === 'close' ? 'Price' : part.field.toUpperCase();
    case 'change_percent': return `Change % (${part.lookback_bars})`;
    case 'indicator': return part.output;
    case 'trendline': return 'Trendline';
    // TVP-11.4: a script's plot (numbered from 1), alertcondition() or alert() calls.
    case 'script': return part.output === 'alert' ? 'Script alert()' : part.output.startsWith('alertcondition:') ? 'Script alertcondition()' : `Script plot ${Number(part.output.split(':')[1]) + 1}`;
    case 'value': return formatAlertThreshold(part.value);
    case 'source': return conditionPartLabel(part.source);
    case 'channel': return `${conditionPartLabel(part.lower)} – ${conditionPartLabel(part.upper)}`;
    default: return '';
  }
}

/** A one-line description of an alert's conditions, e.g. "Price crossing up 100.00 and rsi:14 greater than 70.00". */
export function alertConditionsSummary(alert: Pick<TradingAlert, 'conditions'>): string {
  return (alert.conditions ?? []).map((condition) => {
    if (isAppearsCondition(condition)) return `${conditionPartLabel(condition.source)} ${condition.source.kind === 'script' ? 'fires' : 'appears'}`;
    const operator = operatorLabels[condition.operator] ?? condition.operator;
    const tail = condition.target
      ? conditionPartLabel(condition.target)
      : `${condition.amount ?? ''}${condition.operator.endsWith('_percent') ? '%' : ''} in ${condition.bars ?? 1} bars`;
    return `${conditionPartLabel(condition.source)} ${operator} ${tail}`;
  }).join(' and ');
}

/** The delivery settings, only when there are some (an alert without any sends none). */
function withDelivery(delivery: NonNullable<TradingAlertParameters['delivery']>): { delivery?: TradingAlertParameters['delivery'] } {
  return Object.keys(delivery).length > 0 ? { delivery } : {};
}

export function chartAlertCreateInput(input: {
  alertId: string;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  threshold: number;
  latestPrice: number;
  condition?: 'price_above' | 'price_below';
  expiration: TradingAlertExpiration;
  triggerPolicy?: TradingAlertTriggerPolicy;
  message?: string;
  notificationChannels?: TradingAlertNotificationChannel[];
  /** The sound the Sound channel plays (sent only with that channel). */
  soundName?: string;
  /** The alert's name ({{alert_name}}). */
  name?: string;
  /** The webhook (sent only with the Webhook channel); the URL and secret are write-only. */
  webhookUrl?: string;
  webhookSecret?: string;
  now?: number;
}): TradingAlertCreateInput {
  const triggerPolicy = input.triggerPolicy ?? 'every_time';
  return {
    alert_id: input.alertId,
    instrument_id: input.instrumentId,
    binding_id: input.bindingId,
    condition_type: input.condition ?? priceConditionForThreshold(input.threshold, input.latestPrice),
    threshold: String(input.threshold),
    parameters: {
      lookback_bars: 1,
      indicator_id: null,
      period: 14,
      fast_period: 12,
      slow_period: 26,
      signal_period: 9,
      component: 'value',
      anchor_bars_ago: 0,
      message: input.message ?? '',
      notification_channels: input.notificationChannels ?? ['app', 'toast'],
      trigger_policy: triggerPolicy,
      ...(input.name ? { name: input.name } : {}),
      ...withDelivery({
        ...(input.soundName && input.notificationChannels?.includes('sound') ? { sound: { name: input.soundName } } : {}),
        ...(input.webhookUrl && input.notificationChannels?.includes('webhook') ? { webhook: { url: input.webhookUrl } } : {}),
      }),
    },
    ...(input.webhookSecret && input.webhookUrl && input.notificationChannels?.includes('webhook') ? { webhook_secret: input.webhookSecret } : {}),
    evaluation_policy: {
      interval: input.interval,
      // Mirrors the server, which derives it: only "once per bar close" waits for closed bars.
      allow_partial_bars: triggerPolicy !== 'once_per_bar_close',
      formula_version: 'omnix-indicators-v2',
    },
    frequency: triggerPolicy,
    cooldown_seconds: 0,
    expires_at: expirationTimestamp(input.expiration, input.now),
  };
}

export function chartAlertUpdateInput(
  alert: TradingAlert,
  patch: Partial<Pick<TradingAlertUpdateInput, 'threshold' | 'condition_type' | 'enabled' | 'expires_at'>> & {
    indicator_id?: TradingAlertParameters['indicator_id'];
    period?: number;
    lookback_bars?: number;
    trigger_policy?: TradingAlertTriggerPolicy;
    message?: string;
    notification_channels?: TradingAlertNotificationChannel[];
    /** The sound the Sound channel plays; the other delivery settings are kept. */
    sound_name?: string;
    name?: string;
    /** A new webhook URL or secret; without them the stored webhook is kept. */
    webhook_url?: string;
    webhook_secret?: string;
  },
): TradingAlertUpdateInput {
  const triggerPolicy = patch.trigger_policy ?? alertFrequency(alert);
  const conditionType = patch.condition_type ?? alert.condition_type;
  return {
    instrument_id: alert.instrument_id,
    binding_id: alert.binding_id ?? null,
    condition_type: conditionType,
    threshold: patch.threshold ?? alert.threshold,
    // Alerts described by conditions keep them; legacy alerts let the server derive them.
    ...(conditionType === 'conditions' ? { conditions: alert.conditions } : {}),
    parameters: {
      ...alert.parameters,
      ...(patch.indicator_id !== undefined ? { indicator_id: patch.indicator_id } : {}),
      ...(patch.period !== undefined ? { period: patch.period } : {}),
      ...(patch.lookback_bars !== undefined ? { lookback_bars: patch.lookback_bars } : {}),
      ...(patch.message !== undefined || alert.parameters.message !== undefined
        ? { message: patch.message ?? alert.parameters.message ?? '' }
        : {}),
      ...(patch.notification_channels !== undefined || alert.parameters.notification_channels !== undefined
        ? { notification_channels: patch.notification_channels ?? alert.parameters.notification_channels ?? ['app', 'toast'] }
        : {}),
      ...(patch.trigger_policy !== undefined || alert.parameters.trigger_policy !== undefined
        ? { trigger_policy: triggerPolicy }
        : {}),
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...withDelivery({
        ...alert.parameters.delivery,
        ...(patch.sound_name !== undefined ? { sound: { name: patch.sound_name } } : {}),
        ...(patch.webhook_url !== undefined ? { webhook: { url: patch.webhook_url } } : {}),
      }),
    },
    // A secret alone updates the stored webhook's secret (the server keeps its URL).
    ...(patch.webhook_secret !== undefined && (patch.webhook_url !== undefined || alert.parameters.delivery?.webhook)
      ? { webhook_secret: patch.webhook_secret }
      : {}),
    evaluation_policy: { ...alert.evaluation_policy, allow_partial_bars: triggerPolicy !== 'once_per_bar_close' },
    enabled: patch.enabled ?? alert.enabled,
    frequency: triggerPolicy,
    // The server enforces the frequency; a cooldown is only an extra, legacy limit.
    cooldown_seconds: patch.trigger_policy ? 0 : alert.cooldown_seconds,
    expires_at: patch.expires_at === undefined ? alert.expires_at ?? null : patch.expires_at,
  };
}
