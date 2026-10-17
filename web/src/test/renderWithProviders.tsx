import { MantineProvider } from '@mantine/core';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions } from '@testing-library/react';
import type { ReactElement, ReactNode } from 'react';
import { vi } from 'vitest';
import { omnixTheme } from '../design/theme';

/** Shared test utilities (WP-9.10): one way to render with the app's providers and to fake the gateway. */

/** A query client for one test: no retries, so failures show at once. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

export function TestProviders({ children, queryClient }: { children: ReactNode; queryClient: QueryClient }) {
  return (
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MantineProvider>
  );
}

type ProviderOptions = Omit<RenderOptions, 'wrapper'> & {
  queryClient?: QueryClient;
  /** Renders inside a router at this address, for components that link or navigate. */
  route?: string;
};

/** Renders `ui` with the theme and a query client (and a router when `route` is given). */
export function renderWithProviders(ui: ReactElement, { queryClient = createTestQueryClient(), route, ...options }: ProviderOptions = {}) {
  let element = ui;
  if (route) {
    const rootRoute = createRootRoute({ component: () => ui });
    const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: [route] }) });
    element = <RouterProvider router={router} />;
  }
  const result = render(element, {
    ...options,
    wrapper: ({ children }) => <TestProviders queryClient={queryClient}>{children}</TestProviders>,
  });
  return { ...result, queryClient };
}

type GatewayHandler = (request: { path: string; method: string; body: unknown; url: URL }) => Response | unknown | Promise<Response | unknown>;

/**
 * Stubs the gateway: `routes` maps "METHOD /path" (or "/path" for any method)
 * to a handler returning a Response or a JSON body. Unmatched calls answer 404,
 * so a test never reaches a real server. Returns the fetch mock.
 */
export function stubGateway(routes: Record<string, GatewayHandler>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), 'http://localhost');
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const handler = routes[`${method} ${url.pathname}`] ?? routes[url.pathname];
    if (!handler) return Response.json({ detail: 'not_stubbed' }, { status: 404 });
    let body: unknown = undefined;
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    const result = await handler({ path: url.pathname, method, body, url });
    return result instanceof Response ? result : Response.json(result);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
