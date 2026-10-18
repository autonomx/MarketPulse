import { useEffect, useState } from 'react';
import { desktopBridge } from '../src/shared/desktopBridge';

/**
 * Text size (Settings › Appearance): one percentage for all of MarketPulse's text. The terminal sizes most text in
 * pixels and the chart draws its own, so a root font size would miss them; the desktop app zooms the page instead (the
 * preload's setZoomFactor). A browser has its own zoom, and CSS zoom would also scale the terminal's 100vh height, so
 * there the setting is only saved. Saved on this computer, applied as each window loads.
 */
export const TEXT_SIZE_STORAGE_KEY = 'marketpulse.textSize';
export const MIN_TEXT_SIZE = 80;
export const MAX_TEXT_SIZE = 150;
export const TEXT_SIZE_STEP = 5;
export const DEFAULT_TEXT_SIZE = 100;
const CHANGE_EVENT = 'marketpulse:text-size';

type ZoomBridge = { setZoomFactor?: (factor: number) => void; onTextSizeStep?: (listener: (step: number) => void) => void };

let listeningToMenu = false;

/** The View menu's Larger Text, Smaller Text and Actual Text Size (Ctrl+=, Ctrl+-, Ctrl+0) step the saved size. */
export function followTextSizeMenu(): void {
  const bridge = desktopBridge() as ZoomBridge | null;
  if (listeningToMenu || !bridge?.onTextSizeStep) return;
  listeningToMenu = true;
  bridge.onTextSizeStep((step) => {
    saveTextSize(step === 0 ? DEFAULT_TEXT_SIZE : loadTextSize() + step * TEXT_SIZE_STEP);
  });
}

export function normalizeTextSize(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_TEXT_SIZE;
  const clamped = Math.min(MAX_TEXT_SIZE, Math.max(MIN_TEXT_SIZE, numeric));
  return Math.round(clamped / TEXT_SIZE_STEP) * TEXT_SIZE_STEP;
}

export function loadTextSize(): number {
  try {
    const stored = window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY);
    return stored === null ? DEFAULT_TEXT_SIZE : normalizeTextSize(stored);
  } catch {
    return DEFAULT_TEXT_SIZE;
  }
}

/** Whether this window can change its text size (the desktop app); a browser uses its own zoom. */
export function textSizeAvailable(): boolean {
  return typeof (desktopBridge() as ZoomBridge | null)?.setZoomFactor === 'function';
}

/** Shows the page at `percent` text size without saving it. */
export function applyTextSize(percent: number): void {
  const size = normalizeTextSize(percent);
  (desktopBridge() as ZoomBridge | null)?.setZoomFactor?.(size / 100);
  document.documentElement.dataset.mpTextSize = String(size);
}

/** Saves and applies a text size, and tells this window's settings. */
export function saveTextSize(percent: number): number {
  const size = normalizeTextSize(percent);
  try {
    window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, String(size));
  } catch {
    // Without storage the size still applies to this window until it reloads.
  }
  applyTextSize(size);
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: size }));
  return size;
}

/** The saved text size, following changes from this window and from MarketPulse's other windows. */
export function useTextSize(): number {
  const [size, setSize] = useState(loadTextSize);
  useEffect(() => {
    const local = (event: Event) => setSize(normalizeTextSize((event as CustomEvent<number>).detail));
    const other = (event: StorageEvent) => {
      if (event.key !== TEXT_SIZE_STORAGE_KEY) return;
      const next = loadTextSize();
      applyTextSize(next);
      setSize(next);
    };
    window.addEventListener(CHANGE_EVENT, local);
    window.addEventListener('storage', other);
    return () => {
      window.removeEventListener(CHANGE_EVENT, local);
      window.removeEventListener('storage', other);
    };
  }, []);
  return size;
}
