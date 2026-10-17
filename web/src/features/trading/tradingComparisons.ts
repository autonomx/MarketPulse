export type TradingComparisonPlacement = 'percent' | 'price-scale' | 'pane';

export type TradingComparison = {
  instrumentId: string;
  placement: TradingComparisonPlacement;
  visible?: boolean;
};

export const TRADING_COMPARISON_COLORS = [
  '#2962ff',
  '#ab47bc',
  '#26a69a',
  '#ff9800',
  '#ef5350',
] as const;
