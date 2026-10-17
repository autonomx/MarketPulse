import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TradingSideRail } from './TradingSideRail';

describe('TradingSideRail', () => {
  it('closes the panel when the active shortcut is clicked', () => {
    const onSelectTab = vi.fn();
    const onToggle = vi.fn();

    render(
      <TradingSideRail
        activeTab="watchlist"
        collapsed={false}
        onSelectTab={onSelectTab}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Watchlist' }));

    expect(onToggle).toHaveBeenCalledOnce();
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it('switches sections without closing when another shortcut is clicked', () => {
    const onSelectTab = vi.fn();
    const onToggle = vi.fn();

    render(
      <TradingSideRail
        activeTab="watchlist"
        collapsed={false}
        onSelectTab={onSelectTab}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Alerts' }));

    expect(onSelectTab).toHaveBeenCalledWith('alerts');
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('opens the panel when a shortcut is clicked while collapsed', () => {
    const onSelectTab = vi.fn();
    const onToggle = vi.fn();

    render(
      <TradingSideRail
        activeTab="watchlist"
        collapsed
        onSelectTab={onSelectTab}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Trade' }));

    expect(onSelectTab).toHaveBeenCalledWith('paper');
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it('exposes Symbol Intelligence and Automatic Journal while the right panel is collapsed', () => {
    const onSelectTab = vi.fn();
    const onToggle = vi.fn();

    render(
      <TradingSideRail
        activeTab="watchlist"
        collapsed
        onSelectTab={onSelectTab}
        onToggle={onToggle}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Symbol Intelligence' }));
    expect(onSelectTab).toHaveBeenLastCalledWith('intelligence');
    expect(onToggle).toHaveBeenCalledTimes(1);

    onToggle.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Automatic Journal' }));
    expect(onSelectTab).toHaveBeenLastCalledWith('journal');
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('names the scripts section Omnix Scripts', () => {
    render(<TradingSideRail activeTab="watchlist" collapsed={false} onSelectTab={vi.fn()} onToggle={vi.fn()} />);

    expect(screen.getByRole('button', { name: 'Omnix Scripts' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Pine/ })).not.toBeInTheDocument();
  });
});
