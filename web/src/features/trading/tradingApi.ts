import type {
  BarsResponse,
  CanonicalInstrument,
  ProviderDescriptor,
  TradingAlert,
  TradingAlertCreateInput,
  TradingAlertTrigger,
  TradingAlertUpdateInput,
  TradingDocument,
  TradingStreamMessage,
  MarketBar,
} from './tradingTypes';
import { decodeTradingFormula, evaluateTradingFormula, parseTradingFormula } from './tradingFormula';
import type { components } from './api/generated';
import { unwrapLabelled } from '../../api/http';
import { parseJson, tradingStreamMessageSchema } from '../../api/schemas/streams';
import { api } from './api/gateway';

export type TradingDocumentKind = 'workspaces' | 'watchlists' | 'watchlist-flags' | 'drawings' | 'indicator-presets' | 'scripts';

export type TradingCurrencyRate = components['schemas']['CurrencyRateResponse'];
export type TradingQuote = components['schemas']['QuoteResponse'];
export type TradingMarketStatus = components['schemas']['MarketStatusResponse'];

const trading = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading');

// The trading document families share one contract.
const DOCUMENT_PATHS = {
  workspaces: { list: '/api/trading/workspaces', record: '/api/trading/workspaces/{record_id}' },
  watchlists: { list: '/api/trading/watchlists', record: '/api/trading/watchlists/{record_id}' },
  'watchlist-flags': { list: '/api/trading/watchlist-flags', record: '/api/trading/watchlist-flags/{record_id}' },
  drawings: { list: '/api/trading/drawings', record: '/api/trading/drawings/{record_id}' },
  'indicator-presets': { list: '/api/trading/indicator-presets', record: '/api/trading/indicator-presets/{record_id}' },
  scripts: { list: '/api/trading/scripts', record: '/api/trading/scripts/{record_id}' },
} as const;

export function tradingStreamUrl(instrumentId: string, interval: string, bindingId?: string | null): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const query = new URLSearchParams({ instrument_id: instrumentId, interval });
  if (bindingId) query.set('binding_id', bindingId);
  return `${protocol}//${window.location.host}/api/trading/stream?${query.toString()}`;
}

export function subscribeTradingStream(
  instrumentId: string,
  interval: string,
  onMessage: (message: TradingStreamMessage) => void,
  onStatus?: (status: 'connecting' | 'live' | 'closed' | 'error') => void,
  bindingId?: string | null,
): () => void {
  onStatus?.('connecting');
  const socket = new WebSocket(tradingStreamUrl(instrumentId, interval, bindingId));
  socket.addEventListener('open', () => onStatus?.('live'));
  socket.addEventListener('message', (event) => {
    onMessage((parseJson(tradingStreamMessageSchema, String(event.data)) as TradingStreamMessage | null)
      ?? { type: 'error', code: 'invalid_stream_message', message: 'Trading stream returned an invalid message.' });
  });
  socket.addEventListener('error', () => onStatus?.('error'));
  socket.addEventListener('close', () => onStatus?.('closed'));
  return () => socket.close(1000, 'chart disposed');
}

/** Chart-only bar options (TVP-2.5): clock-aligned derived intervals, with or without extended hours. */
export type TradingBarsOptions = { alignment?: 'clock'; extendedHours?: boolean };

function barsQuery(instrumentId: string, interval: string, limit: number, bindingId?: string | null, options?: TradingBarsOptions) {
  return {
    instrument_id: instrumentId,
    interval,
    limit,
    ...(bindingId ? { binding_id: bindingId } : {}),
    ...(options?.alignment ? { alignment: options.alignment, extended_hours: options.extendedHours ?? true } : {}),
  };
}

const DOCUMENT_PAGE_SIZE = 500;

function formulaInstrument(instrumentId: string, expression: string, source: CanonicalInstrument): CanonicalInstrument & { name: string | null } {
  return {
    ...source,
    name: null,
    instrument_id: instrumentId,
    display_symbol: expression,
    venue_symbol: expression,
    venue: 'DERIVED',
    instrument_type: 'index',
    base_currency: null,
    quote_currency: null,
    minimum_tick: '0.00000001',
    price_scale: 100,
  };
}

