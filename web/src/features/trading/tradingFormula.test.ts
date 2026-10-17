import { describe, expect, it } from 'vitest';
import {
  decodeTradingFormula,
  encodeTradingFormula,
  evaluateTradingFormula,
  parseTradingFormula,
} from './tradingFormula';

describe('trading arithmetic formulas', () => {
  it('parses operators with TradingView-style precedence', () => {
    const formula = parseTradingFormula('BTCUSD / ETHUSD + 2');
    expect(formula?.symbols).toEqual(['BTCUSD', 'ETHUSD']);
    expect(formula ? evaluateTradingFormula(formula.root, (symbol) => ({ BTCUSD: 100, ETHUSD: 20 }[symbol] ?? null)) : null).toBe(7);
  });

  it('supports explicit formulas, parentheses, powers, and embedded symbol hyphens', () => {
    const formula = parseTradingFormula('=BTC-USDT / (ETH-USDT ^ 2)', {
      symbolHints: ['BTC-USDT', 'ETH-USDT'],
    });
    expect(formula?.symbols).toEqual(['BTC-USDT', 'ETH-USDT']);
    expect(formula ? evaluateTradingFormula(formula.root, (symbol) => ({ 'BTC-USDT': 100, 'ETH-USDT': 5 }[symbol] ?? null)) : null).toBe(4);
  });

  it('treats an unrecognized hyphen as subtraction', () => {
    const formula = parseTradingFormula('(TOTAL3-USDT)/BTC');
    expect(formula?.symbols).toEqual(['TOTAL3', 'USDT', 'BTC']);
  });

  it('round-trips a derived instrument payload', () => {
    const id = encodeTradingFormula('BTCUSD / ETHUSD', {
      BTCUSD: 'crypto:BINANCE:spot:BTC-USDT',
      ETHUSD: 'crypto:BINANCE:spot:ETH-USDT',
    });
    expect(decodeTradingFormula(id)).toEqual({
      expression: 'BTCUSD / ETHUSD',
      operands: {
        BTCUSD: 'crypto:BINANCE:spot:BTC-USDT',
        ETHUSD: 'crypto:BINANCE:spot:ETH-USDT',
      },
    });
  });
});
