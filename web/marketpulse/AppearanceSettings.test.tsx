import { MantineProvider, useMantineColorScheme } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { OMNIX_APPEARANCE_MODE_STORAGE_KEY } from '../src/design/appearanceEffects';
import { AppearanceFollower, applyStoredAppearance } from './appearance';
import { AppearanceSettings } from './AppearanceSettings';

function MantineScheme() {
  const { colorScheme } = useMantineColorScheme();
  return <output aria-label="Mantine scheme">{colorScheme}</output>;
}

function renderSettings() {
  return render(
    <MantineProvider defaultColorScheme={applyStoredAppearance()}>
      <AppearanceFollower />
      <MantineScheme />
      <AppearanceSettings />
    </MantineProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.omnixAppearance;
  delete document.documentElement.dataset.omnixAppearancePreference;
});
afterEach(() => cleanup());

describe('appearance settings', () => {
  it('starts dark and switches the page and Mantine to light', () => {
    renderSettings();
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();

    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));

    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked();
    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY)).toBe('light');
    expect(screen.getByLabelText('Mantine scheme')).toHaveTextContent('light');
  });

  it('remembers the choice across launches', () => {
    window.localStorage.setItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY, 'light');
    renderSettings();

    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked();
    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(screen.getByLabelText('Mantine scheme')).toHaveTextContent('light');
  });

  it('saves System as the preference', () => {
    renderSettings();

    fireEvent.click(screen.getByRole('radio', { name: 'System' }));

    expect(screen.getByRole('radio', { name: 'System' })).toBeChecked();
    expect(window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY)).toBe('system');
    expect(document.documentElement.dataset.omnixAppearancePreference).toBe('system');
  });
});

describe('text size setting', () => {
  afterEach(() => {
    delete (window as { marketPulse?: unknown }).marketPulse;
  });

  it('steps, slides and resets the text size of the desktop window', () => {
    const zoom: number[] = [];
    (window as { marketPulse?: unknown }).marketPulse = { setZoomFactor: (factor: number) => zoom.push(factor) };
    renderSettings();

    expect(screen.getByRole('slider', { name: 'Text size' })).toHaveValue('100');
    expect(screen.getByRole('button', { name: 'Reset' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Larger text' }));
    expect(screen.getByRole('slider', { name: 'Text size' })).toHaveValue('105');
    expect(screen.getByText('105%')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('slider', { name: 'Text size' }), { target: { value: '150' } });
    expect(screen.getByRole('button', { name: 'Larger text' })).toBeDisabled();
    expect(window.localStorage.getItem('marketpulse.textSize')).toBe('150');

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(zoom).toEqual([1, 1.05, 1.5, 1]);
    expect(screen.queryByText(/use the browser/)).toBeNull();
  });

  it('points a browser to its own zoom', () => {
    renderSettings();
    expect(screen.getByText(/use the browser.s own zoom/)).toBeInTheDocument();
  });
});
