import { describe, expect, it } from 'vitest';
import type { CanonicalInstrument } from './tradingTypes';
import { binanceInstrumentIdFor, preferredCryptoInstrument } from './cryptoInstrumentDefaults';

function instrument(instrumentId: string, venue = 'BINANCE'): CanonicalInstrument {
  return { instrument_id: instrumentId, asset_class: 'crypto', venue } as CanonicalInstrument;
}

describe('crypto instrument defaults', () => {
  it('normalizes Binance USD spot pairs to the supported USDT catalog pair', () => {
    const solUsd = instrument('crypto:BINANCE:spot:SOL-USD');
    const solUsdt = instrument('crypto:BINANCE:spot:SOL-USDT');

    expect(binanceInstrumentIdFor(solUsd.instrument_id)).toBe(solUsdt.instrument_id);
    expect(preferredCryptoInstrument(solUsd, [solUsd, solUsdt]).instrument_id).toBe(solUsdt.instrument_id);
  });

  it('keeps a supported instrument unchanged', () => {
    const btcUsdt = instrument('crypto:BINANCE:spot:BTC-USDT');

    expect(preferredCryptoInstrument(btcUsdt, [btcUsdt]).instrument_id).toBe(btcUsdt.instrument_id);
  });
});
