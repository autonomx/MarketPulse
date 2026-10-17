import './app/viewApiFirewallBootstrap';
import './shared/installPrompt';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import './design/layers.css';
import './design/tokens.css';
import '@mantine/core/styles.layer.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OmnixApp } from './app/OmnixApp';
import { installClientErrorReporting } from './app/clientErrorReporter';
import { omnixTheme } from './design/theme';
import './styles.css';
import './appearance-overrides.css';
import './theme-presets.css';
import './liquid-glass-theme.css';

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

async function mountApplication(): Promise<void> {
  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <QueryClientProvider client={queryClient}>
          <OmnixApp />
        </QueryClientProvider>
      </MantineProvider>
    </React.StrictMode>,
  );
}

void mountApplication();
