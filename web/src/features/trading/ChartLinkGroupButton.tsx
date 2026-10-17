import { useEffect, useRef, useState } from 'react';
import { CHART_LINK_GROUP_LABELS, CHART_LINK_GROUPS, type ChartLinkGroup } from './chartLinkGroups';
import { useTradingStore } from './tradingStore';
import './ChartLinkGroupButton.css';

const OPTIONS: Array<ChartLinkGroup | null> = [...CHART_LINK_GROUPS, null];

/** The chart's colour link group (TVP-4.1): charts in a group share their symbol, across tabs. */
export function ChartLinkGroupButton({ chartId, group }: { chartId: string; group: ChartLinkGroup | undefined }) {
  const setChartLinkGroup = useTradingStore((state) => state.setChartLinkGroup);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!open) return;
    // The checked item takes focus; a press outside closes the menu.
    rootRef.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);
  const shut = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };
  const choose = (next: ChartLinkGroup | null) => {
    setChartLinkGroup(chartId, next);
    shut();
  };
  const onMenuKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = [...(rootRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      shut();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      items[(at + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
    }
  };
  const label = group ? `${CHART_LINK_GROUP_LABELS[group]} link group` : 'No link group';
  return (
    <div ref={rootRef} className="chart-link-group">
      <button
        ref={buttonRef}
        type="button"
        className={`chart-link-group-button${group ? ` is-${group}` : ''}`}
        aria-label={`${label}: change`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={group ? `${label}: charts in it share their symbol` : 'Link this chart to a colour group'}
        onClick={() => setOpen((value) => !value)}
      />
      {open ? (
        <div className="chart-link-group-menu" role="menu" aria-label="Link group" onKeyDown={onMenuKey}>
          {OPTIONS.map((item) => (
            <button
              key={item ?? 'none'}
              type="button"
              role="menuitemradio"
              aria-checked={(group ?? null) === item}
              className={item ? `is-${item}` : undefined}
              onClick={() => choose(item)}
            >
              {item ? <i aria-hidden="true" /> : null}{item ? CHART_LINK_GROUP_LABELS[item] : 'No link group'}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
