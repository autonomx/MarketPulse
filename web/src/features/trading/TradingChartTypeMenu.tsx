import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  TRADING_CHART_TYPE_GROUPS,
  TRADING_CHART_TYPE_OPTIONS,
  type TradingChartType,
} from './chart/chartAdapter';
import { TradingChartTypeIcon } from './TradingChartTypeIcon';
import './TradingChartTypeMenu.css';

const favoritesStorageKey = 'omnix.trading.chart-type-favorites';
type ChartTypeOption = typeof TRADING_CHART_TYPE_OPTIONS[number];

function isChartType(value: unknown): value is TradingChartType {
  return typeof value === 'string' && TRADING_CHART_TYPE_OPTIONS.some((option) => option.value === value);
}

function readFavorites(): Set<TradingChartType> {
  if (typeof window === 'undefined') return new Set();
  try {
    const stored = JSON.parse(window.localStorage.getItem(favoritesStorageKey) ?? '[]') as unknown;
    return new Set(Array.isArray(stored) ? stored.filter(isChartType) : []);
  } catch {
    return new Set();
  }
}

export function TradingChartTypeMenu({
  value,
  onChange,
}: {
  value: TradingChartType;
  onChange: (value: TradingChartType) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const [favorites, setFavorites] = useState<Set<TradingChartType>>(readFavorites);
  const selected = TRADING_CHART_TYPE_OPTIONS.find((option) => option.value === value) ?? TRADING_CHART_TYPE_OPTIONS[0];

  useEffect(() => {
    try {
      window.localStorage.setItem(favoritesStorageKey, JSON.stringify([...favorites]));
    } catch {
      // Favorites remain usable for this session if storage is unavailable.
    }
  }, [favorites]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target) && !menuRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener('pointerdown', closeOnOutsidePointer);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('pointerdown', closeOnOutsidePointer);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !menuRef.current || !triggerRef.current) return;
    const margin = 8;
    const triggerBounds = triggerRef.current.getBoundingClientRect();
    const menuBounds = menuRef.current.getBoundingClientRect();
    const left = Math.max(margin, Math.min(triggerBounds.left, window.innerWidth - menuBounds.width - margin));
    const below = triggerBounds.bottom + 4;
    const top = below + menuBounds.height <= window.innerHeight - margin
      ? below
      : Math.max(margin, triggerBounds.top - menuBounds.height - 4);
    setMenuPosition((current) => current.top === top && current.left === left ? current : { top, left });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const reposition = () => {
      if (!triggerRef.current || !menuRef.current) return;
      const margin = 8;
      const triggerBounds = triggerRef.current.getBoundingClientRect();
      const menuBounds = menuRef.current.getBoundingClientRect();
      const left = Math.max(margin, Math.min(triggerBounds.left, window.innerWidth - menuBounds.width - margin));
      const below = triggerBounds.bottom + 4;
      const top = below + menuBounds.height <= window.innerHeight - margin
        ? below
        : Math.max(margin, triggerBounds.top - menuBounds.height - 4);
      setMenuPosition({ top, left });
    };
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [open]);

  const choose = (next: TradingChartType) => {
    onChange(next);
    setOpen(false);
  };

  const toggleFavorite = (next: TradingChartType) => {
    setFavorites((current) => {
      const updated = new Set(current);
      if (updated.has(next)) updated.delete(next);
      else updated.add(next);
      return updated;
    });
  };

  return (
    <div ref={rootRef} className="trading-chart-type-picker">
      <button
        ref={triggerRef}
        type="button"
        className="trading-chart-type-trigger"
        aria-label="Chart type"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={`Chart type: ${selected.label}`}
        onClick={() => setOpen((current) => !current)}
      >
        <TradingChartTypeIcon kind={selected.icon} size={24} />
        <span className="trading-chart-type-trigger-caret" aria-hidden="true">⌄</span>
      </button>
      {open ? (
        <div
          ref={menuRef}
          className="trading-chart-type-menu"
          role="listbox"
          aria-label="TradingView chart types"
          style={{ top: menuPosition.top, left: menuPosition.left }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {TRADING_CHART_TYPE_GROUPS.map((group) => {
            const options = TRADING_CHART_TYPE_OPTIONS.filter((option) => option.group === group.id);
            return (
              <section key={group.id} className="trading-chart-type-group" role="group" aria-label={group.label}>
                {options.map((option: ChartTypeOption) => {
                  const favorite = favorites.has(option.value);
                  return (
                    <div key={option.value} className={`trading-chart-type-row${value === option.value ? ' selected' : ''}`}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={value === option.value}
                        className="trading-chart-type-option"
                        onClick={() => choose(option.value)}
                      >
                        <TradingChartTypeIcon kind={option.icon} size={27} />
                        <span>{option.label}</span>
                      </button>
                      <button
                        type="button"
                        className="trading-chart-type-favorite"
                        aria-label={`${favorite ? 'Remove' : 'Add'} ${option.label} ${favorite ? 'from' : 'to'} chart favorites`}
                        aria-pressed={favorite}
                        title={favorite ? 'Remove from favorites' : 'Add to favorites'}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          toggleFavorite(option.value);
                        }}
                      >
                        {favorite ? '★' : '☆'}
                      </button>
                    </div>
                  );
                })}
              </section>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
