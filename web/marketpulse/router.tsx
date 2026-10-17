import { Suspense } from 'react';
import { createRootRoute, createRoute, createRouter, Outlet, redirect } from '@tanstack/react-router';
import type { OmnixModuleDefinition } from '../src/app/modules';
import { LoginPage } from '../src/app/LoginPage';
import { NotFoundPage } from './NotFoundPage';
import { lazyWithRetry, RouteErrorBoundary, RouteErrorFallback } from '../src/app/RouteErrorBoundary';
import { tradingModule } from '../src/features/trading/module';
import { FirstRunWelcome } from './FirstRunWelcome';
import { SettingsPage } from './SettingsPage';
import { StartupNotice } from './StartupNotice';

// The trading workspace, loaded like Omnix's ModuleWorkspace but without Omnix's module registry (W-1).
const tradingDefinition = {
  id: tradingModule.id,
  label: tradingModule.label,
  summary: tradingModule.summary,
  route: tradingModule.route,
} as OmnixModuleDefinition;
const TradingWorkspace = lazyWithRetry(() => tradingModule.loadWorkspace().then((component) => ({ default: component })));

function TradingRoute() {
  return (
    <RouteErrorBoundary resetKey="trading">
      <Suspense fallback={<StartupNotice message="Loading charts…" />}>
        <TradingWorkspace module={tradingDefinition} />
      </Suspense>
    </RouteErrorBoundary>
  );
}

const rootRoute = createRootRoute({
  component: () => <Outlet />,
  errorComponent: ({ error, reset }) => <RouteErrorFallback error={error} onRetry={reset} />,
  notFoundComponent: NotFoundPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/trading' });
  },
});

const tradingRoute = createRoute({ getParentRoute: () => rootRoute, path: 'trading', component: TradingRoute });

const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: 'login', component: LoginPage });

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'settings',
  // The trading workspace opens a category (`?category=trading-market-data`).
  validateSearch: (search: Record<string, unknown>): { category?: string } => (
    typeof search.category === 'string' ? { category: search.category } : {}
  ),
  component: SettingsPage,
});

const welcomeRoute = createRoute({ getParentRoute: () => rootRoute, path: 'welcome', component: FirstRunWelcome });

export const router = createRouter({
  routeTree: rootRoute.addChildren([indexRoute, tradingRoute, loginRoute, settingsRoute, welcomeRoute]),
  defaultPreload: false,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
