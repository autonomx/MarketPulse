import { useEffect, useRef, type ReactNode } from 'react';
import { commitAppearanceMode } from '../../design/appearanceEffects';
import { useAppearanceMode } from '../../design/useAppearanceMode';
import type { DesktopBridge } from '../../shared/desktopBridge';
import './TradingDesktopTitleBar.css';

/**
 * The desktop app's title bar (MarketPulse W-4): the session tabs sit in the
 * frameless window's top strip, the empty space drags the window, and the
 * native window controls are drawn over the reserved inset in the bar's colours.
 * Beside them, buttons open Settings and switch between light and dark mode.
 */
export function TradingDesktopTitleBar({ bridge, onOpenSettings, children }: { bridge: DesktopBridge; onOpenSettings: () => void; children: ReactNode }) {
  const bar = useRef<HTMLDivElement | null>(null);
  const { resolvedMode } = useAppearanceMode();
  const light = resolvedMode === 'light';

  // The window controls take the bar's colours again whenever the light level changes.
  useEffect(() => {
    const element = bar.current;
    if (!element || typeof window.getComputedStyle !== 'function') return;
    const style = window.getComputedStyle(element);
    bridge.setTitleBarOverlay({ color: style.backgroundColor, symbolColor: style.color });
  }, [bridge, resolvedMode]);

  const label = light ? 'Switch to dark mode' : 'Switch to light mode';
  return (
    <div ref={bar} className="trading-title-bar" data-desktop-platform={bridge.platform}>
      <span className="trading-title-bar-mark" aria-hidden="true" />
      <div className="trading-title-bar-tabs">{children}</div>
      <div className="trading-title-bar-drag" aria-hidden="true" />
      <button type="button" className="trading-title-bar-action" aria-label="Settings" title="Settings" onClick={onOpenSettings}>
        <GearGlyph />
      </button>
      <button
        type="button"
        className="trading-title-bar-action"
        aria-label={label}
        aria-pressed={light}
        title={label}
        onClick={() => commitAppearanceMode(light ? 'dark' : 'light')}
      >
        {light ? <MoonGlyph /> : <SunGlyph />}
      </button>
    </div>
  );
}

function GearGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M10.3 3h3.4l.5 2.4 1.6.9 2.3-.8 1.7 2.9-1.8 1.6v1.9l1.8 1.6-1.7 2.9-2.3-.8-1.6.9-.5 2.4h-3.4l-.5-2.4-1.6-.9-2.3.8-1.7-2.9 1.8-1.6v-1.9L4.2 8.4l1.7-2.9 2.3.8 1.6-.9.5-2.4Z" />
    </svg>
  );
}

function SunGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" />
    </svg>
  );
}

function MoonGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
    </svg>
  );
}
