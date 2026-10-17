import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from './chart/chartAdapter';
import {
  ChartCopyImageButton,
  ChartGoToDate,
  ChartMarketStatusBadges,
  ChartWorkflowSettings,
  TradingBarCountdown,
} from './TradingChartWorkflowControls';
import type { MarketBar } from './tradingTypes';
import type { TradingChartPanelModel } from './useTradingChartPanel';
import { fixture } from '../../test/fixture';

const model = (overrides: Record<string, unknown>) => fixture<TradingChartPanelModel>(overrides);

describe('chart workflow controls (TVP-2.5)', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the time to bar close under the last-price label', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-08T14:58:30Z'));
    const adapter = fixture<TradingChartAdapter>({
      onViewportChange: () => () => undefined,
      lastPriceLabelPosition: () => ({ y: 120, color: '#20c997', side: 'right', scaleWidth: 64, paneHeight: 400 }),
    });
    const bar = fixture<MarketBar>({ start_time: '2026-10-08T14:00:00Z', end_time: '2026-10-08T15:00:00Z' });
    render(<TradingBarCountdown adapter={adapter} bar={bar} interval="1h" />);
    const timer = screen.getByRole('timer', { name: 'Bar closes in 01:30' });
    expect(timer).toHaveStyle({ top: '130px', right: '0px', width: '64px' });
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(screen.getByRole('timer')).toHaveTextContent('01:00');
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.queryByRole('timer')).toBeNull();
  });

  it('submits a typed date and time to go to', () => {
    const goToDate = vi.fn();
    const ws = model({
      goToDate,
      goToDateDefault: () => '2026-10-02',
      goToDateError: null,
      goToDateLoading: false,
      goToDateOpen: true,
      openGoToDate: vi.fn(),
      replayMode: false,
      setGoToDateOpen: vi.fn(),
    });
    render(<ChartGoToDate ws={ws} />);
    const form = screen.getByRole('form', { name: 'Go to date' });
    expect(screen.getByLabelText('Date')).toHaveValue('2026-10-02');
    fireEvent.change(screen.getByLabelText('Time'), { target: { value: '09:30' } });
    fireEvent.submit(form);
    expect(goToDate).toHaveBeenCalledWith('2026-10-02T09:30');
  });

  it('opens go to date from its button and shows loading and errors', () => {
    const openGoToDate = vi.fn();
    const { rerender } = render(<ChartGoToDate ws={model({ goToDateOpen: false, openGoToDate, replayMode: false, setGoToDateOpen: vi.fn(), goToDateDefault: () => '' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Go to date' }));
    expect(openGoToDate).toHaveBeenCalled();
    rerender(<ChartGoToDate ws={model({
      goToDate: vi.fn(), goToDateDefault: () => '2026-10-02', goToDateError: 'No earlier history is available; the chart starts 2026-01-01.',
      goToDateLoading: true, goToDateOpen: true, openGoToDate, replayMode: false, setGoToDateOpen: vi.fn(),
    })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading older history');
    expect(screen.getByRole('alert')).toHaveTextContent('chart starts 2026-01-01');
  });

  it('reports the copy result on the copy button', () => {
    const copyChartImage = vi.fn().mockResolvedValue(true);
    const { rerender } = render(<ChartCopyImageButton ws={model({ chartImageCopyStatus: 'idle', copyChartImage })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy chart image' }));
    expect(copyChartImage).toHaveBeenCalled();
    rerender(<ChartCopyImageButton ws={model({ chartImageCopyStatus: 'copied', copyChartImage })} />);
    expect(screen.getByRole('button', { name: 'Copy chart image' })).toHaveTextContent('Copied');
  });

  it('shows market status and a delayed-data badge', () => {
    const { rerender } = render(<ChartMarketStatusBadges ws={model({ active: true, marketStatus: 'Post-market', marketStatusValue: 'post_market', dataDelay: 'Delayed 15 min' })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Post-market');
    expect(screen.getByRole('status')).toHaveAttribute('data-status', 'post_market');
    expect(screen.getByText('Delayed 15 min')).toBeInTheDocument();
    // Inactive charts show the badge without their own live region.
    rerender(<ChartMarketStatusBadges ws={model({ active: false, marketStatus: 'Post-market', marketStatusValue: 'post_market', dataDelay: null })} />);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByText('Post-market')).toBeInTheDocument();
  });

  it('toggles chart settings and saves and applies templates', async () => {
    const updateChartSettings = vi.fn();
    const saveChartTemplate = vi.fn().mockResolvedValue(true);
    const applyChartTemplateRecord = vi.fn();
    const template = { recordId: 't1', name: 'Momentum', chartType: 'line', settings: {}, indicators: [] };
    render(<ChartWorkflowSettings ws={model({
      applyChartTemplateRecord, barCountdownAvailable: true, barCountdownOn: true, chartSettings: undefined,
      chartId: 'chart-1', chartTemplateStatus: 'idle', chartTemplates: [template], deleteChartTemplate: vi.fn(), extendedHoursAvailable: true,
      eventMarkersAvailable: true, eventMarkersOn: true,
      loadChartTemplates: vi.fn(), saveChartTemplate, sessionTaggedBars: true, showExtendedHours: true, updateChartSettings,
    })} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Countdown to bar close' }));
    expect(updateChartSettings).toHaveBeenLastCalledWith({ barCountdown: false });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Extended hours (pre/post-market)' }));
    expect(updateChartSettings).toHaveBeenLastCalledWith({ extendedHours: false });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pre/post-market price line' }));
    expect(updateChartSettings).toHaveBeenLastCalledWith({ extendedPriceLine: false });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Earnings, dividends and splits' }));
    expect(updateChartSettings).toHaveBeenLastCalledWith({ events: false });
    fireEvent.change(screen.getByRole('textbox', { name: 'Template name' }), { target: { value: 'Swing' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(saveChartTemplate).toHaveBeenCalledWith('Swing');
    fireEvent.click(screen.getByRole('button', { name: 'Momentum' }));
    expect(applyChartTemplateRecord).toHaveBeenCalledWith(template);
  });

  it('disables the extended-hours toggle, with the reason, when the feed has no sessions', () => {
    render(<ChartWorkflowSettings ws={model({
      applyChartTemplateRecord: vi.fn(), barCountdownAvailable: true, barCountdownOn: true, chartId: 'chart-1', chartSettings: undefined,
      chartTemplateStatus: 'idle', chartTemplates: [], deleteChartTemplate: vi.fn(), extendedHoursAvailable: false,
      loadChartTemplates: vi.fn(), saveChartTemplate: vi.fn(), sessionTaggedBars: false, showExtendedHours: true, updateChartSettings: vi.fn(),
    })} />);
    const toggle = screen.getByRole('checkbox', { name: 'Extended hours (pre/post-market)' });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAccessibleDescription(/without exchange sessions/);
  });
});
