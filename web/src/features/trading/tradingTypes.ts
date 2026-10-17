import type { components } from './api/generated';

type RequiredField<T, K extends keyof T> = T & Required<Pick<T, K>>;

export type AssetClass = components['schemas']['AssetClass'];
export type InstrumentType = components['schemas']['InstrumentType'];
/** `name` is set only where the symbol alone says little (an economic series' title, TVP-10.5). */
export type CanonicalInstrument = components['schemas']['CanonicalInstrument'];
export type ProviderPolicy = components['schemas']['ProviderPolicy'];
export type ProviderBinding = components['schemas']['ProviderBinding'];
export type ProviderDescriptor = components['schemas']['ProviderDescriptor'];
export type MarketBar = RequiredField<components['schemas']['MarketBar-Output'], 'received_at'>;
export type DatasetProvenance = components['schemas']['DatasetProvenance'];
export type BarsResponse = components['schemas']['BarsResponse'];

export type TradingStreamMessage =
  | {
      type: 'bar';
      bar: Omit<MarketBar, 'adjustment_mode' | 'session' | 'provider' | 'received_at'> & {
        binding_id: string;
      };
    }
  | { type: 'error'; code: string; message: string };

export type TradingDocument = components['schemas']['TradingDocumentResponse'];
export type TradingAlertCondition = components['schemas']['TradingAlert']['condition_type'];
// Every channel the server knows; it rejects webhook, email and push until their senders ship, and the dialog offers only the rest.
export type TradingAlertNotificationChannel = NonNullable<components['schemas']['TradingAlertParameters-Input']['notification_channels']>[number];
// The alert's server-enforced frequency (TVP-1.1); parameters.trigger_policy mirrors it.
export type TradingAlertTriggerPolicy = 'once' | 'once_per_bar' | 'once_per_bar_close' | 'once_per_minute' | 'every_time';
export type TradingAlertFrequency = TradingAlertTriggerPolicy;
// What the UI sends; alerts it reads carry every parameter (TradingAlertParameters-Output).
export type TradingAlertParameters = Omit<components['schemas']['TradingAlertParameters-Input'], 'message' | 'trigger_policy'> & {
  message?: string;
  notification_channels?: TradingAlertNotificationChannel[];
  trigger_policy?: TradingAlertTriggerPolicy;
};
export type TradingAlertIndicatorId = NonNullable<TradingAlertParameters['indicator_id']>;
export type TradingAlertEvaluationPolicy = components['schemas']['TradingAlertEvaluationPolicy'];
export type TradingAlert = components['schemas']['TradingAlert'];
export type TradingAlertConditionSpec = TradingAlert['conditions'][number];
export type TradingAlertTrigger = components['schemas']['TradingAlertTrigger'];
export type TradingAlertCreateInput = Omit<components['schemas']['TradingAlertCreate'], 'parameters' | 'evaluation_policy'> & {
  parameters: TradingAlertParameters;
  evaluation_policy: components['schemas']['TradingAlertEvaluationPolicy-Input'];
};
export type TradingAlertUpdateInput = Omit<components['schemas']['TradingAlertUpdate'], 'parameters' | 'evaluation_policy'> & {
  parameters: TradingAlertParameters;
  evaluation_policy: components['schemas']['TradingAlertEvaluationPolicy-Input'];
};
