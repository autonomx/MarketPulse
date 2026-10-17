import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TradingSidePanel } from './TradingSidePanel';
import type { ComponentProps } from 'react';

const imports = vi.hoisted(() => ({ paper: 0, pine: 0 }));
vi.mock('./TradingWatchlist', () => ({ TradingWatchlist: () => <p>Watchlist ready</p> }));
vi.mock('./TradingPaperPanel', () => {
  imports.paper += 1;
  return { TradingPaperPanel: ({ instrumentId }: { instrumentId: string }) => <p>Paper panel: {instrumentId}</p> };
});
vi.mock('./TradingPinePanel', () => {
  imports.pine += 1;
  return { TradingPinePanel: () => <p>Pine editor ready</p> };
});

const props: ComponentProps<typeof TradingSidePanel> = {
  instruments: [], activeInstrumentId: 'BINANCE:BTCUSDT', bindingId: null,
  interval: '1h', indicators: [], layout: 'auto', chartCount: 1,
  minimumChartCount: 1, maximumChartCount: 4,
  links: { instrument: false, interval: false, crosshair: false, visibleRange: false },
  snapMode: 'none', pineIndicatorId: null,
  onSelectInstrument: vi.fn(), onSelectAlert: vi.fn(), onSetIndicators: vi.fn(),
  onPineIndicatorChange: vi.fn(), onOpenPineScript: vi.fn(), onSetLayout: vi.fn(),
  onSetChartCount: vi.fn(), onAddChart: vi.fn(), onRemoveChart: vi.fn(),
  onSetLink: vi.fn(), onSetSnapMode: vi.fn(),
};

describe('TradingSidePanel deferred panels', () => {
  it('loads optional panels on selection and keeps navigation usable', async () => {
    const { rerender } = render(<TradingSidePanel {...props} />);
    expect(screen.getByText('Watchlist ready')).toBeInTheDocument();
    expect(imports).toEqual({ paper: 0, pine: 0 });

    fireEvent.click(screen.getByRole('tab', { name: 'Trade' }));
    expect(screen.getByRole('tab', { name: 'Watchlist' })).toBeEnabled();
    expect(await screen.findByText('Paper panel: BINANCE:BTCUSDT')).toBeInTheDocument();
    expect(imports.paper).toBe(1);

    fireEvent.click(screen.getByRole('tab', { name: 'Watchlist' }));
    expect(screen.getByText('Watchlist ready')).toBeInTheDocument();
    rerender(<TradingSidePanel {...props} selectedTab="pine" />);
    expect(await screen.findByText('Pine editor ready')).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Omnix Scripts editor' })).toBeInTheDocument();
    expect(imports.pine).toBe(1);
  });
});
