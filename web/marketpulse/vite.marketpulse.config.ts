import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The MarketPulse web build (roadmap MP-4.1): this folder's index.html and main.tsx over the mirrored Omnix source.
const here = fileURLToPath(new URL('.', import.meta.url));
const gateway = process.env.VITE_GATEWAY_ORIGIN?.trim() || 'http://127.0.0.1:47800';

// Omnix's typography rule (vite.config.ts): pixel font sizes become rem so the text scale applies.
function scalableTypographyPostcssPlugin() {
  return {
    postcssPlugin: 'omnix-scalable-typography',
    Declaration(declaration: { prop: string; value: string }) {
      if (declaration.prop.toLowerCase() !== 'font-size' || !declaration.value.includes('px')) return;
      declaration.value = declaration.value.replace(/(-?\d*\.?\d+)px\b/gu, (match, rawValue: string) => {
        const pixels = Number(rawValue);
        return Number.isFinite(pixels) ? `${Number((pixels / 16).toFixed(6))}rem` : match;
      });
    },
  };
}

export default defineConfig({
  root: here,
  publicDir: 'public',
  plugins: [react()],
  // Visible text names MarketPulse (Omnix S-3); fixed at build time so module-level strings see it.
  define: { 'import.meta.env.VITE_PRODUCT_NAME': JSON.stringify('MarketPulse') },
  css: { postcss: { plugins: [scalableTypographyPostcssPlugin()] } },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          const path = id.replaceAll('\\', '/');
          if (path.includes('/node_modules/lightweight-charts/') || path.includes('/node_modules/fancy-canvas/')) return 'trading-chart-engine';
          return undefined;
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    fs: { allow: ['..'] },
    proxy: Object.fromEntries(['/api', '/events', '/health', '/ready'].map((path) => [path, { target: gateway, changeOrigin: false, ws: true }])),
  },
  preview: { host: '127.0.0.1', port: 4174 },
});
