import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultAlertNotifications } from './alertDelivery';
import { AlertDeliveryFields } from './AlertDeliveryFields';

afterEach(() => {
  cleanup();
  delete window.marketPulse;
});

const desktop = () => {
  window.marketPulse = { version: '1.0.0', platform: 'win32', setTitleBarOverlay: vi.fn(), revealLogs: vi.fn(), openExternal: vi.fn(), requestLoginCode: vi.fn() };
};

describe('desktop alert channel (MarketPulse S-6)', () => {
  it('offers push in a browser and the desktop channel in the desktop app', () => {
    const editor = { notifications: ['app' as const] };
    const view = render(<AlertDeliveryFields editor={editor} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Push')).toBeInTheDocument();
    expect(screen.queryByLabelText('Desktop')).toBeNull();
    view.unmount();

    desktop();
    render(<AlertDeliveryFields editor={editor} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Desktop')).toBeInTheDocument();
    expect(screen.queryByLabelText('Push')).toBeNull();
  });

  it('starts new alerts with the desktop channel inside the desktop app', () => {
    expect(defaultAlertNotifications()).toEqual(['app', 'toast']);
    desktop();
    expect(defaultAlertNotifications()).toEqual(['app', 'toast', 'desktop']);
  });
});
