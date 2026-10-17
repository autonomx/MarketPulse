import type { HermesRpgTurnReadoutResponse } from './api/hermesClient';

export interface RpgTurnReadoutPreviewState {
  category?: string;
  systems?: string[];
  effectCount?: number;
  groundingStatus?: string;
}

export function createRpgTurnReadoutPreview(payload: HermesRpgTurnReadoutResponse | undefined): RpgTurnReadoutPreviewState | undefined {
  if (!payload?.ok) return undefined;
  return {
    category: payload.turn?.category,
    systems: payload.systems,
    effectCount: payload.effect_count,
    groundingStatus: payload.grounding_status,
  };
}
