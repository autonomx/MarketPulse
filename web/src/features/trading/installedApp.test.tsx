import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setTradingCommandAvailability, tradingCommandAvailability, useTradingCommandDispatcher } from './commands/useTradingCommands';
import { InstallAppNote } from './InstallAppNote';
import { setInstallPromptForTests } from '../../shared/installPrompt';
import { isInstalledApp, useInstalledAppCommandKeys } from './installedApp';

afterEach(() => {
  cleanup();
  act(() => setInstallPromptForTests(null));
});

function media(standalone: boolean, mode = '(display-mode: standalone)') {
  const changeListeners = new Set<() => void>();
  let matches = standalone;
  const query = (text: string) => ({
    get matches() { return text === mode && matches; },
    addEventListener: (_: string, listener: () => void) => { changeListeners.add(listener); },
    removeEventListener: (_: string, listener: () => void) => { changeListeners.delete(listener); },
  }) as unknown as MediaQueryList;
  return { query, changeListeners, setStandalone(next: boolean) { matches = next; changeListeners.forEach((listener) => listener()); } };
}

describe('installed app (TVP-4.5)', () => {
  it('detects the app window by its display mode', () => {
    expect(isInstalledApp(media(true).query)).toBe(true);
    expect(isInstalledApp(media(false).query)).toBe(false);
    expect(isInstalledApp(media(true, '(display-mode: window-controls-overlay)').query)).toBe(true);
    // A fullscreen browser tab (F11) is not the app.
    expect(isInstalledApp(media(true, '(display-mode: fullscreen)').query)).toBe(false);
  });

  it('switches the trading keys to the app keys in the app window, and back', () => {
    const window = media(false);
    const hook = renderHook(() => useInstalledAppCommandKeys(window.query));
    expect(tradingCommandAvailability()).toBe('browser');
    act(() => window.setStandalone(true));
    expect(tradingCommandAvailability()).toBe('installed');
    hook.unmount();
    expect(tradingCommandAvailability()).toBe('browser');
    expect(window.changeListeners.size).toBe(0);
  });

  it('keeps a claimed browser key from reaching the browser in the app window, even with nothing to run', () => {
    const hook = renderHook(() => useTradingCommandDispatcher());
    const press = () => {
      const event = new KeyboardEvent('keydown', { key: 'w', code: 'KeyW', ctrlKey: true, bubbles: true, cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(press()).toBe(false);
    act(() => setTradingCommandAvailability('installed'));
    expect(press()).toBe(true);
    act(() => setTradingCommandAvailability('browser'));
    hook.unmount();
  });

  it('offers the browser install prompt in the shortcut dialog', async () => {
    const prompt = vi.fn(async () => undefined);
    act(() => setInstallPromptForTests({ prompt, userChoice: Promise.resolve({ outcome: 'accepted' as const }) } as never));
    render(<InstallAppNote availability="browser" />);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Install app' })));
    expect(prompt).toHaveBeenCalled();
    expect(await screen.findByRole('status')).toHaveTextContent('Installed');
  });

  it('reports a dismissed install', async () => {
    act(() => setInstallPromptForTests({ prompt: vi.fn(async () => undefined), userChoice: Promise.resolve({ outcome: 'dismissed' as const }) } as never));
    render(<InstallAppNote availability="browser" />);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Install app' })));
    expect(await screen.findByRole('status')).toHaveTextContent('Not installed');
  });

  it('explains the app keys without a prompt, and says nothing in the app', () => {
    const view = render(<InstallAppNote availability="browser" />);
    expect(screen.getByText(/browser’s menu/)).toBeInTheDocument();
    view.rerender(<InstallAppNote availability="installed" />);
    expect(view.container).toBeEmptyDOMElement();
  });
});

describe('desktop app (MarketPulse W-2)', () => {
  afterEach(() => {
    delete window.marketPulse;
  });

  it('counts the desktop app as the installed app and turns off web push there', async () => {
    const { pushSupported } = await import('./alertNotificationsApi');
    expect(isInstalledApp(media(false).query)).toBe(false);

    window.marketPulse = { version: '1.0.0', platform: 'win32', setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal: vi.fn(), requestLoginCode: vi.fn() };

    expect(isInstalledApp(media(false).query)).toBe(true);
    expect(pushSupported()).toBe(false);
  });
});
