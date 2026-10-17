import type { TradingChartState, TradingLayout, TradingLinkState } from './tradingStore';
import { downloadJson } from '../../shared/download';

export type TradingWorkspaceExport = {
  schemaVersion: 2;
  exportedAt: string;
  layout: TradingLayout;
  activeChartId: string;
  charts: TradingChartState[];
  links: TradingLinkState;
};

export function buildTradingWorkspaceExport(input: Omit<TradingWorkspaceExport, 'schemaVersion' | 'exportedAt'>): TradingWorkspaceExport {
  return {
    schemaVersion: 2,
    exportedAt: new Date().toISOString(),
    layout: input.layout,
    activeChartId: input.activeChartId,
    charts: input.charts.map((chart) => ({
      ...chart,
      indicators: chart.indicators.map((indicator) => ({ ...indicator })),
    })),
    links: { ...input.links },
  };
}

export function downloadTradingWorkspaceExport(payload: TradingWorkspaceExport): void {
  downloadJson(payload, `omnix-trading-workspace-${payload.exportedAt.replace(/[:.]/g, '-')}.json`);
}
