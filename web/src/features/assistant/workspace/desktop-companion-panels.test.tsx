import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { desktopCompanionControlStore } from './desktop-companion-control-store';
import { DesktopCompanionControls } from './desktop-companion-controls';
import { DesktopCompanionTextSurface } from './desktop-companion-text-surface';
import { DESKTOP_COMPANION_STATUS_EVENT, DESKTOP_COMPANION_TEXT_EVENT } from '../../../events/bus';

afterEach(() => {
  cleanup();
  desktopCompanionControlStore.reset();
});

describe('desktop companion panels', () => {
  it('drives Companion Watch through the control store and shows its status', () => {
    render(<DesktopCompanionControls />);

    expect(screen.getByRole('button', { name: 'Pause' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(screen.getByRole('button', { name: 'Watching' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Muted' }));
    expect(desktopCompanionControlStore.getState()).toMatchObject({ requested: true, muted: false });

    act(() => {
      window.dispatchEvent(new CustomEvent(DESKTOP_COMPANION_STATUS_EVENT, { detail: { phase: 'analyzing' } }));
    });
    expect(screen.getByText('Analyzing')).toBeInTheDocument();
  });

  it('shows the latest remark until it is dismissed', () => {
    render(<DesktopCompanionTextSurface />);
    const notice = { sessionId: 's1', observationId: 'o1', turnId: 't1', content: 'That build finished.', expiresAtMs: Date.now() + 60_000 };

    act(() => {
      window.dispatchEvent(new CustomEvent(DESKTOP_COMPANION_TEXT_EVENT, { detail: notice }));
    });
    expect(screen.getByText('That build finished.')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss', hidden: true }));
    expect(screen.queryByText('That build finished.')).not.toBeInTheDocument();
  });
});
