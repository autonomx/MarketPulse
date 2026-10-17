import { useEffect, useState } from 'react';
import { PriceScaleMode } from 'lightweight-charts';
import type { TradingChartAdapter, TradingPriceScaleSide } from './chart/chartAdapter';
import './TradingPriceScaleMenu.css';
import './TradingPriceScaleTrigger.css';

export type TradingPriceScaleMode = 'normal' | 'percentage' | 'indexed' | 'logarithmic';

export type TradingPriceScaleMenuState = {
  autoScale: boolean;
  mode: TradingPriceScaleMode;
  invertScale: boolean;
  side: TradingPriceScaleSide;
  labelsVisible: boolean;
  latestValueLabelVisible: boolean;
  priceScaleLinesVisible: boolean;
  gridLinesVisible: boolean;
  scalePriceOnly: boolean;
};

export const defaultTradingPriceScaleMenuState: TradingPriceScaleMenuState = {
  autoScale: true,
  mode: 'normal',
  invertScale: false,
  side: 'right',
  labelsVisible: true,
  latestValueLabelVisible: true,
  priceScaleLinesVisible: true,
  gridLinesVisible: true,
  scalePriceOnly: false,
};

const modeLabels: Array<{ id: TradingPriceScaleMode; label: string; shortcut?: string }> = [
  { id: 'normal', label: 'Regular' },
  { id: 'percentage', label: 'Percent', shortcut: 'Alt + P' },
  { id: 'indexed', label: 'Indexed to 100' },
  { id: 'logarithmic', label: 'Logarithmic', shortcut: 'Alt + L' },
];

function modeValue(mode: TradingPriceScaleMode): PriceScaleMode {
  if (mode === 'percentage') return PriceScaleMode.Percentage;
  if (mode === 'indexed') return PriceScaleMode.IndexedTo100;
  if (mode === 'logarithmic') return PriceScaleMode.Logarithmic;
  return PriceScaleMode.Normal;
}

