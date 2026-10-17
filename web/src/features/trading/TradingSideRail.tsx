import type { TradingSideTab } from './TradingSidePanel';
import './TradingSideRail.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

const railTabs: Array<{ id: TradingSideTab; label: string; glyph: string }> = [
  { id: 'pine', label: `${PRODUCT_NAME} Scripts`, glyph: '{}' },
  { id: 'objects', label: 'Object tree', glyph: '▱' },
  { id: 'paper', label: 'Trade', glyph: '⇄' },
  { id: 'intelligence', label: 'Symbol Intelligence', glyph: '◎' },
  { id: 'journal', label: 'Automatic Journal', glyph: '≡' },
  { id: 'prospective', label: 'Evidence', glyph: '✓' },
  { id: 'watchlist', label: 'Watchlist', glyph: '▤' },
  { id: 'alerts', label: 'Alerts', glyph: '◷' },
  { id: 'indicators', label: 'Indicators', glyph: '◇' },
  { id: 'news', label: 'News', glyph: '▱' },
  { id: 'layout', label: 'Layout', glyph: '▦' },
];

export function TradingSideRail({
  activeTab,
  collapsed,
  onSelectTab,
  onToggle,
}: {
  activeTab: TradingSideTab;
  collapsed: boolean;
  onSelectTab: (tab: TradingSideTab) => void;
  onToggle: () => void;
}) {
  return (
    <aside className="trading-side-rail" aria-label="Trading side panel rail">
      <button
        type="button"
        className="trading-side-rail-toggle"
        aria-label={collapsed ? 'Expand right panel' : 'Collapse right panel'}
        title={collapsed ? 'Expand right panel' : 'Collapse right panel'}
        onClick={onToggle}
      >
        {collapsed ? '‹' : '›'}
      </button>
      <nav aria-label="Trading side panel shortcuts">
        {railTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={activeTab === tab.id ? 'active' : undefined}
            aria-label={tab.label}
            aria-pressed={activeTab === tab.id}
            title={tab.label}
            onClick={() => {
              if (!collapsed && activeTab === tab.id) {
                onToggle();
                return;
              }
              onSelectTab(tab.id);
              if (collapsed) onToggle();
            }}
          >
            <span aria-hidden="true">{tab.glyph}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
