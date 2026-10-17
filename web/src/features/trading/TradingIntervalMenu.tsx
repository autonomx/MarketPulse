import { useState, type FormEvent } from 'react';
import {
  intervalAvailability,
  intervalCompactLabel,
  intervalMenuLabel,
  parseTradingInterval,
  sortTradingIntervals,
  TRADING_VIEW_INTERVAL_GROUPS,
} from './tradingIntervals';
import { useTradingStore } from './tradingStore';

/** Result of typing an interval: the value to apply, or why it can't be applied. */
export function resolveCustomInterval(
  input: string,
  supportedIntervals: readonly string[],
  feedName?: string,
): { interval: string } | { error: string } {
  const parsed = parseTradingInterval(input);
  if (!parsed.ok) return { error: parsed.error };
  const availability = intervalAvailability(parsed.value, supportedIntervals, feedName);
  if (!availability.available) return { error: availability.reason ?? `${intervalMenuLabel(parsed.value)} is not available.` };
  return { interval: parsed.value };
}

/**
 * The toolbar's interval controls: favourite intervals as quick buttons, and the full interval
 * menu with a custom-interval box (any count of t/s/m/h/D/W/M/R) and favourite stars (TVP-2.5).
 */
export function TradingIntervalMenu({
  interval,
  supportedIntervals,
  feedName,
  onSelect,
}: {
  interval: string;
  supportedIntervals: readonly string[];
  feedName?: string;
  onSelect: (interval: string) => void;
}) {
  const favoriteIntervals = useTradingStore((state) => state.favoriteIntervals);
  const toggleFavoriteInterval = useTradingStore((state) => state.toggleFavoriteInterval);
  const addFavoriteInterval = useTradingStore((state) => state.addFavoriteInterval);
  const [open, setOpen] = useState(false);
  const [customInput, setCustomInput] = useState('');
  const [customError, setCustomError] = useState<string | null>(null);
  const feed = feedName ?? 'the selected feed';
  const quickIntervals = favoriteIntervals.filter((item) => intervalAvailability(item, supportedIntervals).available);
  const listedValues = new Set(TRADING_VIEW_INTERVAL_GROUPS.flatMap((group) => group.options.map((option) => option.value)));
  const customFavorites = sortTradingIntervals(favoriteIntervals.filter((item) => !listedValues.has(item)));

  const applyCustom = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const result = resolveCustomInterval(customInput, supportedIntervals, feed);
    if ('error' in result) {
      setCustomError(result.error);
      return;
    }
    setCustomError(null);
    setCustomInput('');
    addFavoriteInterval(result.interval);
    onSelect(result.interval);
    event.currentTarget.closest('details')?.removeAttribute('open');
  };

  const option = (value: string, label: string) => {
    const availability = intervalAvailability(value, supportedIntervals, feed);
    const derived = availability.baseInterval !== null && availability.baseInterval !== value;
    const favorite = favoriteIntervals.includes(value);
    return (
      <div key={value} className="trading-interval-row">
        <button
          type="button"
          className="trading-interval-option"
          aria-pressed={interval === value}
          disabled={!availability.available}
          title={availability.available
            ? derived ? `${label} · calculated from ${intervalCompactLabel(availability.baseInterval ?? value)}` : label
            : availability.reason ?? label}
          onClick={(event) => {
            onSelect(value);
            event.currentTarget.closest('details')?.removeAttribute('open');
          }}
        >
          {label}
        </button>
        <button
          type="button"
          className="trading-interval-favorite"
          aria-label={favorite ? `Remove ${label} from favourite intervals` : `Add ${label} to favourite intervals`}
          aria-pressed={favorite}
          title={favorite ? 'Remove from favourites' : 'Add to favourites'}
          onClick={() => toggleFavoriteInterval(value)}
        >
          {favorite ? '★' : '☆'}
        </button>
      </div>
    );
  };

  return (
    <div className="trading-timeframe-buttons" role="group" aria-label="Trading timeframe">
      {quickIntervals.map((item) => (
        <button
          key={item}
          type="button"
          className={interval === item ? 'active' : undefined}
          aria-pressed={interval === item}
          onClick={() => onSelect(item)}
        >
          {intervalCompactLabel(item)}
        </button>
      ))}
      <details className="trading-interval-manager" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary
          role="button"
          aria-label="All supported Trading intervals"
          aria-haspopup="dialog"
          aria-expanded={open}
        >
          <span>{intervalCompactLabel(interval)}</span>
          <span className="trading-menu-caret" aria-hidden="true">⌄</span>
        </summary>
        {/* A dialog, not a listbox: it holds a form and favourite buttons beside the intervals. */}
        <div className="trading-interval-menu" role="dialog" aria-label="TradingView intervals">
          <form className="trading-interval-custom" aria-label="Custom interval" onSubmit={applyCustom}>
            <input
              aria-label="Custom interval"
              placeholder="Custom: 7m, 3h, 2D"
              value={customInput}
              onChange={(event) => { setCustomInput(event.target.value); setCustomError(null); }}
            />
            <button type="submit">Add</button>
            {customError ? <small role="alert">{customError}</small> : null}
          </form>
          {customFavorites.length > 0 ? (
            <section className="trading-interval-group" role="group" aria-label="Custom">
              <header>Custom</header>
              {customFavorites.map((value) => option(value, intervalMenuLabel(value)))}
            </section>
          ) : null}
          {TRADING_VIEW_INTERVAL_GROUPS.map((group) => (
            <section key={group.label} className="trading-interval-group" role="group" aria-label={group.label}>
              <header>{group.label}<span aria-hidden="true">⌃</span></header>
              {group.options.map((item) => option(item.value, item.label))}
            </section>
          ))}
        </div>
      </details>
    </div>
  );
}
