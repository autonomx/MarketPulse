import { describe, expect, it } from 'vitest';

import { desktopCompanionControlStore } from './desktop-companion-control-store';
import { statusLabel } from './desktop-companion-controls';

describe('desktop companion control store', () => {
  it('requires an explicit start and keeps mute independent from pause', () => {
    desktopCompanionControlStore.reset();
    expect(desktopCompanionControlStore.getState()).toMatchObject({ requested: false, paused: false, muted: true });

    desktopCompanionControlStore.dispatch('start');
    desktopCompanionControlStore.dispatch('unmute');
    desktopCompanionControlStore.dispatch('pause');
    expect(desktopCompanionControlStore.getState()).toMatchObject({ requested: true, paused: true, muted: false });

    desktopCompanionControlStore.dispatch('resume');
    expect(desktopCompanionControlStore.getState()).toMatchObject({ requested: true, paused: false, muted: false });

    desktopCompanionControlStore.dispatch('stop');
    expect(desktopCompanionControlStore.getState()).toMatchObject({ requested: false, paused: false, muted: false });
    desktopCompanionControlStore.reset();
  });
});

describe('desktop companion status labels', () => {
  it('renders actionable preflight and privacy states', () => {
    expect(statusLabel('sharing', 'preflight_running')).toBe('Testing model');
    expect(statusLabel('error', 'remote_vision_not_allowed')).toBe('Remote blocked');
    expect(statusLabel('watching_idle', 'preflight_passed')).toBe('Watching');
    expect(statusLabel('paused', 'paused_by_user')).toBe('Paused');
  });
});
