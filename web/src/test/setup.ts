import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'scrollTo', {
    configurable: true,
    value: () => undefined,
  });

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// Selections live in the URL's search parameters (WP-9.6); each test starts without them.
if (typeof window !== 'undefined') {
  afterEach(() => {
    if (window.location.search) window.history.replaceState(window.history.state, '', window.location.pathname);
  });
}
