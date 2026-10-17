/**
 * Whether U.S. equity trading is open or in its extended sessions (04:00–20:00 ET, Monday to Friday), for the
 * optional "keep the computer awake during market hours" setting. Holidays are not excluded: staying awake on one is
 * harmless.
 */
export function isMarketHours(now: Date): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: 'numeric', hour12: false,
  }).formatToParts(now);
  const weekday = parts.find((part) => part.type === 'weekday')?.value ?? '';
  const hour = Number(parts.find((part) => part.type === 'hour')?.value ?? '0') % 24;
  if (weekday === 'Sat' || weekday === 'Sun') return false;
  return hour >= 4 && hour < 20;
}
