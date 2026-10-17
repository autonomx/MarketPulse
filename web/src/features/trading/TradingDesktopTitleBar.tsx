import { useEffect, useRef, type ReactNode } from 'react';
import type { DesktopBridge } from '../../shared/desktopBridge';
import './TradingDesktopTitleBar.css';

/**
 * The desktop app's title bar (MarketPulse W-4): the session tabs sit in the
 * frameless window's top strip, the empty space drags the window, and the
 * native window controls are drawn over the reserved inset in the bar's colours.
 */
export function TradingDesktopTitleBar({ bridge, children }: { bridge: DesktopBridge; children: ReactNode }) {
  const bar = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = bar.current;
    if (!element || typeof window.getComputedStyle !== 'function') return;
    const style = window.getComputedStyle(element);
    bridge.setTitleBarOverlay({ color: style.backgroundColor, symbolColor: style.color });
  }, [bridge]);

  return (
    <div ref={bar} className="trading-title-bar" data-desktop-platform={bridge.platform}>
      <span className="trading-title-bar-mark" aria-hidden="true" />
      <div className="trading-title-bar-tabs">{children}</div>
      <div className="trading-title-bar-drag" aria-hidden="true" />
    </div>
  );
}
