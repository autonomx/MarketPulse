// Fixed range volume profile (TVP-3.6): the volume profile indicator's computation over the bars between the
// anchors, drawn at the range's left edge with the point of control and the value area.
import { volumeProfileData, type VolumeProfileData } from '../../../indicators/coreIndicators';
import type { MarketBar } from '../../../tradingTypes';
import { lineStroke } from '../shapes';
import { defineDrawingTool, type DrawingBarSeries, type DrawingShape } from '../types';
import { patternBars } from './projections';

/** The profile of the bars starting between `from` and `to` (either order); null with no bars there. */
export function rangeVolumeProfile(bars: DrawingBarSeries, from: string, to: string): VolumeProfileData | null {
  const range = patternBars(bars, from, to);
  if (range.length === 0) return null;
  const marketBars = range.map((bar) => ({
    start_time: bar.time, open: String(bar.open), high: String(bar.high), low: String(bar.low), close: String(bar.close), volume: String(bar.volume),
  })) as unknown as MarketBar[];
  return volumeProfileData(marketBars, marketBars.length);
}

const VALUE_AREA = '#2962ff';
const OUTSIDE = '#787b86';
const POC = '#f23645';

export const fixedRangeVolumeProfileTool = defineDrawingTool({
  id: 'fixed-range-volume-profile',
  label: 'Fixed range volume profile',
  group: 'volume-based',
  creation: { gesture: 'drag' },
  defaultProperties: { widthPercent: 30 },
  propertySchema: [{ key: 'widthPercent', label: 'Width (% of range)', type: 'number', min: 5, max: 100, step: 5 }],
  draftPreview: 'shapes',
  geometry: (context) => {
    const [first, second] = context.points;
    const left = Math.min(first.x, second.x);
    const right = Math.max(first.x, second.x);
    const outline: DrawingShape = { kind: 'rect', x: left, y: Math.min(first.y, second.y), width: right - left, height: Math.abs(second.y - first.y), ...lineStroke(context), strokeWidth: 1, dash: [4, 4] };
    const profile = rangeVolumeProfile(context.bars, context.rawPoints[0].time, context.rawPoints[1].time);
    if (!profile || profile.maxVolume <= 0) return [outline];
    const widthPercent = Number(context.properties.widthPercent);
    const maxWidth = (right - left) * (Number.isFinite(widthPercent) ? Math.min(100, Math.max(5, widthPercent)) : 30) / 100;
    const time = context.rawPoints[0].time;
    const shapes: DrawingShape[] = [];
    let top = Number.POSITIVE_INFINITY;
    let bottom = Number.NEGATIVE_INFINITY;
    profile.bins.forEach((bin, index) => {
      const high = context.project({ time, price: bin.high });
      const low = context.project({ time, price: bin.low });
      if (!high || !low) return;
      top = Math.min(top, high.y);
      bottom = Math.max(bottom, low.y);
      const inValueArea = bin.low >= profile.valueAreaLow - 1e-12 && bin.high <= profile.valueAreaHigh + 1e-12;
      shapes.push({
        kind: 'rect',
        x: left,
        y: Math.min(high.y, low.y) + 0.5,
        width: maxWidth * bin.volume / profile.maxVolume,
        height: Math.max(1, Math.abs(low.y - high.y) - 1),
        fill: index === profile.pocIndex ? POC : inValueArea ? VALUE_AREA : OUTSIDE,
        fillOpacity: inValueArea ? 0.45 : 0.25,
        hit: 'none',
      });
    });
    const poc = context.project({ time, price: profile.poc });
    if (poc) shapes.push({ kind: 'segment', x1: left, y1: poc.y, x2: right, y2: poc.y, stroke: POC, strokeWidth: 1 });
    if (Number.isFinite(top)) shapes.unshift({ kind: 'rect', x: left, y: top, width: right - left, height: bottom - top, ...lineStroke(context), strokeWidth: 1, dash: [4, 4], fill: context.style.color, fillOpacity: 0.04 });
    return shapes;
  },
});
