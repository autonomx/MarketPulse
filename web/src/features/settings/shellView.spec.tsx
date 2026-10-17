import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { OMNIX_TEXT_SCALE_STORAGE_KEY } from '../../design/appearanceEffects';
import { SettingsControlCenter } from './SettingsControlCenter';

describe('settings shell', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.style.removeProperty('font-size');
    document.documentElement.style.removeProperty('--omnix-text-scale');
    document.documentElement.removeAttribute('data-omnix-text-scale');
  });

  it('tracks provider changes, discards them, and routes categories', () => {
    render(<MantineProvider><SettingsControlCenter /></MantineProvider>);
    expect(screen.getByRole('heading', { name: 'Settings Control Center' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Fallback behavior'), { target: { value: 'fail' } });
    expect(screen.getByText('1 unsaved changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    fireEvent.click(screen.getByRole('button', { name: /RPG/ }));
    expect(screen.getByRole('heading', { name: 'RPG' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Images & Speech Input/ }));
    expect(screen.getByRole('heading', { name: 'Images & Speech Input' })).toBeInTheDocument();
    expect(screen.getByLabelText('Width')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Tools & Integrations/ }));
    expect(screen.getByText('Configuration ownership')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Jobs, Assets & Storage/ }));
    expect(screen.getByLabelText('Retention days')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Diagnostics & Developer/ }));
    expect(screen.getByRole('heading', { name: 'Runtime details' })).toBeInTheDocument();
  });

  it('changes and persists app-wide text size from Appearance & Accessibility', () => {
    render(<MantineProvider><SettingsControlCenter /></MantineProvider>);
    fireEvent.click(screen.getByRole('button', { name: /Appearance & Accessibility/ }));

    const slider = screen.getByLabelText('App text size');
    expect(slider).toHaveValue('100');
    fireEvent.change(slider, { target: { value: '120' } });

    expect(slider).toHaveValue('120');
    expect(screen.getByText('120%')).toBeInTheDocument();
    expect(document.documentElement.dataset.omnixTextScale).toBe('120');
    expect(document.documentElement.style.fontSize).toBe('120%');
    expect(window.localStorage.getItem(OMNIX_TEXT_SCALE_STORAGE_KEY)).toBe('120');

    fireEvent.click(screen.getByRole('button', { name: 'Decrease app text size' }));
    expect(screen.getByText('115%')).toBeInTheDocument();
  });
});
