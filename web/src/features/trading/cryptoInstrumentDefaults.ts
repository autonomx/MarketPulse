import type { CanonicalInstrument } from './tradingTypes';

const BINANCE_SPOT_BASES = new Set(['BTC', 'ETH', 'SOL']);

export function binanceInstrumentIdFor(instrumentId: string): string {
  const match = /^crypto:(?:BINANCE|COINBASE|KRAKEN):spot:([A-Z0-9]+)-USD$/i.exec(instrumentId);
  const base = match?.[1]?.toUpperCase();
  return base && BINANCE_SPOT_BASES.has(base)
    ? `crypto:BINANCE:spot:${base}-USDT`
    : instrumentId;
}

export function preferredCryptoInstrument(
  instrument: CanonicalInstrument,
  instruments: readonly CanonicalInstrument[],
): CanonicalInstrument {
  if (instrument.asset_class !== 'crypto') return instrument;
  const preferredId = binanceInstrumentIdFor(instrument.instrument_id);
  return instruments.find((candidate) => candidate.instrument_id === preferredId) ?? instrument;
}
