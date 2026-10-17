import { normalizeHotkey } from './hotkeys';
import { isRebindable, rebindProblem, tradingCommandDefinition, type KeyOverrides } from './tradingCommands';
import { setTradingCommandKeyOverrides } from './useTradingCommands';

/**
 * Rebound keys are kept in this browser for now, like the other trading view
 * preferences. A per-user server document (the trading settings document of
 * TVP-0.3) can replace this storage without changing the dialog.
 */
export const KEY_OVERRIDES_STORAGE_KEY = 'omnix.trading.key-overrides';

/**
 * Keeps only known, rebindable commands with keys the dialog would accept
 * (browser-reserved keys are kept: the installed app can use them).
 */
export function sanitizeKeyOverrides(value: unknown): KeyOverrides {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {};
  const overrides: Record<string, readonly string[]> = {};
  for (const [id, keys] of Object.entries(value)) {
    const definition = tradingCommandDefinition(id);
    if (!definition || !isRebindable(definition) || !Array.isArray(keys)) continue;
    const hotkeys = keys.filter((key): key is string => typeof key === 'string' && key.trim() !== '').map(normalizeHotkey)
      .filter((key) => rebindProblem(definition, key, 'installed') === null);
    // An override left with no keys would leave the command dead; drop it so the defaults apply.
    if (hotkeys.length > 0) overrides[id] = [...new Set(hotkeys)];
  }
  return overrides;
}

export function loadStoredKeyOverrides(): KeyOverrides {
  try {
    return sanitizeKeyOverrides(JSON.parse(window.localStorage.getItem(KEY_OVERRIDES_STORAGE_KEY) ?? '{}'));
  } catch {
    return {};
  }
}

/** Applies overrides to the dispatcher and keeps them for the next visit. */
export function saveKeyOverrides(overrides: KeyOverrides): void {
  setTradingCommandKeyOverrides(overrides);
  try {
    if (Object.keys(overrides).length === 0) window.localStorage.removeItem(KEY_OVERRIDES_STORAGE_KEY);
    else window.localStorage.setItem(KEY_OVERRIDES_STORAGE_KEY, JSON.stringify(overrides));
  } catch {
    // Storage can be unavailable (private mode, quota); the keys still apply for this session.
  }
}
