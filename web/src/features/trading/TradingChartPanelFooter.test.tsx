import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChartPanelFooter } from './TradingChartPanelFooter';
import { REPLAY_SPEEDS } from './replayClock';
import { useTradingReplayStore } from './tradingReplayStore';
import type { TradingChartPanelModel } from './useTradingChartPanel';
import { TRADING_TIMEZONE_OPTIONS } from './tradingTime';
import { fixture } from '../../test/fixture';

function footerModel(overrides: Record<string, unknown> = {}) {
  return fixture<TradingChartPanelModel>({
    active: true,
    allBarsRef: { current: new Array(40) },
    applyCustomRange: vi.fn(),
    chartId: 'chart-1',
    chartQuery: { data: undefined },
    customRangeEnd: '',
    customRangeError: null,
    customRangeOpen: false,
    customRangeRef: { current: null },
    customRangeStart: '',
    drawings: { status: 'saved' },
    exitReplay: vi.fn(),
    nextReplayBar: vi.fn(),
    openCustomRange: vi.fn(),
    previousReplayBar: vi.fn(),
    provenance: undefined,
    replayChoosingStart: false,
    replayCurrentBar: { end_time: '2026-08-05T12:15:00Z' },
    replayHasNextBar: true,
    replayHasPreviousBar: true,
    replayMode: true,
    replayPlaying: false,
    replayStartTime: Date.parse('2026-08-05T12:10:00Z'),
    replayVisibleBarCount: 12,
    resetReplay: vi.fn(),
    selectReplayStart: vi.fn(),
    selectedRangeLabel: 'All',
    selectedTimezone: 'UTC',
    selectedTimezoneOption: TRADING_TIMEZONE_OPTIONS[0],
    setCustomRangeEnd: vi.fn(),
    setCustomRangeOpen: vi.fn(),
    setCustomRangeStart: vi.fn(),
    setTimezoneId: vi.fn(),
    setTimezoneMenuOpen: vi.fn(),
    showRange: vi.fn(),
    streamStatus: 'replay',
    timezoneId: TRADING_TIMEZONE_OPTIONS[0].id,
    timezoneMenuOpen: false,
    timezoneMenuRef: { current: null },
    toggleReplayPlaying: vi.fn(),
    ...overrides,
  });
}

describe('chart footer replay controls', () => {
  beforeEach(() => {
    useTradingReplayStore.getState().clear();
    useTradingReplayStore.getState().setSpeed(1);
  });

  it('has one speed control with the nine fixed speeds, bound to the shared setting', () => {
    render(<ChartPanelFooter ws={footerModel()} />);
    const toolbar = screen.getByRole('group', { name: 'Chart replay controls' });
    const speed = within(toolbar).getByRole('combobox', { name: 'Replay speed' });
    expect(within(toolbar).getAllByRole('combobox')).toHaveLength(1);
    expect(within(speed).getAllByRole('option').map((option) => option.textContent)).toEqual(REPLAY_SPEEDS.map((value) => `${value}×`));
    expect(speed).toHaveValue('1');
    fireEvent.change(speed, { target: { value: '30' } });
    expect(useTradingReplayStore.getState().speed).toBe(30);
    expect(screen.getByText(/12\/40/)).toBeInTheDocument();
  });

  it('wires step, play, select-bar and real-time buttons to the replay actions', () => {
    const ws = footerModel();
    render(<ChartPanelFooter ws={ws} />);
    fireEvent.click(screen.getByRole('button', { name: 'Replay next bar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay previous bar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Play replay' }));
    fireEvent.click(screen.getByRole('button', { name: 'Choose replay start' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset replay' }));
    fireEvent.click(screen.getByRole('button', { name: 'Exit replay and return to real time' }));
    expect(ws.nextReplayBar).toHaveBeenCalledOnce();
    expect(ws.previousReplayBar).toHaveBeenCalledOnce();
    expect(ws.toggleReplayPlaying).toHaveBeenCalledOnce();
    expect(ws.selectReplayStart).toHaveBeenCalledOnce();
    expect(ws.resetReplay).toHaveBeenCalledOnce();
    expect(ws.exitReplay).toHaveBeenCalledOnce();
  });

  it('has one control that leaves replay', () => {
    render(<ChartPanelFooter ws={footerModel()} />);
    const toolbar = screen.getByRole('group', { name: 'Chart replay controls' });
    expect(within(toolbar).getAllByRole('button', { name: /exit replay/i })).toHaveLength(1);
    expect(within(toolbar).getByRole('button', { name: /exit replay/i })).toHaveTextContent('Real time');
  });

  it('keeps Select bar available during playback so the user can jump to a new start', () => {
    render(<ChartPanelFooter ws={footerModel({ replayPlaying: true })} />);
    expect(screen.getByRole('button', { name: 'Pause replay' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Choose replay start' })).toBeEnabled();
  });

  it('disables stepping while a start bar is being chosen', () => {
    render(<ChartPanelFooter ws={footerModel({
      replayChoosingStart: true,
      replayCurrentBar: null,
      replayHasNextBar: false,
      replayHasPreviousBar: false,
      replayStartTime: null,
      replayVisibleBarCount: 0,
    })} />);
    expect(screen.getByRole('button', { name: 'Choose replay start' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset replay' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Replay previous bar' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Replay next bar' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Play replay' })).toBeDisabled();
    expect(screen.getByText(/Select a bar · 0\/40/)).toBeInTheDocument();
  });

  it('shows replay controls only on the active chart', () => {
    render(<ChartPanelFooter ws={footerModel({ active: false })} />);
    expect(screen.queryByRole('group', { name: 'Chart replay controls' })).not.toBeInTheDocument();
  });
});
