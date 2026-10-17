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

const COINMARKETCAP_CREDENTIALS = '/api/trading/market-data/providers/coinmarketcap/credentials';
const IBKR_SETTINGS = '/api/trading/market-data/providers/ibkr/settings';
const FRED_CREDENTIALS = '/api/trading/market-data/providers/fred/credentials';
const marketData = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading market-data');

export const tradingMarketDataApi = {
  coinmarketcapCredentials: (): Promise<CoinMarketCapCredentialStatus> => marketData(api.GET(COINMARKETCAP_CREDENTIALS)),
  saveCoinMarketCapCredentials: (input: components['schemas']['CoinMarketCapCredentialUpdate']): Promise<CoinMarketCapCredentialStatus> =>
    marketData(api.PUT(COINMARKETCAP_CREDENTIALS, { body: input })),
  fredCredentials: (): Promise<ProviderKeyStatus> => marketData(api.GET(FRED_CREDENTIALS)),
  saveFredCredentials: (input: components['schemas']['CoinMarketCapCredentialUpdate']): Promise<ProviderKeyStatus> =>
    marketData(api.PUT(FRED_CREDENTIALS, { body: input })),
  ibkrSettings: (): Promise<IbkrSettingsStatus> => marketData(api.GET(IBKR_SETTINGS)),
  saveIbkrSettings: (input: Partial<IbkrSettings>): Promise<IbkrSettingsStatus> =>
    marketData(api.PUT(IBKR_SETTINGS, { body: input })),
};
