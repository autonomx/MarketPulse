import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushTradingDrawingSaves, tradingDrawingRecordId } from '../drawings/useTradingDrawings';
import { api } from '../api/gateway';
import { tradingApi } from '../tradingApi';
import type { TradingDocument } from '../tradingTypes';
import { copyWorkspaceDrawings, workspaceDrawingCopies } from './duplicateWorkspace';

vi.mock('../drawings/useTradingDrawings', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../drawings/useTradingDrawings')>()),
  flushTradingDrawingSaves: vi.fn().mockResolvedValue(undefined),
}));

function drawingRecord(workspaceId: string, tabId: string, instrumentId: string, status = 'active', drawings: unknown[] = [{ drawingId: 'd1', instrumentId }]): TradingDocument {
  return {
    record_id: tradingDrawingRecordId(instrumentId, `${workspaceId}:${tabId}`),
    record_type: 'drawing',
    payload: { instrumentId, drawings },
    revision: 3,
    status,
    updated_at: '2026-10-08T00:00:00Z',
  } as TradingDocument;
}

describe('duplicate layout drawings (TVP-2.5)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('maps each tab drawing document of the source workspace to the duplicate', () => {
    const records = [
      drawingRecord('main', 'tab-1', 'crypto:BINANCE:spot:BTC-USDT'),
      drawingRecord('main', 'tab-2', 'equity:NASDAQ:AAPL'),
      drawingRecord('main', 'tab-1', 'crypto:BINANCE:spot:ETH-USDT', 'archived'),
      drawingRecord('main', 'tab-1', 'crypto:BINANCE:spot:SOL-USDT', 'active', []),
      // A workspace whose id starts with the source id must not be copied.
      drawingRecord('main-copy-1234abcd', 'tab-1', 'crypto:BINANCE:spot:BTC-USDT'),
      drawingRecord('other', 'tab-1', 'crypto:BINANCE:spot:BTC-USDT'),
    ];
    const copies = workspaceDrawingCopies(records, 'main', 'main-copy-9999', ['tab-1', 'tab-2']);
    expect(copies.map((copy) => copy.recordId)).toEqual([
      tradingDrawingRecordId('crypto:BINANCE:spot:BTC-USDT', 'main-copy-9999:tab-1'),
      tradingDrawingRecordId('equity:NASDAQ:AAPL', 'main-copy-9999:tab-2'),
    ]);
    expect(copies[0].payload).toEqual(records[0].payload);
    expect(copies[0].payload).not.toBe(records[0].payload);
  });

  it('keeps the drawings a chart kept alone while drawings did not sync (TVP-3.8)', () => {
    // The tab scope 'tab-1' with a chart scope, as drawingScopeId builds it.
    const chartScoped = drawingRecord('main', 'tab-1:chart:chart-2', 'equity:NASDAQ:AAPL');
    const copies = workspaceDrawingCopies([chartScoped, drawingRecord('main', 'tab-1', 'equity:NASDAQ:AAPL')], 'main', 'main-copy', ['tab-1']);
    expect(copies.map((copy) => copy.recordId)).toEqual([
      tradingDrawingRecordId('equity:NASDAQ:AAPL', 'main-copy:tab-1:chart:chart-2'),
      tradingDrawingRecordId('equity:NASDAQ:AAPL', 'main-copy:tab-1'),
    ]);
  });

  it('creates the copies through the drawing documents API and skips ones that exist', async () => {
    const source = drawingRecord('main', 'tab-1', 'crypto:BINANCE:spot:BTC-USDT');
    const existing = drawingRecord('copy', 'tab-1', 'equity:NASDAQ:AAPL');
    const alsoSource = drawingRecord('main', 'tab-1', 'equity:NASDAQ:AAPL');
    vi.spyOn(tradingApi, 'allDocuments').mockResolvedValue([source, existing, alsoSource]);
    const create = vi.spyOn(tradingApi, 'createDocument').mockImplementation(async (_kind, recordId, payload) => ({ ...source, record_id: recordId, payload }));
    await expect(copyWorkspaceDrawings('main', 'copy', ['tab-1'])).resolves.toBe(1);
    expect(flushTradingDrawingSaves).toHaveBeenCalled();
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith('drawings', tradingDrawingRecordId('crypto:BINANCE:spot:BTC-USDT', 'copy:tab-1'), source.payload);
  });

  it('reads every drawing document, page by page', async () => {
    const page = (count: number, offset: number) => Array.from({ length: count }, (_, index) => ({
      ...drawingRecord('main', 'tab-1', `crypto:BINANCE:spot:C${offset + index}-USDT`),
      updated_at: `2026-10-08T00:00:${String(59 - Math.floor((offset + index) / 20)).padStart(2, '0')}Z`,
    }));
    const pages = [page(500, 0), page(3, 500)];
    const get = vi.spyOn(api, 'GET').mockImplementation((async (_path: string, init: { params: { query: Record<string, unknown> } }) => ({
      data: { records: pages[get.mock.calls.length - 1] },
      response: new Response(null, { status: 200 }),
      query: init.params.query,
    })) as never);
    const records = await tradingApi.allDocuments('drawings');
    expect(records).toHaveLength(503);
    expect(get).toHaveBeenCalledTimes(2);
    const secondQuery = (get.mock.calls[1][1] as { params: { query: Record<string, unknown> } }).params.query;
    expect(secondQuery).toEqual({ limit: 500, after_updated_at: pages[0][499].updated_at, after_record_id: pages[0][499].record_id });
  });
});
