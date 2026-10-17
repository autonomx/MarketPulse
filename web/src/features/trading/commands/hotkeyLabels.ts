import { parseHotkey } from './hotkeys';
import type { TradingCommandDefinition } from './tradingCommands';

export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
}

const KEY_LABELS: Record<string, string> = {
  arrowleft: '←',
  arrowright: '→',
  arrowup: '↑',
  arrowdown: '↓',
  pageup: 'Page Up',
  pagedown: 'Page Down',
  enter: 'Enter',
  tab: 'Tab',
  space: 'Space',
  delete: 'Delete',
  backspace: 'Backspace',
  escape: 'Esc',
  '[plus]': '+',
  plus: '+',
  slash: '/',
};

/** A hotkey string as people read it: `mod+shift+z` is "Ctrl+Shift+Z" (or "⌘+⇧+Z" on macOS). */
export function formatHotkey(hotkey: string, mac = isMacPlatform()): string {
  const modifierLabels: Record<string, string> = mac
    ? { mod: '⌘', ctrl: '⌃', meta: '⌘', alt: '⌥', shift: '⇧' }
    : { mod: 'Ctrl', ctrl: 'Ctrl', meta: 'Win', alt: 'Alt', shift: 'Shift' };
  const parsed = parseHotkey(hotkey);
  const modifiers = (['mod', 'ctrl', 'meta', 'alt', 'shift'] as const).filter((modifier) => parsed[modifier]).map((modifier) => modifierLabels[modifier]);
  return [...modifiers, KEY_LABELS[parsed.key] ?? parsed.key.toUpperCase()].join('+');
}

/** The keys a command lists, including typed-character patterns. */
export function formatCommandKeys(definition: TradingCommandDefinition, keys: readonly string[], mac = isMacPlatform()): string[] {
  if (definition.keyPattern === 'letter') return ['A–Z'];
  if (definition.keyPattern === 'interval') return ['0–9', ','];
  return keys.map((hotkey) => formatHotkey(hotkey, mac));
}
