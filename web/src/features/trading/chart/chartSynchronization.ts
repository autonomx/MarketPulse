import type { TradingCrosshairPoint, TradingVisibleRange } from './chartAdapter';

export type ChartSynchronizationLinks = {
  crosshair: boolean;
  visibleRange: boolean;
};

export type SynchronizableChart = {
  onCrosshair: (listener: (point: TradingCrosshairPoint | null) => void) => () => void;
  setCrosshair: (point: TradingCrosshairPoint | null) => void;
  onVisibleRange: (listener: (range: TradingVisibleRange | null) => void) => () => void;
  setVisibleRange: (range: TradingVisibleRange) => void;
};

type RegisteredChart = {
  adapter: SynchronizableChart;
  disposeCrosshair: () => void;
  disposeRange: () => void;
};

export class TradingChartSynchronization {
  private readonly charts = new Map<string, RegisteredChart>();
  private links: ChartSynchronizationLinks = { crosshair: true, visibleRange: false };
  private applyingCrosshair = false;
  private applyingRange = false;

  setLinks(links: ChartSynchronizationLinks): void {
    this.links = { ...links };
  }

  register(chartId: string, adapter: SynchronizableChart): () => void {
    this.unregister(chartId);
    const disposeCrosshair = adapter.onCrosshair((point) => this.publishCrosshair(chartId, point));
    const disposeRange = adapter.onVisibleRange((range) => this.publishVisibleRange(chartId, range));
    this.charts.set(chartId, { adapter, disposeCrosshair, disposeRange });
    return () => this.unregister(chartId);
  }

  unregister(chartId: string): void {
    const chart = this.charts.get(chartId);
    if (!chart) return;
    chart.disposeCrosshair();
    chart.disposeRange();
    this.charts.delete(chartId);
  }

  private publishCrosshair(sourceId: string, point: TradingCrosshairPoint | null): void {
    if (!this.links.crosshair || this.applyingCrosshair) return;
    this.applyingCrosshair = true;
    try {
      this.charts.forEach((chart, chartId) => {
        if (chartId !== sourceId) chart.adapter.setCrosshair(point);
      });
    } finally {
      this.applyingCrosshair = false;
    }
  }

  private publishVisibleRange(sourceId: string, range: TradingVisibleRange | null): void {
    if (!this.links.visibleRange || this.applyingRange || range === null) return;
    this.applyingRange = true;
    try {
      this.charts.forEach((chart, chartId) => {
        if (chartId !== sourceId) chart.adapter.setVisibleRange(range);
      });
    } finally {
      this.applyingRange = false;
    }
  }

  dispose(): void {
    [...this.charts.keys()].forEach((chartId) => this.unregister(chartId));
  }

  get registeredChartCount(): number {
    return this.charts.size;
  }
}
