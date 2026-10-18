import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  window.localStorage.clear();
  delete (window as { marketPulse?: unknown }).marketPulse;
  delete document.documentElement.dataset.mpTextSize;
  vi.resetModules();
});

async function load() {
  return import('./textSize');
}

describe('text size', () => {
  it('rounds to 5% steps between 80% and 150%, defaulting to 100%', async () => {
    const { normalizeTextSize, loadTextSize } = await load();
    expect(normalizeTextSize(112)).toBe(110);
    expect(normalizeTextSize(10)).toBe(80);
    expect(normalizeTextSize(400)).toBe(150);
    expect(normalizeTextSize('nope')).toBe(100);
    expect(loadTextSize()).toBe(100);
  });

  it('zooms the desktop window and saves the size', async () => {
    const setZoomFactor = vi.fn();
    (window as { marketPulse?: unknown }).marketPulse = { setZoomFactor };
    const { saveTextSize, loadTextSize, textSizeAvailable } = await load();

    expect(textSizeAvailable()).toBe(true);
    expect(saveTextSize(125)).toBe(125);
    expect(setZoomFactor).toHaveBeenCalledWith(1.25);
    expect(loadTextSize()).toBe(125);
    expect(document.documentElement.dataset.mpTextSize).toBe('125');
  });

  it('steps the saved size from the View menu, and Actual Text Size resets it', async () => {
    let menu: ((step: number) => void) | undefined;
    const setZoomFactor = vi.fn();
    (window as { marketPulse?: unknown }).marketPulse = { setZoomFactor, onTextSizeStep: (listener: (step: number) => void) => { menu = listener; } };
    const { followTextSizeMenu, loadTextSize } = await load();

    followTextSizeMenu();
    menu?.(1);
    menu?.(1);
    expect(loadTextSize()).toBe(110);
    menu?.(-1);
    expect(loadTextSize()).toBe(105);
    menu?.(0);
    expect(loadTextSize()).toBe(100);
    expect(setZoomFactor).toHaveBeenLastCalledWith(1);
  });

  it('only saves in a browser, which has its own zoom', async () => {
    const { saveTextSize, loadTextSize, textSizeAvailable } = await load();
    expect(textSizeAvailable()).toBe(false);
    saveTextSize(130);
    expect(loadTextSize()).toBe(130);
    expect(document.documentElement.style.getPropertyValue('zoom')).toBe('');
  });
});
