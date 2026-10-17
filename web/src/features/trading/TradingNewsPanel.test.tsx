import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const hermesApi = vi.hoisted(() => ({
  start: vi.fn(),
  audit: vi.fn(),
}));

vi.mock('./tradingHermesResearchApi', () => ({ tradingHermesResearchApi: hermesApi }));

import { TradingNewsPanel } from './TradingNewsPanel';

describe('TradingNewsPanel', () => {
  afterEach(() => vi.clearAllMocks());

  it('does not research on mount and runs Hermes only on demand', async () => {
    hermesApi.start.mockResolvedValue({
      planner_backend: 'hermes',
      report: {
        source_evidence_ids: ['evidence-1'],
        research_status: 'complete',
        catalyst_status: 'confirmed',
      },
    });
    hermesApi.audit.mockResolvedValue({
      as_of: '2026-08-25T21:00:00Z',
      evidence: [{
        evidence_id: 'evidence-1',
        source_type: 'web',
        source_locator: 'https://example.test/nvda-news',
        source_published_at: '2026-08-25T20:55:00Z',
        captured_at: '2026-08-25T21:00:00Z',
        title: 'NVDA reports strong demand',
      }],
      latest_report: {
        research_status: 'complete',
        catalyst_status: 'confirmed',
      },
    });

    render(<TradingNewsPanel instrumentId="equity:NASDAQ:NVDA" />);

    expect(hermesApi.start).not.toHaveBeenCalled();
    expect(hermesApi.audit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Research with Hermes' }));

    expect(await screen.findByText('Sources used')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'NVDA reports strong demand' })).toHaveAttribute(
      'href',
      'https://example.test/nvda-news',
    );
    expect(hermesApi.start).toHaveBeenCalledWith('equity:NASDAQ:NVDA');
    expect(hermesApi.audit).toHaveBeenCalledWith('equity:NASDAQ:NVDA');
  });

  it('reports an empty result instead of presenting it as ready research', async () => {
    hermesApi.start.mockResolvedValue({
      planner_backend: 'local_safe_stop',
      report: {
        source_evidence_ids: [],
        research_status: 'failed',
        catalyst_status: 'unresolved',
      },
    });
    hermesApi.audit.mockResolvedValue({ as_of: '2026-08-25T21:00:00Z', evidence: [], latest_report: null });

    render(<TradingNewsPanel instrumentId="equity:NASDAQ:NVDA" />);
    fireEvent.click(screen.getByRole('button', { name: 'Research with Hermes' }));

    expect(await screen.findByText('No sources')).toBeInTheDocument();
    expect(screen.getByText(/no usable sources were returned/i)).toBeInTheDocument();
  });

  it('shows the configured-AI market brief with citations after on-demand research', async () => {
    hermesApi.start.mockResolvedValue({
      planner_backend: 'hermes',
      report: {
        source_evidence_ids: ['evidence-1'],
        research_status: 'partial',
        catalyst_status: 'confirmed',
      },
      brief_warning: null,
      brief: {
        instrument_id: 'equity:NASDAQ:NVDA',
        generated_at: '2026-08-25T21:00:00Z',
        provider: 'configured-provider',
        model: 'configured-model',
        headline: 'Demand update remains the current catalyst',
        summary: 'The research sources point to a current demand update while financing coverage remains unresolved.',
        key_points: [{
          text: 'The company update identifies demand as the current catalyst.',
          source_evidence_ids: ['evidence-1'],
        }],
        risks: [],
        watch_items: [],
        confidence: 'medium',
        source_evidence_ids: ['evidence-1'],
        read_only: true,
        disclaimer: 'AI-generated research summary only. Not financial advice. No order was created or executed.',
      },
    });
    hermesApi.audit.mockResolvedValue({
      as_of: '2026-08-25T21:00:00Z',
      evidence: [{
        evidence_id: 'evidence-1',
        source_type: 'web',
        source_locator: 'https://example.test/nvda-news',
        source_published_at: '2026-08-25T20:55:00Z',
        captured_at: '2026-08-25T21:00:00Z',
        title: 'NVDA reports strong demand',
      }],
      latest_report: {
        research_status: 'partial',
        catalyst_status: 'confirmed',
      },
    });

    render(<TradingNewsPanel instrumentId="equity:NASDAQ:NVDA" />);
    fireEvent.click(screen.getByRole('button', { name: 'Research with Hermes' }));

    expect(await screen.findByText('AI market brief')).toBeInTheDocument();
    expect(screen.getByText('Demand update remains the current catalyst')).toBeInTheDocument();
    expect(screen.getByText(/financing coverage remains unresolved/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Source' })).toHaveAttribute('href', 'https://example.test/nvda-news');
    expect(screen.getByText(/Generated with your configured AI/i)).toBeInTheDocument();
  });

  it('clears completed research when the active symbol changes', async () => {
    hermesApi.start.mockResolvedValue({
      planner_backend: 'hermes',
      report: {
        source_evidence_ids: ['evidence-1'],
        research_status: 'complete',
        catalyst_status: 'confirmed',
      },
    });
    hermesApi.audit.mockResolvedValue({
      as_of: '2026-08-25T21:00:00Z',
      evidence: [{
        evidence_id: 'evidence-1',
        source_type: 'web',
        source_locator: 'https://example.test/nvda-news',
        source_published_at: '2026-08-25T20:55:00Z',
        captured_at: '2026-08-25T21:00:00Z',
        title: 'NVDA report',
      }],
      latest_report: null,
    });

    const { rerender } = render(<TradingNewsPanel instrumentId="equity:NASDAQ:NVDA" />);
    fireEvent.click(screen.getByRole('button', { name: 'Research with Hermes' }));
    expect(await screen.findByText('NVDA report')).toBeInTheDocument();

    rerender(<TradingNewsPanel instrumentId="equity:NYSE:GME" />);

    expect(screen.queryByText('NVDA report')).not.toBeInTheDocument();
    expect(screen.getByText('On demand')).toBeInTheDocument();
    expect(screen.getByText('Research runs only when you request it.')).toBeInTheDocument();
  });

  it('ignores a research completion from a symbol that is no longer active', async () => {
    let resolveStart!: (value: unknown) => void;
    hermesApi.start.mockReturnValue(new Promise((resolve) => { resolveStart = resolve; }));
    hermesApi.audit.mockResolvedValue({
      as_of: '2026-08-25T21:00:00Z',
      evidence: [],
      latest_report: null,
    });

    const { rerender } = render(<TradingNewsPanel instrumentId="equity:NASDAQ:NVDA" />);
    fireEvent.click(screen.getByRole('button', { name: 'Research with Hermes' }));
    rerender(<TradingNewsPanel instrumentId="equity:NYSE:GME" />);

    resolveStart({
      planner_backend: 'hermes',
      report: {
        source_evidence_ids: ['evidence-1'],
        research_status: 'complete',
        catalyst_status: 'confirmed',
      },
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(hermesApi.audit).not.toHaveBeenCalled();
    expect(screen.getByText('On demand')).toBeInTheDocument();
  });
});