function MenuRow({
  checked,
  children,
  shortcut,
  onClick,
  submenu,
  expanded,
}: {
  checked?: boolean;
  children: React.ReactNode;
  shortcut?: string;
  onClick: () => void;
  submenu?: boolean;
  expanded?: boolean;
}) {
  return (
    <button
      type="button"
      className="trading-price-scale-menu-row"
      role={checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
      aria-checked={checked}
      aria-haspopup={submenu ? 'menu' : undefined}
      aria-expanded={submenu ? expanded : undefined}
      onClick={onClick}
    >
      <span className="trading-price-scale-menu-check" aria-hidden="true">{checked ? '✓' : ''}</span>
      <span className="trading-price-scale-menu-label">{children}</span>
      {shortcut ? <kbd>{shortcut}</kbd> : null}
      {submenu ? <b aria-hidden="true">›</b> : null}
    </button>
  );
}

export function TradingPriceScaleMenu({
  adapter,
  state,
  onChange,
  onClose,
  onSettings,
  rightOffset,
  rightOffsetOptions,
  onRightOffsetChange,
}: {
  adapter: TradingChartAdapter;
  state: TradingPriceScaleMenuState;
  onChange: (patch: Partial<TradingPriceScaleMenuState>) => void;
  onClose: () => void;
  onSettings: () => void;
  rightOffset?: number;
  rightOffsetOptions?: readonly number[];
  onRightOffsetChange?: (offset: number) => void;
}) {
  const [labelsOpen, setLabelsOpen] = useState(false);
  const [linesOpen, setLinesOpen] = useState(false);
  const [rightOffsetOpen, setRightOffsetOpen] = useState(false);

  useEffect(() => {
    const close = () => onClose();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('pointerdown', close, { once: true });
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', keydown);
    };
  }, [onClose]);

  const setAutoScale = (autoScale: boolean) => {
    adapter.setPriceScaleAutoScale(autoScale);
    onChange({ autoScale });
  };

  const setMode = (mode: TradingPriceScaleMode) => {
    adapter.setPriceScaleMode(modeValue(mode));
    onChange({ mode });
  };

  const setSide = (side: TradingPriceScaleSide) => {
    adapter.setPriceScaleSide(side);
    onChange({ side });
  };

  const setInvert = (invertScale: boolean) => {
    adapter.setPriceScaleInvert(invertScale);
    onChange({ invertScale });
  };

  const setLabels = (labelsVisible: boolean) => {
    adapter.setPriceScaleLabelsVisible(labelsVisible);
    onChange({ labelsVisible });
  };

  const setLatestValueLabel = (latestValueLabelVisible: boolean) => {
    adapter.setLatestValueLabelVisible(latestValueLabelVisible);
    onChange({ latestValueLabelVisible });
  };

  const setScalePriceOnly = (scalePriceOnly: boolean) => {
    adapter.setScalePriceOnly(scalePriceOnly);
    onChange({ scalePriceOnly });
  };

  const setGridLines = (gridLinesVisible: boolean) => {
    adapter.setGridLinesVisible(gridLinesVisible);
    onChange({ gridLinesVisible });
  };

  const setPriceScaleLines = (priceScaleLinesVisible: boolean) => {
    adapter.setPriceScaleLinesVisible(priceScaleLinesVisible);
    onChange({ priceScaleLinesVisible });
  };

  return (
    <div
      className="trading-price-scale-menu"
      role="menu"
      aria-label="Price scale settings"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <MenuRow checked={state.autoScale} onClick={() => { adapter.fitContent(); onChange({ autoScale: true }); }}>
        Auto (fits data to screen)
      </MenuRow>
      <MenuRow checked={!state.autoScale} onClick={() => setAutoScale(!state.autoScale)}>
        Lock price to bar ratio <small>0.0068</small>
      </MenuRow>
      {onRightOffsetChange ? (
        <>
          <MenuRow submenu expanded={rightOffsetOpen} onClick={() => setRightOffsetOpen((value) => !value)}>
            Right margin <small>{rightOffset === 0 ? 'None' : String(rightOffset) + ' bars'}</small>
          </MenuRow>
          {rightOffsetOpen ? (
            <div className="trading-price-scale-submenu" role="menu" aria-label="Chart right margin">
              {(rightOffsetOptions ?? [0, 5, 10, 20, 50]).map((offset) => (
                <MenuRow
                  key={offset}
                  checked={rightOffset === offset}
                  onClick={() => {
                    onRightOffsetChange(offset);
                    setRightOffsetOpen(false);
                  }}
                >
                  {offset === 0 ? 'None' : String(offset) + ' bars'}
                </MenuRow>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
      <MenuRow checked={state.scalePriceOnly} onClick={() => setScalePriceOnly(!state.scalePriceOnly)}>
        Scale price chart only
      </MenuRow>
      <MenuRow checked={state.invertScale} shortcut="Alt + I" onClick={() => setInvert(!state.invertScale)}>
        Invert scale
      </MenuRow>

      <div className="trading-price-scale-menu-separator" />
      {modeLabels.map((item) => (
        <MenuRow key={item.id} checked={state.mode === item.id} shortcut={item.shortcut} onClick={() => setMode(item.id)}>
          {item.label}
        </MenuRow>
      ))}

      <div className="trading-price-scale-menu-separator" />
      <MenuRow onClick={() => setSide(state.side === 'right' ? 'left' : 'right')}>
        Move scale to {state.side === 'right' ? 'left' : 'right'}
      </MenuRow>
      <MenuRow submenu expanded={labelsOpen} onClick={() => setLabelsOpen((value) => !value)}>
        Labels
      </MenuRow>
      {labelsOpen ? (
        <div className="trading-price-scale-submenu" role="menu" aria-label="Price scale labels">
          <MenuRow checked={state.labelsVisible} onClick={() => setLabels(!state.labelsVisible)}>Price labels</MenuRow>
          <MenuRow checked={state.latestValueLabelVisible} onClick={() => setLatestValueLabel(!state.latestValueLabelVisible)}>Latest value label</MenuRow>
        </div>
      ) : null}
      <MenuRow submenu expanded={linesOpen} onClick={() => setLinesOpen((value) => !value)}>
        Lines
      </MenuRow>
      {linesOpen ? (
        <div className="trading-price-scale-submenu" role="menu" aria-label="Price scale lines">
          <MenuRow checked={state.gridLinesVisible} onClick={() => setGridLines(!state.gridLinesVisible)}>Grid lines</MenuRow>
          <MenuRow checked={state.priceScaleLinesVisible} onClick={() => setPriceScaleLines(!state.priceScaleLinesVisible)}>Price scale border</MenuRow>
        </div>
      ) : null}
      <div className="trading-price-scale-menu-separator" />
      <MenuRow onClick={() => { onClose(); onSettings(); }}>
        ◇&nbsp; More settings…
      </MenuRow>
    </div>
  );
}
