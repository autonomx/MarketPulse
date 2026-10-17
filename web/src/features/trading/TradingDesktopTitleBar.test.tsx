import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DesktopBridge } from '../../shared/desktopBridge';
import { TradingDesktopTitleBar } from './TradingDesktopTitleBar';

afterEach(() => cleanup());

function bridge(platform = 'win32'): DesktopBridge {
  return { version: '1.0.0', platform, setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal: vi.fn(), requestLoginCode: vi.fn() };
}

describe('desktop title bar (MarketPulse W-4)', () => {
  it('holds the tabs, leaves a drag strip and colours the window controls', () => {
    const desktop = bridge();
    const { container } = render(<TradingDesktopTitleBar bridge={desktop}><button type="button">Tab 1</button></TradingDesktopTitleBar>);

    expect(screen.getByRole('button', { name: 'Tab 1' })).toBeInTheDocument();
    expect(container.querySelector('.trading-title-bar-drag')).not.toBeNull();
    expect(container.querySelector('.trading-title-bar')?.getAttribute('data-desktop-platform')).toBe('win32');
    expect(desktop.setTitleBarOverlay).toHaveBeenCalledTimes(1);
    const colors = vi.mocked(desktop.setTitleBarOverlay).mock.calls[0][0];
    expect(typeof colors.color).toBe('string');
    expect(typeof colors.symbolColor).toBe('string');
  });

  it('marks the platform so macOS reserves the traffic lights on the left', () => {
    const { container } = render(<TradingDesktopTitleBar bridge={bridge('darwin')}><span /></TradingDesktopTitleBar>);

    expect(container.querySelector('.trading-title-bar')?.getAttribute('data-desktop-platform')).toBe('darwin');
  });
});
