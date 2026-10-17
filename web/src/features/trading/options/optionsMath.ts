/**
 * The options strategy builder's maths (TVP-10.4): Black-Scholes as the server computes it (`options.py`), and a
 * strategy's profit and loss across underlying prices at expiry and at a chosen date, with what-if volatility.
 */

export type OptionKind = 'call' | 'put';

export type OptionLeg = {
  id: string;
  symbol: string;
  kind: OptionKind;
  strike: number;
  /** Years from now to the leg's expiry. */
  years: number;
  /** 1 bought, -1 sold. */
  side: 1 | -1;
  quantity: number;
  /** Premium paid or received per share. */
  price: number;
  iv: number;
};

export type Market = { rate: number; dividendYield: number };

export const CONTRACT_SIZE = 100;

function normCdf(x: number): number {
  // Abramowitz-Stegun 7.1.26 through erf; good to about 1e-7.
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * z);
  const erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z);
  return 0.5 * (1 + sign * erf);
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

export type Greeks = { price: number; delta: number; gamma: number; theta: number; vega: number };

/** Price and Greeks per share; theta per calendar day, vega per volatility point. */
export function blackScholes(kind: OptionKind, spot: number, strike: number, years: number, market: Market, sigma: number): Greeks {
  if (spot <= 0 || strike <= 0 || years <= 0 || sigma <= 0) {
    const price = kind === 'call' ? Math.max(0, spot - strike) : Math.max(0, strike - spot);
    const delta = kind === 'call' ? (spot > strike ? 1 : 0) : (spot < strike ? -1 : 0);
    return { price, delta, gamma: 0, theta: 0, vega: 0 };
  }
  const { rate, dividendYield: q } = market;
  const root = Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (rate - q + 0.5 * sigma * sigma) * years) / (sigma * root);
  const d2 = d1 - sigma * root;
  const carry = Math.exp(-q * years);
  const discount = Math.exp(-rate * years);
  const gamma = carry * normPdf(d1) / (spot * sigma * root);
  const vega = spot * carry * normPdf(d1) * root / 100;
  const decay = -spot * carry * normPdf(d1) * sigma / (2 * root);
  if (kind === 'call') {
    const theta = decay - rate * strike * discount * normCdf(d2) + q * spot * carry * normCdf(d1);
    return { price: spot * carry * normCdf(d1) - strike * discount * normCdf(d2), delta: carry * normCdf(d1), gamma, theta: theta / 365, vega };
  }
  const theta = decay + rate * strike * discount * normCdf(-d2) - q * spot * carry * normCdf(-d1);
  return { price: strike * discount * normCdf(-d2) - spot * carry * normCdf(-d1), delta: -carry * normCdf(-d1), gamma, theta: theta / 365, vega };
}

export type WhatIf = { daysForward: number; ivShift: number };

/**
 * A strategy's profit or loss at an underlying price, `daysForward` from now with volatility shifted by `ivShift`, or
 * at expiry: the first leg's (later legs, in a calendar spread, still priced with their time left).
 */
export function strategyPnl(legs: readonly OptionLeg[], spot: number, market: Market, whatIf: WhatIf | 'expiry'): number {
  const { daysForward, ivShift } = whatIf === 'expiry' ? { daysForward: Math.min(...legs.map((leg) => leg.years)) * 365, ivShift: 0 } : whatIf;
  return legs.reduce((total, leg) => {
    const yearsLeft = Math.max(0, leg.years - daysForward / 365);
    const sigma = Math.max(0.01, leg.iv * (1 + ivShift));
    const value = blackScholes(leg.kind, spot, leg.strike, yearsLeft, market, sigma).price;
    return total + leg.side * leg.quantity * CONTRACT_SIZE * (value - leg.price);
  }, 0);
}

export type StrategySummary = {
  /** Paid (positive) or received (negative) to open. */
  netCost: number;
  maxProfit: number;
  maxLoss: number;
  /** Whether profit or loss keeps growing past the priced range (an uncovered call side, say). */
  profitUnbounded: boolean;
  lossUnbounded: boolean;
  breakevens: number[];
  greeks: Omit<Greeks, 'price'>;
};

/** Prices from `spot` x (1 - span) to x (1 + span), and the expiry P&L along them. */
export function pnlCurve(legs: readonly OptionLeg[], spot: number, market: Market, whatIf: WhatIf | 'expiry', span = 0.3, steps = 120): Array<{ price: number; pnl: number }> {
  return Array.from({ length: steps + 1 }, (_, index) => {
    const price = spot * (1 - span + (2 * span * index) / steps);
    return { price, pnl: strategyPnl(legs, price, market, whatIf) };
  });
}

export function strategySummary(legs: readonly OptionLeg[], spot: number, market: Market, span = 0.3): StrategySummary {
  const curve = pnlCurve(legs, spot, market, 'expiry', span, 240);
  const values = curve.map((point) => point.pnl);
  const breakevens: number[] = [];
  for (let index = 1; index < curve.length; index += 1) {
    const [left, right] = [curve[index - 1], curve[index]];
    if (left.pnl === 0) breakevens.push(left.price);
    else if (Math.sign(left.pnl) !== Math.sign(right.pnl) && right.pnl !== 0) breakevens.push(left.price + (right.price - left.price) * (-left.pnl / (right.pnl - left.pnl)));
  }
  // Past the range only calls keep changing value: the net calls' direction says which way the edge runs.
  const netCalls = legs.reduce((total, leg) => total + (leg.kind === 'call' ? leg.side * leg.quantity : 0), 0);
  const greeks = legs.reduce((total, leg) => {
    const model = blackScholes(leg.kind, spot, leg.strike, leg.years, market, leg.iv);
    const size = leg.side * leg.quantity * CONTRACT_SIZE;
    return { delta: total.delta + size * model.delta, gamma: total.gamma + size * model.gamma, theta: total.theta + size * model.theta, vega: total.vega + size * model.vega };
  }, { delta: 0, gamma: 0, theta: 0, vega: 0 });
  return {
    netCost: legs.reduce((total, leg) => total + leg.side * leg.quantity * CONTRACT_SIZE * leg.price, 0),
    maxProfit: Math.max(...values),
    maxLoss: Math.min(...values),
    profitUnbounded: netCalls > 0,
    lossUnbounded: netCalls < 0,
    breakevens,
    greeks,
  };
}
