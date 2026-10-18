import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OMNIX_APPEARANCE_MODE_STORAGE_KEY } from '../../design/appearanceEffects';
import type { DesktopBridge } from '../../shared/desktopBridge';
import { TradingDesktopTitleBar } from './TradingDesktopTitleBar';

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.dataset.omnixAppearance = 'dark';
  document.documentElement.dataset.omnixAppearancePreference = 'dark';
});
afterEach(() => cleanup());

function bridge(platform = 'win32'): DesktopBridge {
  return { version: '1.0.0', platform, setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal: vi.fn(), requestLoginCode: vi.fn() };
}

describe('desktop title bar (MarketPulse W-4)', () => {
  it('holds the tabs, leaves a drag strip and colours the window controls', () => {
    const desktop = bridge();
    const { container } = render(<TradingDesktopTitleBar bridge={desktop} onOpenSettings={vi.fn()}><button type="button">Tab 1</button></TradingDesktopTitleBar>);

    expect(screen.getByRole('button', { name: 'Tab 1' })).toBeInTheDocument();
    expect(container.querySelector('.trading-title-bar-drag')).not.toBeNull();
    expect(container.querySelector('.trading-title-bar')?.getAttribute('data-desktop-platform')).toBe('win32');
    expect(desktop.setTitleBarOverlay).toHaveBeenCalledTimes(1);
    const colors = vi.mocked(desktop.setTitleBarOverlay).mock.calls[0][0];
    expect(typeof colors.color).toBe('string');
    expect(typeof colors.symbolColor).toBe('string');
  });

  it('switches between light and dark mode and recolours the window controls', () => {
    const desktop = bridge();
    render(<TradingDesktopTitleBar bridge={desktop} onOpenSettings={vi.fn()}><span /></TradingDesktopTitleBar>);

    fireEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }));

    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY)).toBe('light');
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toHaveAttribute('aria-pressed', 'true');
    expect(desktop.setTitleBarOverlay).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }));

    expect(document.documentElement.dataset.omnixAppearance).toBe('dark');
    expect(window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY)).toBe('dark');
    expect(desktop.setTitleBarOverlay).toHaveBeenCalledTimes(3);
  });

  it('opens Settings from the gear beside the window controls', () => {
    const onOpenSettings = vi.fn();
    render(<TradingDesktopTitleBar bridge={bridge()} onOpenSettings={onOpenSettings}><span /></TradingDesktopTitleBar>);

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it('marks the platform so macOS reserves the traffic lights on the left', () => {
    const { container } = render(<TradingDesktopTitleBar bridge={bridge('darwin')} onOpenSettings={vi.fn()}><span /></TradingDesktopTitleBar>);

    expect(container.querySelector('.trading-title-bar')?.getAttribute('data-desktop-platform')).toBe('darwin');
  });
});
