import type { RpgInventoryItemPreview } from './rpgUiState';

export type RpgItemUiActionKind =
  | 'inspect'
  | 'use'
  | 'equip'
  | 'drop'
  | 'salvage'
  | 'craft'
  | 'modify'
  | 'sell'
  | 'buy'
  | 'diagnostics'
  | 'maintenance'
  | 'objectives'
  | 'scenario';

export type RpgItemUiActionMode = 'loadout' | 'item_resolve' | 'merchant' | 'status';

export interface RpgSelectedItemPreview {
  label: string;
  icon: string;
  count: string | number;
  sessionId?: string | null;
}

export interface RpgItemDetailPreview {
  itemName: string;
  icon: string;
  countLabel: string;
  summary: string;
  status: string;
  condition: string;
  tags: string[];
  source: 'llm' | 'preview' | 'pending' | 'unavailable';
}

export interface RpgItemUiAction {
  id: string;
  kind: RpgItemUiActionKind;
  label: string;
  detail: string;
  mode: RpgItemUiActionMode;
  command: string;
  payload: Record<string, unknown>;
  disabled?: boolean;
  item?: RpgSelectedItemPreview;
}

export interface RpgItemObjectivePreview {
  id: string;
  label: string;
  detail: string;
  action: string;
  payload: Record<string, unknown>;
  disabled?: boolean;
}

export interface RpgMerchantEntryPreview {
  id: string;
  label: string;
  detail: string;
  priceLabel: string;
  action: 'buy' | 'sell';
  payload: Record<string, unknown>;
  disabled?: boolean;
}

interface BuildSelectedItemActionsInput {
  item?: RpgInventoryItemPreview;
  selectedSessionId?: string | null;
}

const DEFAULT_ITEM_COUNT = '1';

export function buildSelectedItemActions({ item, selectedSessionId }: BuildSelectedItemActionsInput): RpgItemUiAction[] {
  if (!item) {
    return [];
  }

  const itemName = item.label;
  const selectedItem: RpgSelectedItemPreview = {
    label: itemName,
    icon: item.icon,
    count: item.count || DEFAULT_ITEM_COUNT,
    sessionId: selectedSessionId ?? null,
  };
  const disabled = !selectedSessionId;
  const baseDetail = disabled ? 'Select a live session before applying item actions.' : `${item.count || DEFAULT_ITEM_COUNT} carried.`;
  const loadout = (kind: RpgItemUiActionKind, label: string, detail: string, action: string): RpgItemUiAction => ({
    id: `${kind}:${itemName}`,
    kind,
    label,
    detail: `${detail} ${baseDetail}`.trim(),
    mode: 'loadout',
    command: `${label} ${itemName}`,
    payload: { action, item_name: itemName },
    disabled,
    item: selectedItem,
  });
  const resolve = (kind: RpgItemUiActionKind, label: string, detail: string, command: string): RpgItemUiAction => ({
    id: `${kind}:${itemName}`,
    kind,
    label,
    detail: `${detail} ${baseDetail}`.trim(),
    mode: kind === 'sell' ? 'merchant' : 'item_resolve',
    command,
    payload: kind === 'sell' ? { command, item_name: itemName } : { command },
    disabled,
    item: selectedItem,
  });

  return [
    loadout('inspect', 'Inspect', 'Review LLM-generated item details and deterministic item state.', 'inspect'),
    loadout('use', 'Use', 'Apply item effects through deterministic item handling.', 'use'),
    loadout('equip', 'Equip', 'Equip the item if it belongs in a gear slot.', 'equip'),
    loadout('drop', 'Drop', 'Remove one carried instance when safe.', 'drop'),
    loadout('salvage', 'Salvage', 'Recover deterministic materials from the item.', 'salvage'),
    resolve('sell', 'Sell', 'Offer the item to the active merchant service.', `sell ${itemName}`),
  ];
}

