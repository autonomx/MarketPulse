import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  enterLiveChatFullscreen,
  exitLiveChatFullscreen,
  getLiveChatFullscreenState,
  initializeLiveChatFullscreenController,
  resetLiveChatFullscreenStateForTests,
} from './live-chat-fullscreen-controller';

describe('Live Chat fullscreen controller', () => {
  let dispose: () => void;

  beforeEach(() => {
    resetLiveChatFullscreenStateForTests();
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
    Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: undefined });
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: undefined });
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    dispose = initializeLiveChatFullscreenController();
  });

  afterEach(async () => {
    await exitLiveChatFullscreen();
    dispose();
    vi.restoreAllMocks();
  });

  it('uses the in-app immersive fallback when browser fullscreen is unavailable', () => {
    enterLiveChatFullscreen('header');
    expect(getLiveChatFullscreenState()).toEqual({
      immersive: true,
      browserState: 'unavailable',
      source: 'header',
    });
  });

  it('tracks successful browser fullscreen requests', async () => {
    const requestFullscreen = vi.fn(async () => undefined);
    Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: requestFullscreen });

    enterLiveChatFullscreen('call-card');
    await vi.waitFor(() => expect(getLiveChatFullscreenState().browserState).toBe('active'));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(getLiveChatFullscreenState().source).toBe('call-card');
  });

  it('exits immersive mode from Escape without ending the call', async () => {
    enterLiveChatFullscreen('header');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await vi.waitFor(() => expect(getLiveChatFullscreenState().immersive).toBe(false));
  });
});
