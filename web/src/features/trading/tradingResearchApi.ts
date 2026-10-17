import { unwrapLabelled } from '../../api/http';
import type { MarketResearchRequest, MarketResearchResult } from './researchTypes';
import { api } from './api/gateway';

export const tradingResearchApi = {
  generate: (request: MarketResearchRequest): Promise<MarketResearchResult> =>
    unwrapLabelled(api.POST('/api/trading/research', { body: request }), 'Trading research'),
};