export function buildItemDetailPreview(payload: Record<string, unknown> | null | undefined, item?: RpgSelectedItemPreview): RpgItemDetailPreview | undefined {
  if (!item) {
    return undefined;
  }

  const itemName = item.label;
  const countLabel = `${item.count || DEFAULT_ITEM_COUNT} carried`;
  const detailPayload = firstRecord(
    payload?.item_detail,
    payload?.item_details,
    payload?.detail,
    payload?.details,
    asRecord(payload?.llm)?.item_detail,
    asRecord(payload?.llm)?.details,
  );
  const detailSource = firstString(detailPayload?.source);
  const source: RpgItemDetailPreview['source'] = !item.sessionId
    ? 'preview'
    : detailPayload && detailSource !== 'unavailable' && payload?.ok !== false
      ? 'llm'
      : payload
        ? 'unavailable'
        : 'pending';
  const summary =
    firstString(
      detailPayload?.summary,
      detailPayload?.description,
      detailPayload?.text,
      detailPayload?.narration,
      payload?.summary,
      payload?.description,
    ) ??
    (source === 'pending'
      ? 'Generating an item description with the configured LLM.'
      : source === 'preview'
        ? 'Select or create a live session to generate an item description.'
        : 'An item description is unavailable right now.');
  const status = firstString(detailPayload?.status, detailPayload?.state) ?? (source === 'pending' ? 'Loading' : item.sessionId ? 'Carried' : 'Preview');
  const condition = firstString(detailPayload?.condition, detailPayload?.durability) ?? (source === 'pending' ? 'Loading' : 'Not recorded');
  const tags = firstArray(detailPayload?.tags, detailPayload?.traits, payload?.tags).map(String).slice(0, 5);

  return {
    itemName,
    icon: item.icon,
    countLabel,
    summary,
    status,
    condition,
    tags,
    source,
  };
}

export function buildItemObjectivePreviews(payload: Record<string, unknown> | null | undefined): RpgItemObjectivePreview[] {
  const objectives = Array.isArray(payload?.objectives) ? payload.objectives : [];
  return objectives.map((entry, index) => {
    const objective = asRecord(entry);
    const actionPayload = readRecord(objective.action);
    const action = readString(actionPayload?.action) || readString(actionPayload?.kind) || readString(objective.action) || readString(objective.kind) || 'item_action';
    const label = readString(objective.label) || titleCase(action);
    const detail = readString(objective.detail) || readString(objective.reason) || 'Deterministic item-system suggestion.';
    const payloadValue = readRecord(objective.payload) ?? readRecord(objective.request) ?? actionPayload ?? {};
    return {
      id: readString(objective.id) || readString(objective.objective_id) || `${action}:${index}`,
      label,
      detail,
      action,
      payload: payloadValue,
      disabled: Boolean(objective.disabled),
    };
  });
}

export function buildMerchantEntryPreviews(payload: Record<string, unknown> | null | undefined): RpgMerchantEntryPreview[] {
  const entries = Array.isArray(payload?.entries)
    ? payload.entries
    : Array.isArray(payload?.menu)
      ? payload.menu
      : Array.isArray(payload?.items)
        ? payload.items
        : [];

  return entries.map((entry, index) => {
    const record = asRecord(entry);
    const action = readString(record.action) === 'sell' ? 'sell' : 'buy';
    const itemName = readString(record.item_name) || readString(record.name) || readString(record.label) || `Item ${index + 1}`;
    const priceLabel = readString(record.price_label) || formatPrice(record.price ?? record.quote ?? record.currency);
    return {
      id: readString(record.id) || `${action}:${itemName}:${index}`,
      label: itemName,
      detail: readString(record.detail) || readString(record.description) || `${titleCase(action)} ${itemName}`,
      priceLabel,
      action,
      payload: { action, item_name: itemName },
      disabled: Boolean(record.disabled),
    };
  });
}

function asRecord(value: unknown): Record<string, unknown> {
  return readRecord(value) ?? {};
}

function readRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    const text = readString(value);
    if (text) {
      return text;
    }
  }
  return undefined;
}

function firstRecord(...values: unknown[]): Record<string, unknown> | undefined {
  for (const value of values) {
    const record = readRecord(value);
    if (record) {
      return record;
    }
  }
  return undefined;
}

function firstArray(...values: unknown[]): unknown[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function readNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatPrice(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `${value} coin${value === 1 ? '' : 's'}`;
  }
  const record = asRecord(value);
  const gold = readNumber(record.gold) ?? 0;
  const silver = readNumber(record.silver) ?? 0;
  const copper = readNumber(record.copper) ?? 0;
  const parts = [gold ? `${gold}g` : '', silver ? `${silver}s` : '', copper ? `${copper}c` : ''].filter(Boolean);
  return parts.length ? parts.join(' ') : 'Quote pending';
}
