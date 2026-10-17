import { indicatorOutputsFor } from './indicatorOutputKeys';
import { externalIndicatorOutputKeys, isExternalIndicatorId } from './indicators/externalIndicatorData';
import { intrabarOutputLine } from './indicators/intrabarIndicators';
import type { CoreIndicatorId } from './indicators/coreIndicators';
import { newIndicatorInstance } from './tradingStore';

/**
 * The lines an indicator draws with this period (the server registry computes the same keys); an external-data
 * indicator's lines are its data series (TVP-0.2), the same at any period; Volume Delta and CVD draw one line each
 * from lower-timeframe bars (TVP-6.4).
 */
export function indicatorLines(indicatorId: string, period: number): Array<{ key: string; title: string }> {
  if (isExternalIndicatorId(indicatorId)) return externalIndicatorOutputKeys(indicatorId).map((key) => ({ key, title: key.slice(indicatorId.length + 1) }));
  const intrabar = intrabarOutputLine(indicatorId);
  if (intrabar) return [intrabar];
  try {
    const instance = { ...newIndicatorInstance(indicatorId as CoreIndicatorId), period };
    return indicatorOutputsFor(instance).map((output) => ({ key: output.key, title: output.title }));
  } catch {
    return [];
  }
}
