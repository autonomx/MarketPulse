import { unwrapLabelled } from '../../api/http';
import type {
  TradingScannerDefinition,
  TradingScannerDefinitionInput,
  TradingScannerResult,
  TradingScannerRun,
} from './scannerTypes';
import { api } from './api/gateway';

const scanner = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Trading scanner');

export const tradingScannerApi = {
  /** Rules proposed for a description (TVP-9.4); a proposal to edit, never a run. */
  propose: (text: string) => scanner(api.POST('/api/trading/scanners/propose', { body: { text } })),
  definitions: async (): Promise<TradingScannerDefinition[]> =>
    (await scanner(api.GET('/api/trading/scanners'))).scanners,
  create: (definition: TradingScannerDefinitionInput): Promise<TradingScannerDefinition> =>
    scanner(api.POST('/api/trading/scanners', { body: definition })),
  update: (definition: TradingScannerDefinitionInput & { revision: number }): Promise<TradingScannerDefinition> =>
    scanner(api.PUT('/api/trading/scanners/{scanner_id}', {
      params: { path: { scanner_id: definition.scanner_id }, header: { 'If-Match': definition.revision } },
      body: definition,
    })),
  // The run routes return untyped objects; these are the fields the scanner returns.
  start: async (scannerId: string): Promise<TradingScannerRun> =>
    (await scanner(api.POST('/api/trading/scanners/{scanner_id}/runs', { params: { path: { scanner_id: scannerId } } }))) as unknown as TradingScannerRun,
  cancel: async (runId: string): Promise<{ ok: boolean; run_id: string; status: string }> =>
    (await scanner(api.POST('/api/trading/scanners/runs/{run_id}/cancel', { params: { path: { run_id: runId } } }))) as unknown as { ok: boolean; run_id: string; status: string },
  runs: async (scannerId?: string): Promise<TradingScannerRun[]> =>
    (await scanner(api.GET('/api/trading/scanners/runs', { params: { query: scannerId ? { scanner_id: scannerId } : {} } }))).runs,
  results: async (runId: string): Promise<TradingScannerResult[]> =>
    (await scanner(api.GET('/api/trading/scanners/runs/{run_id}/results', { params: { path: { run_id: runId } } }))).results,
};
