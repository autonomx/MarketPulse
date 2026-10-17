import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { TradingLayout, TradingLinkState } from './tradingStore';

type LayoutOption = {
  id: TradingLayout;
  label: string;
  preview: string;
};

const layoutOptions = (count: number): LayoutOption[] => {
  if (count === 1) return [{ id: 'columns-1', label: 'Single', preview: 'one' }];
  if (count === 2) return [
    { id: 'columns-2', label: 'Columns', preview: 'columns-2' },
    { id: 'rows-2', label: 'Rows', preview: 'rows-2' },
  ];
  if (count === 3) return [
    { id: 'main-left-3', label: 'Main left', preview: 'main-left-3' },
    { id: 'main-right-3', label: 'Main right', preview: 'main-right-3' },
    { id: 'main-top-3', label: 'Main top', preview: 'main-top-3' },
    { id: 'main-bottom-3', label: 'Main bottom', preview: 'main-bottom-3' },
    { id: 'columns-3', label: 'Columns', preview: 'columns-3' },
  ];
  return [
    { id: 'columns-2', label: '2 columns', preview: 'columns-2' },
    { id: 'columns-3', label: '3 columns', preview: 'columns-3' },
    { id: 'columns-4', label: '4 columns', preview: 'columns-4' },
    { id: 'rows-2', label: '2 rows', preview: 'rows-2' },
    { id: 'rows-3', label: '3 rows', preview: 'rows-3' },
    { id: 'rows-4', label: '4 rows', preview: 'rows-4' },
  ];
};

const syncDescriptions: Record<keyof TradingLinkState, string> = {
  instrument: 'Symbol changes on all charts within the layout',
  interval: 'Interval changes on all charts within the layout',
  crosshair: 'Crosshair movement is shared across all charts within the layout',
  visibleRange: 'Zooming and date-range changes are shared across all charts within the layout',
  time: 'Clicking a bar scrolls the other charts to its time, whatever their interval',
};

function previewStyle(preview: string): CSSProperties {
  const columns = preview.match(/^columns-(\d)$/)?.[1];
  const rows = preview.match(/^rows-(\d)$/)?.[1];
  return {
    ...(columns ? { '--preview-columns': columns } : {}),
    ...(rows ? { '--preview-rows': rows } : {}),
  } as CSSProperties;
}

function Preview({ option, count }: { option: LayoutOption; count: number }) {
  const cells = option.preview === 'one' ? 1 : option.preview.includes('3') || option.preview.includes('2') ? 3 : count;
  const normalizedCells = option.preview.startsWith('columns-') || option.preview.startsWith('rows-')
    ? Math.min(count, Number(option.preview.at(-1)))
    : cells;
  return (
    <span className={`trading-layout-preview preview-${option.preview}`} style={previewStyle(option.preview)} aria-hidden="true">
      {Array.from({ length: normalizedCells }, (_, index) => <i key={index} />)}
    </span>
  );
}

export function TradingChartLayoutPicker({
  chartCount,
  maximumChartCount,
  layout,
  links,
  onSetChartCount,
  onSetLayout,
  onSetLink,
}: {
  chartCount: number;
  maximumChartCount: number;
  layout: TradingLayout;
  links: TradingLinkState;
  onSetChartCount: (count: number) => void;
  onSetLayout: (layout: TradingLayout) => void;
  onSetLink: (key: keyof TradingLinkState, enabled: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const counts = useMemo(
    () => Array.from({ length: maximumChartCount }, (_, index) => index + 1),
    [maximumChartCount],
  );

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const choose = (count: number, nextLayout: TradingLayout) => {
    onSetChartCount(count);
    onSetLayout(nextLayout);
    setOpen(false);
  };

  return (
    <div className="trading-chart-layout-picker" ref={rootRef}>
      <select
        className="trading-chart-layout-count"
        aria-label="Number of charts"
        value={chartCount}
        onChange={(event) => onSetChartCount(Number(event.target.value))}
      >
        {counts.map((count) => <option key={count} value={count}>{count} chart{count === 1 ? '' : 's'}</option>)}
      </select>
      <button
        type="button"
        className="trading-chart-layout-trigger"
        aria-label="Open chart layout menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Preview option={layoutOptions(chartCount).find((option) => option.id === layout) ?? layoutOptions(chartCount)[0]} count={chartCount} />
      </button>
      {open ? (
        <div className="trading-chart-layout-menu" role="menu" aria-label="Chart layouts">
          <div className="trading-chart-layout-menu-header">
            <strong>Chart layout</strong>
            <span>Select a grid for each chart count</span>
          </div>
          {counts.map((count) => (
            <div className="trading-chart-layout-row" key={count}>
              <span className="trading-chart-layout-row-count">{count}</span>
              <div className="trading-chart-layout-options">
                {layoutOptions(count).map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={chartCount === count && layout === option.id}
                    aria-label={`${count} chart${count === 1 ? '' : 's'} · ${option.label}`}
                    aria-pressed={chartCount === count && layout === option.id}
                    className="trading-chart-layout-option"
                    onClick={() => choose(count, option.id)}
                  >
                    <Preview option={option} count={count} />
                    <small>{option.label}</small>
                  </button>
                ))}
              </div>
            </div>
          ))}
          <section className="trading-chart-layout-sync" aria-label="Sync in layout">
            <h3>Sync in layout</h3>
            {([
              ['instrument', 'Symbol'],
              ['interval', 'Interval'],
              ['crosshair', 'Crosshair'],
              ['visibleRange', 'Date range'],
              ['time', 'Time'],
            ] as Array<[keyof TradingLinkState, string]>).map(([key, label]) => (
              <div className="trading-chart-layout-sync-row" key={key}>
                <span className="trading-chart-layout-sync-label">
                  {label}
                  <button
                    type="button"
                    className="trading-chart-layout-info"
                    aria-label={`About ${label} synchronization`}
                    data-tooltip={syncDescriptions[key]}
                    title={syncDescriptions[key]}
                  >
                    i
                  </button>
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={Boolean(links[key])}
                  aria-label={`Sync ${label}`}
                  className={links[key] ? 'active' : undefined}
                  onClick={() => onSetLink(key, !links[key])}
                >
                  <i aria-hidden="true" />
                </button>
              </div>
            ))}
          </section>
        </div>
      ) : null}
    </div>
  );
}