function formulaBarNumber(bar: MarketBar | undefined, field: 'open' | 'high' | 'low' | 'close' | 'volume'): number | null {
  const value = Number(bar?.[field]);
  return Number.isFinite(value) ? value : null;
}

function normalizedFormulaSymbol(value: string): string {
  return value.toUpperCase().replace(/[-:_/]/g, '');
}

function isCanonicalFormulaOperand(value: string): boolean {
  return /^(crypto|equity):/i.test(value);
}

async function resolveFormulaOperand(symbol: string, operandId: string): Promise<string> {
  if (isCanonicalFormulaOperand(operandId)) return operandId;

  const candidates = (await trading(api.GET('/api/trading/instruments/search', {
    params: { query: { query: operandId || symbol } },
  }))).instruments;
  const normalized = normalizedFormulaSymbol(operandId || symbol);
  const match = candidates.find((candidate) => [
    candidate.display_symbol,
    candidate.venue_symbol,
    candidate.instrument_id,
  ].some((value) => normalizedFormulaSymbol(value) === normalized));
  if (!match) throw new Error(`Arithmetic chart symbol could not be resolved: ${symbol}`);
  return match.instrument_id;
}

async function formulaBars(
  instrumentId: string,
  interval: string,
  limit: number,
): Promise<BarsResponse> {
  const payload = decodeTradingFormula(instrumentId);
  if (!payload) throw new Error('Invalid arithmetic chart formula.');
  const formula = parseTradingFormula(payload.expression, { symbolHints: Object.keys(payload.operands) });
  if (!formula) throw new Error('Invalid arithmetic chart formula.');

  const operandIds = await Promise.all(formula.symbols.map((symbol) => resolveFormulaOperand(
    symbol,
    payload.operands[symbol] ?? symbol,
  )));
  const responses: BarsResponse[] = await Promise.all(operandIds.map((operandId) => trading(api.GET('/api/trading/bars', {
    params: { query: barsQuery(operandId, interval, limit) },
  }))));
  const source = responses[0];
  if (!source) throw new Error('Arithmetic chart formula has no market data.');

  const operandBars = new Map(formula.symbols.map((symbol, index) => [symbol, responses[index]?.bars ?? []]));
  const cursors = new Map(formula.symbols.map((symbol) => [symbol, 0]));
  const valueAt = (symbol: string, time: number, field: 'open' | 'high' | 'low' | 'close'): number | null => {
    const bars = operandBars.get(symbol) ?? [];
    if (bars.length === 0) return null;
    let cursor = cursors.get(symbol) ?? 0;
    while (cursor + 1 < bars.length && Date.parse(bars[cursor + 1].start_time) <= time) cursor += 1;
    cursors.set(symbol, cursor);
    if (Date.parse(bars[cursor]?.start_time ?? '') > time) return null;
    return formulaBarNumber(bars[cursor], field);
  };

  const bars = source.bars.flatMap((sourceBar) => {
    const time = Date.parse(sourceBar.start_time);
    if (!Number.isFinite(time)) return [];
    const values = (field: 'open' | 'high' | 'low' | 'close') => evaluateTradingFormula(
      formula.root,
      (symbol) => valueAt(symbol, time, field),
    );
    const open = values('open');
    const high = values('high');
    const low = values('low');
    const close = values('close');
    if ([open, high, low, close].some((value) => value === null)) return [];
    const normalizedValues = [open as number, high as number, low as number, close as number];
    const volume = formula.symbols.reduce((sum, symbol) => sum + (formulaBarNumber(
      operandBars.get(symbol)?.[cursors.get(symbol) ?? 0],
      'volume',
    ) ?? 0), 0);
    return [{
      ...sourceBar,
      instrument_id: instrumentId,
      open: String(normalizedValues[0]),
      high: String(Math.max(...normalizedValues)),
      low: String(Math.min(...normalizedValues)),
      close: String(normalizedValues[3]),
      volume: String(Math.max(0, volume)),
      provider: 'DERIVED',
      provider_event_id: null,
      provider_sequence: null,
      received_at: sourceBar.received_at ?? source.provenance.received_at,
    }];
  });

  const binding = {
    ...source.binding,
    binding_id: `formula:${source.binding.binding_id}`,
    instrument_id: instrumentId,
    provider: 'DERIVED',
    provider_symbol: payload.expression,
    realtime_scope: 'none',
  };
  return {
    ...source,
    bars,
    binding,
    instrument: formulaInstrument(instrumentId, payload.expression, source.instrument),
    provenance: { ...source.provenance, instrument_id: instrumentId },
  };
}

