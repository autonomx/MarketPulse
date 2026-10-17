// The product name the trading UI shows, fixed at build time (MarketPulse S-3).
interface ImportMetaEnv {
  readonly VITE_PRODUCT_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
