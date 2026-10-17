import { describe, expect, it } from 'vitest';
import { deliveryCreateFields, deliveryEditorOf, deliveryUpdatePatch } from './alertDelivery';
import type { TradingAlert } from './tradingTypes';

const alert = {
  parameters: { name: 'Breakout', delivery: { sound: { name: 'siren' }, webhook: { display_url: 'https://hooks.example', has_secret: true } } },
} as unknown as TradingAlert;

describe('alert delivery editing (TVP-1.5)', () => {
  it('reads the name, sound and stored webhook (never its URL)', () => {
    expect(deliveryEditorOf(alert)).toEqual({
      name: 'Breakout', sound: 'chime', storedSound: 'siren', webhookDisplay: 'https://hooks.example', webhookHasSecret: true,
    });
  });

  it('sends a new URL or secret only when typed, and an unknown stored sound only when changed', () => {
    const editing = { ...deliveryEditorOf(alert), notifications: ['sound', 'webhook'] };
    expect(deliveryUpdatePatch(editing)).toEqual({ name: 'Breakout', sound_name: undefined, webhook_url: undefined, webhook_secret: undefined });
    expect(deliveryUpdatePatch({ ...editing, webhookUrl: ' https://x.example/h ', webhookSecret: ' s ' })).toMatchObject({ webhook_url: 'https://x.example/h', webhook_secret: 's' });
    expect(deliveryCreateFields({ name: ' ', webhookUrl: '' })).toEqual({ name: undefined, soundName: 'chime', webhookUrl: undefined, webhookSecret: undefined });
    // Unticked: a typed URL or secret is dropped; an emptied name clears it.
    expect(deliveryUpdatePatch({ ...editing, notifications: ['app'], name: '', webhookUrl: 'https://x.example/h', webhookSecret: 's' }))
      .toEqual({ name: '', sound_name: undefined, webhook_url: undefined, webhook_secret: undefined });
  });
});
