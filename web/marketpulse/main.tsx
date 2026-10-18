import './bootstrap';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import '../src/design/layers.css';
import '../src/design/tokens.css';
import '@mantine/core/styles.layer.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { installClientErrorReporting } from '../src/app/clientErrorReporter';
import { omnixTheme } from '../src/design/theme';
import '../src/styles.css';
import '../src/appearance-overrides.css';
import '../src/theme-presets.css';
import '../src/liquid-glass-theme.css';
import './marketpulse.css';
import { AppearanceFollower, applyStoredAppearance } from './appearance';
import { router } from './router';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: 1,
    },
  },
});

// Uncaught errors and unhandled rejections are reported to the gateway (WP-9.9).
installClientErrorReporting();
const colorScheme = applyStoredAppearance();

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <MantineProvider theme={omnixTheme} defaultColorScheme={colorScheme}>
      <AppearanceFollower />
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
