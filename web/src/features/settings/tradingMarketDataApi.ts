import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';
export type CoinMarketCapCredentialStatus = components['schemas']['CoinMarketCapCredentialStatus'];
export type ProviderKeyStatus = components['schemas']['ProviderKeyStatus'];

export type IbkrSettings = {
  enabled: boolean;
  monitor_enabled: boolean;
  host: string;
  port: number;
  client_id: number;
  live_authority_enabled: boolean;
  recovery_authority_enabled: boolean;
};

export type IbkrSettingsStatus = components['schemas']['IbkrSettingsStatus'];
export type AlpacaCredentialStatus = components['schemas']['AlpacaIexCredentialStatus'];
export type AlpacaCredentialUpdate = components['schemas']['AlpacaIexCredentialUpdate'];

const COINMARKETCAP_CREDENTIALS = '/api/trading/market-data/providers/coinmarketcap/credentials';
const IBKR_SETTINGS = '/api/trading/market-data/providers/ibkr/settings';
const FRED_CREDENTIALS = '/api/trading/market-data/providers/fred/credentials';
// The same keys AUTO PAPER's execution quotes use (the Strategies panel shows them too).
const ALPACA_CREDENTIALS = '/api/trading/execution/providers/alpaca-iex/credentials';
const marketData = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading market-data');

export const tradingMarketDataApi = {
  coinmarketcapCredentials: (): Promise<CoinMarketCapCredentialStatus> => marketData(api.GET(COINMARKETCAP_CREDENTIALS)),
  saveCoinMarketCapCredentials: (input: components['schemas']['CoinMarketCapCredentialUpdate']): Promise<CoinMarketCapCredentialStatus> =>
    marketData(api.PUT(COINMARKETCAP_CREDENTIALS, { body: input })),
  fredCredentials: (): Promise<ProviderKeyStatus> => marketData(api.GET(FRED_CREDENTIALS)),
  saveFredCredentials: (input: components['schemas']['CoinMarketCapCredentialUpdate']): Promise<ProviderKeyStatus> =>
    marketData(api.PUT(FRED_CREDENTIALS, { body: input })),
  alpacaCredentials: (): Promise<AlpacaCredentialStatus> => marketData(api.GET(ALPACA_CREDENTIALS)),
  saveAlpacaCredentials: (input: AlpacaCredentialUpdate): Promise<AlpacaCredentialStatus> =>
    marketData(api.PUT(ALPACA_CREDENTIALS, { body: input })),
  ibkrSettings: (): Promise<IbkrSettingsStatus> => marketData(api.GET(IBKR_SETTINGS)),
  saveIbkrSettings: (input: Partial<IbkrSettings>): Promise<IbkrSettingsStatus> =>
    marketData(api.PUT(IBKR_SETTINGS, { body: input })),
};
