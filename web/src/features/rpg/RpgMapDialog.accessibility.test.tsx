/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgMapDialog } from './RpgMapDialog';

vi.mock('./RpgMapSurface', () => ({
  RpgMapSurface: ({ mapId, sessionId }: { mapId: string; sessionId: string }) => (
    <div data-testid="map-surface">{mapId}:{sessionId}</div>
  ),
}));

afterEach(() => {
  document.body.innerHTML = '';
});

describe('RpgMapDialog accessibility', () => {
  it('moves focus into the modal and closes with Escape', async () => {
    const onClose = vi.fn();
    const opener = document.createElement('button');
    opener.textContent = 'Open map';
    document.body.appendChild(opener);
    opener.focus();

    render(
      <RpgMapDialog
        locationLabel="Frost Haven"
        mapId="settlement:frost_haven"
        onClose={onClose}
        open
        sessionId="session:test"
      />,
    );

    const dialog = screen.getByRole('dialog', { name: 'Frost Haven' });
    const close = screen.getByRole('button', { name: 'Close map' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    await waitFor(() => expect(close).toHaveFocus());
    expect(screen.getByTestId('map-surface')).toHaveTextContent('settlement:frost_haven:session:test');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('returns focus to the opener after unmount', async () => {
    const opener = document.createElement('button');
    opener.textContent = 'Open map';
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();
    const view = render(
      <RpgMapDialog
        locationLabel="Frost Haven"
        mapId="settlement:frost_haven"
        onClose={onClose}
        open
        sessionId="session:test"
      />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close map' })).toHaveFocus());

    view.unmount();

    expect(opener).toHaveFocus();
  });

  it('closes only when the backdrop itself is activated', () => {
    const onClose = vi.fn();
    const view = render(
      <RpgMapDialog
        locationLabel="Frost Haven"
        mapId="settlement:frost_haven"
        onClose={onClose}
        open
        sessionId="session:test"
      />,
    );
    const backdrop = view.container.querySelector('.rpg-map-dialog-backdrop');
    const dialog = screen.getByRole('dialog', { name: 'Frost Haven' });

    fireEvent.mouseDown(dialog);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(backdrop!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
