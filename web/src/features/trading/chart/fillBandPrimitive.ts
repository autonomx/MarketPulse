// The area between two lines (an Omnix Script's fill(), TVP-11.1): a series primitive that paints, bar by bar, the band
// from each point's `high` to its `low` in the point's colour, under the lines it fills between. It is attached to a
// hidden line series in the same pane, whose price scale it uses, and it widens that scale to the band.
import type {
  AutoscaleInfo,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';

export type FillBandPoint = { time: Time; upper: number; lower: number; color: string };

type RenderTarget = Parameters<IPrimitivePaneRenderer['draw']>[0];

export class FillBandPrimitive implements ISeriesPrimitive<Time> {
  private points: readonly FillBandPoint[] = [];
  private chart: SeriesAttachedParameter<Time>['chart'] | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: () => undefined,
      drawBackground: (target: RenderTarget) => target.useMediaCoordinateSpace(({ context }) => this.paint(context)),
    };
    this.views = [{ zOrder: () => 'bottom', renderer: () => renderer }];
  }

  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this.chart = chart;
    this.series = series;
    this.requestUpdate = requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  setPoints(points: readonly FillBandPoint[]): void {
    this.points = points;
    this.requestUpdate?.();
  }

  /** The band's range over the bars in view, so the price scale shows all of it. */
  autoscaleInfo(start: Logical, end: Logical): AutoscaleInfo | null {
    const timeScale = this.chart?.timeScale();
    if (!timeScale || this.points.length === 0) return null;
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const point of this.points) {
      const logical = timeScale.timeToIndex(point.time, true);
      if (logical === null || logical < start || logical > end || !Number.isFinite(point.upper) || !Number.isFinite(point.lower)) continue;
      min = Math.min(min, point.lower, point.upper);
      max = Math.max(max, point.lower, point.upper);
    }
    return Number.isFinite(min) ? { priceRange: { minValue: min, maxValue: max } } : null;
  }

  private paint(context: CanvasRenderingContext2D): void {
    const timeScale = this.chart?.timeScale();
    const series = this.series;
    if (!timeScale || !series) return;
    let previous: { x: number; top: number; bottom: number } | null = null;
    for (const point of this.points) {
      if (!Number.isFinite(point.upper) || !Number.isFinite(point.lower)) {
        previous = null; // a bar without both values: a gap in the band
        continue;
      }
      const x = timeScale.timeToCoordinate(point.time);
      const top = series.priceToCoordinate(point.upper);
      const bottom = series.priceToCoordinate(point.lower);
      if (x === null || top === null || bottom === null) {
        previous = null; // a gap: the band starts again on the next bar with both values
        continue;
      }
      if (previous) {
        context.beginPath();
        context.moveTo(previous.x, previous.top);
        context.lineTo(x, top);
        context.lineTo(x, bottom);
        context.lineTo(previous.x, previous.bottom);
        context.closePath();
        context.fillStyle = point.color;
        context.fill();
      }
      previous = { x, top, bottom };
    }
  }
}
