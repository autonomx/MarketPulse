import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../design/theme';
import { createTestQueryClient } from '../test/renderWithProviders';

// The add-a-module recipe (WP-9.7): a module is one manifest plus one line in
// the registry. Here the line adds a scaffolded module to the real registry.
vi.mock('./modulesManifest', async (importOriginal) => {
  const { defineModule } = await import('./moduleManifest');
  const fakeModule = defineModule({
    id: 'fake',
    label: 'Fake Module',
    summary: 'A scaffolded module for the registration recipe.',
    route: '/fake',
    icon: 'F',
    backendModules: [],
    apiPrefixes: ['/api/fake'],
    loadWorkspace: async () => function FakeWorkspace() {
      return <p>Fake workspace</p>;
    },
  });
  const original = await importOriginal<typeof import('./modulesManifest')>();
  return { moduleManifests: [...original.moduleManifests, fakeModule] };
});

beforeEach(() => {
  window.localStorage.clear();
});

describe('adding a module', () => {
  it('routes, navigates to and scopes a module from one registration line', async () => {
    const { router } = await import('./router');
    // Omnix's bootstrap installs the firewall with the registered modules.
    await import('./viewApiFirewallBootstrap');
    const { moduleIdFromPathname, isApiAllowedForView } = await import('./viewApiScope');
    const { OmnixApp } = await import('./OmnixApp');

    expect(router.routesByPath['/fake' as keyof typeof router.routesByPath]).toBeDefined();
    expect(moduleIdFromPathname('/fake/anything')).toBe('fake');
    expect(isApiAllowedForView('/api/fake/items', 'fake' as never)).toBe(true);
    expect(isApiAllowedForView('/api/trading/orders', 'fake' as never)).toBe(false);

    window.history.replaceState(null, '', '/fake');
    render(
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <QueryClientProvider client={createTestQueryClient()}>
          <OmnixApp />
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect(await screen.findByText('Fake workspace')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Omnix sidebar' }));
    expect(screen.getByRole('link', { name: 'Fake Module' })).toHaveAttribute('href', '/fake');
  });

  it('shows a not-found page for an address no module owns', async () => {
    const { OmnixApp } = await import('./OmnixApp');
    window.history.replaceState(null, '', '/nowhere');
    render(
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <QueryClientProvider client={createTestQueryClient()}>
          <OmnixApp />
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Chat' })).toHaveAttribute('href', '/chatbot');
  });
});
