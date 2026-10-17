import rules from '../../config/nav-rules.json';

export type NavigationDecision = 'stay' | 'window' | 'open-external' | 'deny';

/**
 * Where a URL goes (config/nav-rules.json): the gateway's own pages stay in the app (a window.open of one becomes a
 * native window, for window sets), other http(s) pages open in the system browser, and everything else is refused.
 * Loopback addresses other than the gateway are refused too, so a page cannot reach the user's other local services.
 */
export function classify(url: string, origin: string, kind: 'navigate' | 'window-open'): NavigationDecision {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return 'deny';
  }
  if (parsed.origin === origin) return kind === 'window-open' ? (rules.internal.windowOpen as NavigationDecision) : 'stay';
  if (!rules.external.schemes.includes(parsed.protocol)) return rules.otherwise as NavigationDecision;
  if (rules.external.deniedHosts.includes(parsed.hostname) || parsed.username || parsed.password) return 'deny';
  return rules.external.action as NavigationDecision;
}
