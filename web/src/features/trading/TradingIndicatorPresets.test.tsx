import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TradingIndicatorPresets } from './TradingIndicatorPresets';
import { tradingApi } from './tradingApi';
import { chartTemplatePayload } from './tradingChartWorkflow';
import type { TradingDocument } from './tradingTypes';

const record = (recordId: string, payload: Record<string, unknown>) => ({
  record_id: recordId, record_type: 'indicator_preset', revision: 1, status: 'active', payload,
}) as TradingDocument;

describe('indicator presets panel', () => {
  afterEach(() => vi.restoreAllMocks());

  it('lists indicator presets but not chart templates (TVP-2.5)', async () => {
    vi.spyOn(tradingApi, 'documents').mockResolvedValue([
      record('preset-1', { name: 'Trend preset', indicators: [{ id: 'sma', period: 20, enabled: true }] }),
      record('template-1', chartTemplatePayload({ name: 'Momentum template', chartType: 'line', indicators: [] })),
    ]);
    render(<TradingIndicatorPresets indicators={[]} onApply={vi.fn()} />);
    expect(await screen.findByRole('button', { name: 'Trend preset' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Momentum template' })).toBeNull();
  });
});
