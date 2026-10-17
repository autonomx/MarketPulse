import { installViewApiFirewall } from '../src/app/viewApiScope';
import { marketPulseScopes } from './scopes';

// Installed first, as in Omnix's main.tsx: every request through the fetch pipeline is scoped to the active page and
// gets the client and CSRF headers. Only MarketPulse's scopes are passed, so no other feature is bundled (W-1).
installViewApiFirewall(marketPulseScopes);
