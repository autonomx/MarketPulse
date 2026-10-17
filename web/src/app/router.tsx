import { useMantineColorScheme } from '@mantine/core';
import {
  Link,
  Navigate,
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
  useRouterState,
} from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { OmnixBrand, OmnixNavItem, OmnixShellLayout, OmnixSidebar, OmnixTopBar } from '../design/primitives';
import { DEFAULT_OMNIX_THEME, type OmnixThemeId } from '../design/appearanceThemes';
import { commitAppearanceSettings, DEFAULT_OMNIX_TEXT_SCALE, loadStoredAppearancePreferences, resolveAppearanceMode, type OmnixAppearanceChangeDetail, type OmnixAppearanceMode } from '../design/appearanceEffects';
import { ModuleWorkspace } from '../features/ModuleWorkspace';
import { defaultModuleId, moduleManifests, omnixModules, type OmnixModuleDefinition, type OmnixModuleId } from './modules';
import { NotFoundView } from './NotFoundView';
import { LoginPage } from './LoginPage';
import { RouteErrorFallback } from './RouteErrorBoundary';
import { SignOutButton } from './SignOutButton';
import { LOGIN_PATH, setActiveViewModule } from './viewApiScope';
import { activateViewRuntime } from './viewRuntime';
import { APPEARANCE_CHANGE_EVENT } from '../events/bus';

const moduleById = Object.fromEntries(omnixModules.map((module) => [module.id, module])) as Record<
  OmnixModuleId,
  OmnixModuleDefinition
>;
const defaultModule = moduleById[defaultModuleId];
// Top-bar modes: the default module first, then the others that declare a mode label.
const modeModules = [...moduleManifests]
  .filter((manifest) => manifest.modeLabel)
  .sort((left, right) => Number(right.id === defaultModuleId) - Number(left.id === defaultModuleId));
const sidebarModules = moduleManifests.filter((manifest) => manifest.sidebar !== false);


/** The module whose route contains `pathname`, or null (the not-found page). */
function moduleFromPath(pathname: string): OmnixModuleDefinition | null {
  return (
    [...omnixModules]
      .sort((left, right) => right.route.length - left.route.length)
      .find((module) => pathname === module.route || pathname.startsWith(`${module.route}/`)) ?? null
  );
}

function initialAppearanceMode(): OmnixAppearanceMode {
  return loadStoredAppearancePreferences().mode ?? 'dark';
}

function initialThemeId(): OmnixThemeId {
  return loadStoredAppearancePreferences().theme ?? DEFAULT_OMNIX_THEME;
}

function initialTextScale(): number {
  return loadStoredAppearancePreferences().textScale ?? DEFAULT_OMNIX_TEXT_SCALE;
}

function OmnixShell() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { setColorScheme } = useMantineColorScheme();
  const [isSidebarVisible, setIsSidebarVisible] = useState(false);
  const [appearanceMode, setAppearanceMode] = useState<OmnixAppearanceMode>(initialAppearanceMode);
  const [themeId, setThemeId] = useState<OmnixThemeId>(initialThemeId);
  const [textScale, setTextScale] = useState(initialTextScale);
  const activeModule = moduleFromPath(pathname);
  const resolvedAppearanceMode = resolveAppearanceMode(appearanceMode);

  const activeModuleId = activeModule?.id ?? null;
  useEffect(() => {
    setActiveViewModule(activeModuleId);
    if (!activeModuleId) return undefined;
    // The leaving workspace's runtime is disposed when the route changes (WP-9.1).
    const runtime = activateViewRuntime(activeModuleId, { queryClient });
    return () => runtime.dispose();
  }, [activeModuleId, queryClient]);

  useEffect(() => {
    const root = document.documentElement;
    const detail = commitAppearanceSettings({
      mode: appearanceMode,
      theme: themeId,
      density: root.dataset.omnixDensity ?? 'comfortable',
      textScale,
      reduceMotion: root.classList.contains('omnix-reduce-motion'),
    });
    setColorScheme(detail.resolvedMode);
  }, [appearanceMode, setColorScheme, textScale, themeId]);

  useEffect(() => {
    const syncAppearance = (event: Event) => {
      const detail = (event as CustomEvent<OmnixAppearanceChangeDetail>).detail;
      if (!detail) return;
      setAppearanceMode(detail.mode);
      setThemeId(detail.theme);
      setTextScale(detail.textScale);
      setColorScheme(detail.resolvedMode);
    };
    window.addEventListener(APPEARANCE_CHANGE_EVENT, syncAppearance);
    return () => window.removeEventListener(APPEARANCE_CHANGE_EVENT, syncAppearance);
  }, [setColorScheme]);

  return (
    <OmnixShellLayout
      isSidebarVisible={isSidebarVisible}
      sidebar={
        <OmnixSidebar hidden={!isSidebarVisible}>
          <OmnixBrand />
          <nav className="omnix-nav">
            {sidebarModules.map((module) => (
              <Link key={module.id} to={module.route as never} title={module.label} activeProps={{ className: 'active' }}>
                <OmnixNavItem active={module.id === activeModuleId} icon={module.icon}>
                  {module.label}
                </OmnixNavItem>
              </Link>
            ))}
          </nav>
        </OmnixSidebar>
      }
      topbar={
        <OmnixTopBar
          isSidebarVisible={isSidebarVisible}
          onToggleSidebar={() => setIsSidebarVisible((value) => !value)}
          onToggleTheme={() => setAppearanceMode(resolvedAppearanceMode === 'light' ? 'dark' : 'light')}
          onThemeChange={setThemeId}
          themeId={themeId}
          themeMode={resolvedAppearanceMode}
          title={activeModule?.label ?? 'Page not found'}
        >
          {modeModules.map((module) => (
            <button
              key={module.id}
              type="button"
              className={module.id === activeModuleId ? 'active' : undefined}
              aria-label={`Open ${module.label} mode`}
              onClick={() => void navigate({ to: module.route as never })}
            >
              {module.modeLabel}
            </button>
          ))}
          <SignOutButton />
        </OmnixTopBar>
      }
    >
      <Outlet />
    </OmnixShellLayout>
  );
}

// The sign-in page renders without the workstation shell, which would
// otherwise start workspace API traffic before a session exists.
function OmnixRoot() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return pathname === LOGIN_PATH ? <Outlet /> : <OmnixShell />;
}

const rootRoute = createRootRoute({
  component: OmnixRoot,
  // A routing failure (outside a workspace's own boundary) still renders a way back.
  errorComponent: ({ error, reset }) => <RouteErrorFallback error={error} onRetry={reset} />,
  notFoundComponent: NotFoundView,
});
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'login',
  component: LoginPage,
});
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: () => <Navigate to={defaultModule.route as never} replace />,
});

// One route per module manifest (WP-9.7).
const moduleRoutes = moduleManifests.map((manifest) => createRoute({
  getParentRoute: () => rootRoute,
  path: manifest.route,
  component: () => <ModuleWorkspace module={moduleById[manifest.id]} />,
}));

export const moduleRoutePaths = omnixModules.map((module) => module.route);

const routeTree = rootRoute.addChildren([indexRoute, loginRoute, ...moduleRoutes]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}

export function OmnixRouterProvider() {
  return <RouterProvider router={router} />;
}
