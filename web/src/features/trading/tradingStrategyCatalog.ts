export type TradingStrategyPhaseDefinition = {
  id: 'discover' | 'research' | 'llm' | 'deterministic' | 'selection' | 'execution';
  label: string;
  description: string;
  safety?: string;
};

export type TradingStrategyDefinition = {
  kind: 'gap_pullback_v1' | 'stoch_rsi_5m_v1';
  label: string;
  version: string;
  thesis: string;
  phases: readonly TradingStrategyPhaseDefinition[];
};

export const TRADING_STRATEGY_DEFINITIONS: Record<'gap_pullback_v1' | 'stoch_rsi_5m_v1', TradingStrategyDefinition> = {
  gap_pullback_v1: {
    kind: 'gap_pullback_v1',
    label: 'Failed Sell-Off / Gap Pullback',
    version: '2.0.0',
    thesis: 'Do not buy the first sell-off. Wait for seller failure, structural reclaim, quality confirmation, then paper execution.',
    phases: [
      {
        id: 'discover',
        label: '1. Scan & freeze',
        description: 'Yahoo discovers current listed gap-up stocks and freezes the point-in-time morning universe.',
      },
      {
        id: 'research',
        label: '2. Research & narrow',
        description: 'Review catalyst evidence, float, liquidity, spread and dilution/supply risk; explicitly narrow the candidate set.',
      },
      {
        id: 'llm',
        label: '3. LLM research',
        description: 'Run a cited catalyst classification over frozen evidence to help an operator review and rank candidates.',
        safety: 'Shadow/research only. LLM output never authorizes an order.',
      },
      {
        id: 'deterministic',
        label: '4. Deterministic setup',
        description: 'Evaluate finalized structure bars for contracting sell volume, L1 → B1 → higher L2, VWAP reclaim, B1 break, breakout volume and optional hold.',
      },
      {
        id: 'selection',
        label: '5. Daily selection',
        description: 'Only hard-gate passes above the configured 0–10 quality threshold become entry-ready; simultaneous names are ordered by quality score, then scan rank.',
      },
      {
        id: 'execution',
        label: '6. Auto paper',
        description: 'Use the configured execution resolution plus live Alpaca IEX evidence for server risk sizing, deterministic paper fills, stop/target protection and EOD flatten.',
        safety: 'Paper only. No live broker path.',
      },
    ],
  },
  stoch_rsi_5m_v1: {
    kind: 'stoch_rsi_5m_v1',
    label: '5m Stoch RSI Cross',
    version: '1.0.0',
    thesis: 'Buy a %K cross above %D below 12; exit on a %K/%D cross down below 80 or above 95. Shadow/replay only.',
    phases: [
      {
        id: 'discover',
        label: '1. Scan & freeze',
        description: 'Use the frozen daily candidate universe as the evidence cohort.',
      },
      {
        id: 'deterministic',
        label: '2. 5m Stoch RSI',
        description: 'Evaluate finalized five-minute candles with causal %K/%D crossings in the configured extreme zones.',
      },
      {
        id: 'execution',
        label: '3. Shadow replay',
        description: 'Record next-bar-open entry/exit evidence and force-flat outcomes without submitting orders.',
        safety: 'Shadow only. No AUTO PAPER or live broker path.',
      },
    ],
  },
};
