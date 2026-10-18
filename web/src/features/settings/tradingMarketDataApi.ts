import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';
export type CoinMarketCapCredentialStatus = components['schemas']['CoinMarketCapCredentialStatus'];
export type ProviderKeyStatus = components['schemas']['ProviderKeyStatus'];

/** How Omnix reaches IBKR: a running IB Gateway, or IBKR's Web API with OAuth keys. */
export type IbkrConnection = 'gateway' | 'web_api';

export type IbkrSettings = {
  enabled: boolean;
  monitor_enabled: boolean;
  host: string;
  port: number;
  client_id: number;
  live_authority_enabled: boolean;
  recovery_authority_enabled: boolean;
  connection: IbkrConnection;
};

export type IbkrSettingsStatus = components['schemas']['IbkrSettingsStatus'];
export type IbkrWebApiStatus = components['schemas']['IbkrWebApiStatus'];
export type IbkrWebApiPublicKeys = components['schemas']['IbkrWebApiPublicKeys'];
export type IbkrWebApiCredentialUpdate = components['schemas']['IbkrWebApiCredentialUpdate'];
export type AlpacaCredentialStatus = components['schemas']['AlpacaIexCredentialStatus'];
export type AlpacaCredentialUpdate = components['schemas']['AlpacaIexCredentialUpdate'];

const COINMARKETCAP_CREDENTIALS = '/api/trading/market-data/providers/coinmarketcap/credentials';
const IBKR_SETTINGS = '/api/trading/market-data/providers/ibkr/settings';
const IBKR_CONNECT = '/api/trading/market-data/providers/ibkr/connect';
const IBKR_WEB_API_KEYS = '/api/trading/market-data/providers/ibkr/web-api/keys';
const IBKR_WEB_API_CREDENTIALS = '/api/trading/market-data/providers/ibkr/web-api/credentials';
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
  connectIbkr: (): Promise<IbkrSettingsStatus> => marketData(api.POST(IBKR_CONNECT)),
  ibkrWebApiKeys: (): Promise<IbkrWebApiPublicKeys> => marketData(api.GET(IBKR_WEB_API_KEYS)),
  generateIbkrWebApiKeys: (replace: boolean): Promise<IbkrWebApiPublicKeys> =>
    marketData(api.POST(IBKR_WEB_API_KEYS, { body: { replace } })),
  saveIbkrWebApiCredentials: (input: IbkrWebApiCredentialUpdate): Promise<IbkrSettingsStatus> =>
    marketData(api.PUT(IBKR_WEB_API_CREDENTIALS, { body: input })),
};
