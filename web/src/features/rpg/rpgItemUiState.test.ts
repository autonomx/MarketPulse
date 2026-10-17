import { describe, expect, it } from 'vitest';
import {
  buildItemObjectivePreviews,
  buildItemDetailPreview,
  buildMerchantEntryPreviews,
  buildSelectedItemActions,
} from './rpgItemUiState';

const selectedItem = { icon: '◇', count: '2', label: 'Field Kit' };

describe('rpg item UI state', () => {
  it('turns completed item-detail failures into an unavailable state', () => {
    const detail = buildItemDetailPreview(
      {
        ok: false,
        error: 'item_detail_llm_unavailable',
        item_detail: {
          summary: 'An LLM provider is not available to describe this item.',
          status: 'Carried',
          condition: 'Worn (40%)',
          source: 'unavailable',
        },
      },
      { label: 'Field Kit', icon: '◇', count: 2, sessionId: 'session-live' },
    );

    expect(detail).toMatchObject({
      source: 'unavailable',
      status: 'Carried',
      condition: 'Worn (40%)',
      summary: 'An LLM provider is not available to describe this item.',
    });
  });

  it('builds deterministic selected-item actions for live sessions', () => {
    const actions = buildSelectedItemActions({ item: selectedItem, selectedSessionId: 'session-live' });

    expect(actions.map((action) => action.kind)).toEqual(['inspect', 'use', 'equip', 'drop', 'salvage', 'sell']);
    expect(actions[0]).toMatchObject({
      id: 'inspect:Field Kit',
      mode: 'loadout',
      payload: { action: 'inspect', item_name: 'Field Kit' },
      disabled: false,
    });
    expect(actions[4]).toMatchObject({
      mode: 'loadout',
      payload: { action: 'salvage', item_name: 'Field Kit' },
    });
    expect(actions[5]).toMatchObject({ mode: 'merchant', command: 'sell Field Kit' });
  });

  it('marks selected-item actions disabled without a live session', () => {
    const actions = buildSelectedItemActions({ item: selectedItem, selectedSessionId: null });

    expect(actions).toHaveLength(6);
    expect(actions.every((action) => action.disabled)).toBe(true);
    expect(actions[0].detail).toContain('Select a live session');
  });

  it('normalizes objective payloads into preview rows', () => {
    const objectives = buildItemObjectivePreviews({
      objectives: [
        {
          objective_id: 'craft:torch',
          action: { action: 'craft', recipe_id: 'torch', station: 'campfire' },
          label: 'Craft torch',
          reason: 'Enough materials are available.',
        },
        { kind: 'report', request: { record: true }, disabled: true },
      ],
    });

    expect(objectives).toEqual([
      {
        id: 'craft:torch',
        label: 'Craft torch',
        detail: 'Enough materials are available.',
        action: 'craft',
        payload: { action: 'craft', recipe_id: 'torch', station: 'campfire' },
        disabled: false,
      },
      {
        id: 'report:1',
        label: 'Report',
        detail: 'Deterministic item-system suggestion.',
        action: 'report',
        payload: { record: true },
        disabled: true,
      },
    ]);
  });

  it('builds merchant entries from menus and price records', () => {
    const entries = buildMerchantEntryPreviews({
      menu: [
        { id: 'buy:torch', action: 'buy', item_name: 'Torch', description: 'A useful light source.', price: { silver: 1 } },
        { action: 'sell', name: 'Field Kit', price: 3, disabled: true },
      ],
    });

    expect(entries).toEqual([
      {
        id: 'buy:torch',
        label: 'Torch',
        detail: 'A useful light source.',
        priceLabel: '1s',
        action: 'buy',
        payload: { action: 'buy', item_name: 'Torch' },
        disabled: false,
      },
      {
        id: 'sell:Field Kit:1',
        label: 'Field Kit',
        detail: 'Sell Field Kit',
        priceLabel: '3 coins',
        action: 'sell',
        payload: { action: 'sell', item_name: 'Field Kit' },
        disabled: true,
      },
    ]);
  });
});
