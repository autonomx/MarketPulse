import type { CanonicalInstrument } from '../tradingTypes';
import { UNKNOWN_DRAWING_INSTRUMENT, type DrawingInstrument } from './tools/types';

/** Tick size and point value for drawing tools, from the chart's instrument. */
export function drawingInstrumentOf(instrument: CanonicalInstrument | undefined): DrawingInstrument {
  if (!instrument) return UNKNOWN_DRAWING_INSTRUMENT;
  const tick = Number(instrument.minimum_tick);
  return {
    tickSize: Number.isFinite(tick) && tick > 0 ? tick : null,
    // Shares, spot and linear perpetuals move 1 unit of currency per 1.0 per unit; an index has no tradable unit.
    pointValue: instrument.instrument_type === 'index' ? null : 1,
  };
}
