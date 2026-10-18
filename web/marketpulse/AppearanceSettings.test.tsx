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