export const tradingApi = {
  providers: async (): Promise<ProviderDescriptor[]> =>
    (await trading(api.GET('/api/trading/providers/status'))).providers,
  instruments: async (query = ''): Promise<CanonicalInstrument[]> =>
    (await trading(api.GET('/api/trading/instruments/search', { params: { query: { query } } }))).instruments,
  bars: (instrumentId: string, interval: string, limit = 1_000, bindingId?: string | null, options?: TradingBarsOptions): Promise<BarsResponse> => {
    if (decodeTradingFormula(instrumentId)) return formulaBars(instrumentId, interval, limit);
    return trading(api.GET('/api/trading/bars', { params: { query: barsQuery(instrumentId, interval, limit, bindingId, options) } }));
  },
  /** Lower-timeframe bars inside chart bars (TVP-0.6); see intrabarData.ts. */
  intrabars: (request: { instrumentId: string; bindingId?: string | null; interval: string; lowerInterval: string; start: number; end: number }) =>
    trading(api.GET('/api/trading/bars/intrabar', {
      params: {
        query: {
          instrument_id: request.instrumentId,
          interval: request.interval,
          lower_interval: request.lowerInterval,
          start: new Date(request.start).toISOString(),
          end: new Date(request.end).toISOString(),
          ...(request.bindingId ? { binding_id: request.bindingId } : {}),
        },
      },
    })),
  quote: (instrumentId: string, bindingId?: string | null): Promise<TradingQuote> =>
    trading(api.GET('/api/trading/quotes', {
      params: { query: { instrument_id: instrumentId, ...(bindingId ? { binding_id: bindingId } : {}) } },
    })),
  currencyRate: (baseCurrency: string, quoteCurrency: string): Promise<TradingCurrencyRate> =>
    trading(api.GET('/api/trading/currency-rates', { params: { query: { base_currency: baseCurrency, quote_currency: quoteCurrency } } })),
  marketStatus: (instrumentId: string): Promise<TradingMarketStatus> =>
    trading(api.GET('/api/trading/market-status', { params: { query: { instrument_id: instrumentId } } })),
  diagnostics: () => trading(api.GET('/api/trading/diagnostics')),
  documents: async (kind: TradingDocumentKind): Promise<TradingDocument[]> =>
    (await trading(api.GET(DOCUMENT_PATHS[kind].list))).records,
  /** Every document of a kind, page by page (newest first). */
  allDocuments: async (kind: TradingDocumentKind): Promise<TradingDocument[]> => {
    const records: TradingDocument[] = [];
    let cursor: { after_updated_at: string; after_record_id: string } | null = null;
    for (;;) {
      const query = { limit: DOCUMENT_PAGE_SIZE, ...(cursor ?? {}) };
      const page: TradingDocument[] = (await trading(api.GET(DOCUMENT_PATHS[kind].list, { params: { query } }))).records;
      records.push(...page);
      const last = page.at(-1);
      if (page.length < DOCUMENT_PAGE_SIZE || !last?.updated_at) return records;
      cursor = { after_updated_at: last.updated_at, after_record_id: last.record_id };
    }
  },
  /** A chart image behind a link (TVP-2.1/2.5): the link opens for signed-in members of the workspace. */
  createSnapshot: (image: string, instrumentId: string, interval: string): Promise<components['schemas']['Snapshot']> =>
    trading(api.POST('/api/trading/snapshots', { body: { image, instrument_id: instrumentId, interval } })),
  document: (kind: TradingDocumentKind, recordId: string): Promise<TradingDocument> =>
    trading(api.GET(DOCUMENT_PATHS[kind].record, { params: { path: { record_id: recordId } }, cache: 'no-store' })),
  createDocument: (kind: TradingDocumentKind, recordId: string, payload: Record<string, unknown>): Promise<TradingDocument> =>
    trading(api.POST(DOCUMENT_PATHS[kind].list, { body: { record_id: recordId, payload } })),
  updateDocument: (kind: TradingDocumentKind, record: TradingDocument, payload: Record<string, unknown>): Promise<TradingDocument> =>
    trading(api.PUT(DOCUMENT_PATHS[kind].record, {
      params: { path: { record_id: record.record_id }, header: { 'If-Match': record.revision } },
      body: { record_id: record.record_id, payload },
    })),
  archiveDocument: (kind: TradingDocumentKind, record: TradingDocument): Promise<TradingDocument> =>
    trading(api.DELETE(DOCUMENT_PATHS[kind].record, {
      params: { path: { record_id: record.record_id }, header: { 'If-Match': record.revision } },
    })),
  alerts: async (): Promise<TradingAlert[]> =>
    (await trading(api.GET('/api/trading/alerts', { cache: 'no-store' }))).alerts,
  /** The indicators the server evaluates for alerts (TVP-1.3). */
  alertIndicators: async (): Promise<string[]> =>
    (await trading(api.GET('/api/trading/alerts/indicators'))).indicator_ids,
  /** The latest value of indicator lines for a list of symbols: the watchlist's indicator columns (TVP-5.2). */
  indicatorValues: async (
    instrumentIds: readonly string[],
    interval: string,
    lines: ReadonlyArray<{ indicator_id: string; period: number; output: string }>,
  ): Promise<Record<string, Array<string | number | null>>> =>
    (await trading(api.POST('/api/trading/indicators/latest', {
      body: {
        instrument_ids: [...instrumentIds],
        interval,
        lines: lines.map((line) => ({ kind: 'indicator' as const, indicator_id: line.indicator_id, inputs: { period: line.period }, output: line.output })),
      },
    }))).values,
  alertTriggers: async (): Promise<TradingAlertTrigger[]> =>
    (await trading(api.GET('/api/trading/alerts/triggers', { cache: 'no-store' }))).triggers,
  /** How many of a watchlist's symbols an alert on it evaluates (TVP-1.7). */
  watchlistAlertCapacity: (watchlistId: string) =>
    trading(api.GET('/api/trading/alerts/watchlist-capacity', { params: { query: { watchlist_id: watchlistId } } })),
  createAlert: (input: TradingAlertCreateInput): Promise<TradingAlert> =>
    trading(api.POST('/api/trading/alerts', { body: input })),
  updateAlert: (alert: TradingAlert, input: TradingAlertUpdateInput): Promise<TradingAlert> =>
    trading(api.PUT('/api/trading/alerts/{alert_id}', {
      params: { path: { alert_id: alert.alert_id }, header: { 'If-Match': alert.revision } },
      body: input,
    })),
  archiveAlert: (alert: TradingAlert): Promise<TradingAlert> =>
    trading(api.DELETE('/api/trading/alerts/{alert_id}', {
      params: { path: { alert_id: alert.alert_id }, header: { 'If-Match': alert.revision } },
    })),
  evaluateAlerts: async (instrumentId: string, observedPrice: string, observedAt?: string): Promise<TradingAlertTrigger[]> =>
    (await trading(api.POST('/api/trading/alerts/evaluate', {
      body: {
        instrument_id: instrumentId,
        observed_price: observedPrice,
        ...(observedAt ? { observed_at: observedAt } : {}),
      },
    }))).triggers,
};
