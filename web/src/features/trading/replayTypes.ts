import type { components } from './api/generated';
export interface FrozenReplayBar {
  instrument_id: string;
  interval: string;
  start_time: string;
  end_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  is_final: boolean;
  adjustment_mode: string;
  session: string;
  provider: string;
  provider_event_id?: string | null;
  provider_sequence?: number | null;
  ingestion_revision: number;
  received_at: string;
}

export type FrozenDatasetSnapshot = components['schemas']['FrozenDatasetSnapshot'];

export type BacktestTrade = components['schemas']['BacktestTrade'];

export type BacktestEquityPoint = components['schemas']['BacktestEquityPoint'];

export type BacktestArtifactReference = components['schemas']['BacktestArtifactReference'];

export type BacktestRunResult = components['schemas']['BacktestRunResult'];
