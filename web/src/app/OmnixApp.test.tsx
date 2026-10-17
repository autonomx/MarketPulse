import { QueryClientProvider } from '@tanstack/react-query';
import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { omnixTheme } from '../design/theme';
import { OmnixApp } from './OmnixApp';
import { createTestQueryClient } from '../test/renderWithProviders';

function renderApp() {
  const queryClient = createTestQueryClient();

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <OmnixApp />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe('OmnixApp', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.dataset.omnixAppearance = '';
    document.documentElement.dataset.omnixAppearancePreference = '';
  });

  it('renders the shared app shell and curated primary sidebar entrypoints', async () => {
    renderApp();

    expect((await screen.findAllByLabelText('Omnix')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Show Omnix sidebar' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Omnix sidebar' }));
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'RPG' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Chatbot' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Storyteller' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Podcast' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Voice Studio' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Voice Cloning' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'STT' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Image Generation' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Trading' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Providers' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Models' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Jobs / Runs' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Assets' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Reports' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Diagnostics' })).toBeInTheDocument();
  });

  it('uses the Omnix mark as a complete sidebar visibility toggle', async () => {
    renderApp();

    expect(await screen.findByRole('button', { name: 'Show Omnix sidebar' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Omnix sidebar' }));
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'RPG' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Hide Omnix sidebar' }));

    expect(screen.queryByRole('link', { name: 'RPG' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show Omnix sidebar' })).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'Show Omnix sidebar' }));

    expect(screen.getByRole('link', { name: 'RPG' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide Omnix sidebar' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('toggles and persists the app appearance mode', async () => {
    renderApp();

    const lightToggle = await screen.findByRole('button', { name: 'Switch to light mode' });

    expect(document.documentElement.dataset.omnixAppearance).toBe('dark');
    fireEvent.click(lightToggle);

    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(window.localStorage.getItem('omnix.appearance.mode')).toBe('light');
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }));

    expect(document.documentElement.dataset.omnixAppearance).toBe('dark');
    expect(window.localStorage.getItem('omnix.appearance.mode')).toBe('dark');
  });
});
