import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';

export type AlpacaIexCredentialStatus = components['schemas']['AlpacaIexCredentialStatus'];
type AlpacaIexCredentialUpdate = components['schemas']['AlpacaIexCredentialUpdate'];

const ALPACA_CREDENTIALS = '/api/trading/execution/providers/alpaca-iex/credentials';

export const tradingExecutionApi = {
  alpacaCredentials: (): Promise<AlpacaIexCredentialStatus> =>
    unwrapLabelled(api.GET(ALPACA_CREDENTIALS), 'Trading execution'),
  saveAlpacaCredentials: (input: AlpacaIexCredentialUpdate): Promise<AlpacaIexCredentialStatus> =>
    unwrapLabelled(api.PUT(ALPACA_CREDENTIALS, { body: input }), 'Trading execution'),
};
