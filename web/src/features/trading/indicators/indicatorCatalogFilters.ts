export type IndicatorCatalogKind = 'indicator' | 'profile' | 'pattern';
export type IndicatorMarket = 'universal' | 'stocks' | 'crypto' | 'derivatives';
export type IndicatorMarketFilter = 'all' | IndicatorMarket;
export type IndicatorCategory = 'trend' | 'momentum' | 'volatility' | 'volume' | 'levels' | 'breadth' | 'on-chain' | 'derivatives' | 'fundamentals' | 'other';
export type IndicatorCategoryFilter = 'all' | IndicatorCategory;
export type IndicatorAvailabilityFilter = 'all' | 'ready' | 'data-required';

export type IndicatorCatalogClassification = {
  markets: IndicatorMarket[];
  category: IndicatorCategory;
};

const STOCK_SPECIFIC = /(analyst price forecast|price target|advance\/decline|earnings|dividend|revenue|fundamental)/i;
const CRYPTO_SPECIFIC = /(1 year active supply|24-hour volume|active addresses|addresses with balance|block (?:height|size)|blocks mined|\bdifficulty\b|\bcrypto\b|gas used|hash rate|miner|mining|transaction|transfer|utxo|spot crypto etf|\bsupply\b)/i;
const DERIVATIVE_SPECIFIC = /(\bbasis\b|funding|liquidation|open interest|long\/short|futures?|perpetual|mark price|index price|\bpremium\b)/i;

const ON_CHAIN = /(1 year active supply|active addresses|addresses with balance|block (?:height|size)|blocks mined|\bdifficulty\b|gas used|hash rate|miner|mining|transaction|transfer|utxo|spot crypto etf|\bsupply\b)/i;
const FUNDAMENTALS = /(analyst price forecast|price target|earnings|dividend|revenue|fundamental|valuation)/i;
const BREADTH = /(advance\/decline|market breadth|new highs|new lows)/i;
const LEVELS = /(fib|key levels|pitchfork|pivot points?|support|resistance|zig zag|fractal|fair value gap|swing levels|liquidity|volume profile|auto trendlines|trend detector)/i;
const VOLATILITY = /(average daily range|average true range|\batr\b|bollinger|bbtrend|bandwidth|chandelier|choppiness|historical volatility|keltner|mass index|ulcer index|volatility stop|donchian)/i;
const VOLUME = /(volume|accumulation distribution|chaikin money flow|money flow|on balance volume|price volume trend|force index|ease of movement|klinger|vwap|volume-weighted|net volume|up\/down volume|positive volume index|negative volume index)/i;
const MOMENTUM = /(rsi|stochastic|momentum|oscillator|commodity channel index|\bcci\b|rate of change|\broc\b|fisher transform|williams %r|ultimate oscillator|coppock|know sure thing|\bkst\b|price momentum oscillator|\bpmo\b|trix|true strength index|relative vigor|relative volatility|smi ergodic)/i;
const TREND = /(moving average|\bema\b|\bsma\b|alma|aroon|average directional|\badx\b|directional movement|\bdmi\b|ichimoku|supertrend|parabolic sar|alligator|trend strength|hull|kaufman|mcginley|linear regression|least squares|triple ema|smoothed moving average|ma cross|macd)/i;

export function classifyIndicatorCatalogEntry(name: string, kind: IndicatorCatalogKind): IndicatorCatalogClassification {
  const markets: IndicatorMarket[] = [];
  if (STOCK_SPECIFIC.test(name)) markets.push('stocks');
  if (CRYPTO_SPECIFIC.test(name)) markets.push('crypto');
  if (DERIVATIVE_SPECIFIC.test(name)) markets.push('derivatives');
  if (markets.length === 0) markets.push('universal');

  if (kind === 'pattern') return { markets, category: 'levels' };
  if (kind === 'profile') return { markets, category: 'volume' };
  if (DERIVATIVE_SPECIFIC.test(name)) return { markets, category: 'derivatives' };
  if (ON_CHAIN.test(name)) return { markets, category: 'on-chain' };
  if (FUNDAMENTALS.test(name)) return { markets, category: 'fundamentals' };
  if (BREADTH.test(name)) return { markets, category: 'breadth' };
  if (LEVELS.test(name)) return { markets, category: 'levels' };
  if (VOLATILITY.test(name)) return { markets, category: 'volatility' };
  if (VOLUME.test(name)) return { markets, category: 'volume' };
  if (MOMENTUM.test(name)) return { markets, category: 'momentum' };
  if (TREND.test(name)) return { markets, category: 'trend' };
  return { markets, category: 'other' };
}

export function indicatorMarketMatches(markets: readonly IndicatorMarket[], filter: IndicatorMarketFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'universal') return markets.includes('universal');
  return markets.includes('universal') || markets.includes(filter);
}

export function indicatorAvailabilityMatches(available: boolean, filter: IndicatorAvailabilityFilter): boolean {
  if (filter === 'all') return true;
  return filter === 'ready' ? available : !available;
}
