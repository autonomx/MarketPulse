/**
 * The desktop shell's bridge (MarketPulse W-2): `window.marketPulse`, exposed by
 * the Electron preload. In a browser there is none and every caller keeps its
 * browser behaviour.
 */
export interface TitleBarOverlayColors {
  /** Background of the window-control area. */
  color: string;
  /** Colour of the minimise, maximise and close symbols. */
  symbolColor: string;
}

export interface DesktopBridge {
  /** The desktop app's version. */
  readonly version: string;
  /** The platform the shell runs on (`win32`, `darwin`, …). */
  readonly platform: string;
  /** Colours the native window controls drawn over the title-bar area. */
  setTitleBarOverlay(colors: TitleBarOverlayColors): void;
  /** Opens the folder holding the app's logs. */
  revealLogs(): void;
  /** Opens a URL in the system browser (Google sign-in, which refuses embedded browsers). */
  openExternal(url: string): void;
  /** Asks the shell for a fresh first-run sign-in code; false once first run is done. */
  requestLoginCode(): Promise<boolean>;
}

declare global {
  interface Window {
    marketPulse?: DesktopBridge;
  }
}

/** The bridge, or null outside the desktop app. */
export function desktopBridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null;
  return window.marketPulse ?? null;
}

/** Whether this page runs inside the desktop app. */
export function isDesktopApp(): boolean {
  return desktopBridge() !== null;
}
