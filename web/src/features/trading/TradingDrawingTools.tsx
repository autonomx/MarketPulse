/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { DrawingTool } from './drawings/drawingCommands';
import { DRAWING_TOOL_DEFINITIONS } from './drawings/tools/registry';
import { TradingDrawingFavoritesBar, TradingDrawingToolToggles, type FavoriteTool } from './TradingDrawingToolToggles';
import './TradingDrawingTools.css';

type DrawingToolItem = {
  label: string;
  glyph: string;
  tool?: DrawingTool;
  shortcut?: string;
  /** Marks a catalogue placeholder; a registered tool with the same label makes it available. */
  available?: boolean;
};

type DrawingToolGroup = {
  id: string;
  label: string;
  glyph: string;
  items: DrawingToolItem[];
};

/** The toolbar catalogue. Items without a `tool` light up once a registered tool has their label. */
export const drawingToolGroups: DrawingToolGroup[] = [
  {
    id: 'cursor',
    label: 'Cursor',
    glyph: '↖',
    items: [
      { label: 'Cross', glyph: '+', tool: 'cursor' },
      { label: 'Dot', glyph: '•', tool: 'dot' },
      { label: 'Arrow', glyph: '↗', tool: 'arrow' },
      { label: 'Demonstration', glyph: '◉', available: false },
      { label: 'Magic', glyph: '*', available: false },
      { label: 'Eraser', glyph: '⌫', tool: 'eraser' },
    ],
  },
  {
    id: 'lines',
    label: 'Lines',
    glyph: '/',
    items: [
      { label: 'Trend line', glyph: '/', tool: 'trend-line', shortcut: 'Alt + T' },
      { label: 'Ray', glyph: '↗', tool: 'ray' },
      { label: 'Info line', glyph: '↗', available: false },
      { label: 'Extended line', glyph: '↗', available: false },
      { label: 'Trend angle', glyph: '∠', available: false },
      { label: 'Horizontal line', glyph: '—', tool: 'horizontal-line', shortcut: 'Alt + H' },
      { label: 'Horizontal ray', glyph: '→', tool: 'horizontal-ray', shortcut: 'Alt + J' },
      { label: 'Vertical line', glyph: '|', tool: 'vertical-line', shortcut: 'Alt + V' },
      { label: 'Crossline', glyph: '+', tool: 'crossline', shortcut: 'Alt + C' },
      { label: 'Price range', glyph: '↕', tool: 'measurement' },
      { label: 'Date range', glyph: '↔', available: false },
      { label: 'Date and price range', glyph: '□', available: false },
    ],
  },
  {
    id: 'channels',
    label: 'Channels',
    glyph: '∥',
    items: [
      { label: 'Parallel channel', glyph: '∥', available: false },
      { label: 'Regression trend', glyph: '▱', available: false },
      { label: 'Flat top/bottom', glyph: '=', available: false },
      { label: 'Disjoint channel', glyph: '⋈', available: false },
    ],
  },
  {
    id: 'pitchforks',
    label: 'Pitchforks',
    glyph: 'Y',
    items: [
      { label: 'Pitchfork', glyph: 'Y', available: false },
      { label: 'Schiff pitchfork', glyph: 'Y', available: false },
      { label: 'Modified Schiff pitchfork', glyph: 'Y', available: false },
      { label: 'Inside pitchfork', glyph: 'Y', available: false },
    ],
  },
  {
    id: 'fibonacci',
    label: 'Fibonacci',
    glyph: '≋',
    items: [
      { label: 'Fib retracement', glyph: '≋', tool: 'fibonacci', shortcut: 'Alt + F' },
      { label: 'Trend-based fib extension', glyph: '≋', available: false },
      { label: 'Fib channel', glyph: '≋', available: false },
      { label: 'Fib time zone', glyph: '║', available: false },
      { label: 'Fib speed resistance fan', glyph: '⌁', available: false },
      { label: 'Trend-based fib time', glyph: '⌁', available: false },
      { label: 'Fib circles', glyph: '◎', available: false },
      { label: 'Fib spiral', glyph: '@', available: false },
      { label: 'Fib speed resistance arcs', glyph: '◔', available: false },
      { label: 'Fib wedge', glyph: '◢', available: false },
      { label: 'Pitchfan', glyph: 'Y', available: false },
    ],
  },
  {
    id: 'gann',
    label: 'Gann',
    glyph: '#',
    items: [
      { label: 'Gann box', glyph: '#', available: false },
      { label: 'Gann square fixed', glyph: '#', available: false },
      { label: 'Gann square', glyph: '#', available: false },
      { label: 'Gann fan', glyph: '⌁', available: false },
    ],
  },
  {
    id: 'chart-patterns',
    label: 'Chart patterns',
    glyph: '⌁',
    items: [
      { label: 'XABCD pattern', glyph: '⌁', available: false },
      { label: 'Cypher pattern', glyph: '⌁', available: false },
      { label: 'Head and shoulders', glyph: '⌁', available: false },
      { label: 'ABCD pattern', glyph: '⌁', available: false },
      { label: 'Triangle pattern', glyph: '△', available: false },
      { label: 'Three drives pattern', glyph: '⌁', available: false },
    ],
  },
  {
    id: 'elliott-waves',
    label: 'Elliott waves',
    glyph: '123',
    items: [
      { label: 'Elliott impulse wave (1·2·3·4·5)', glyph: '123', available: false },
      { label: 'Elliott correction wave (A·B·C)', glyph: 'ABC', available: false },
      { label: 'Elliott triangle wave (A·B·C·D·E)', glyph: 'ABCDE', available: false },
      { label: 'Elliott double combo wave (W·X·Y)', glyph: 'WXY', available: false },
      { label: 'Elliott triple combo wave (W·X·Y·X·Z)', glyph: 'WXYZ', available: false },
    ],
  },
  {
    id: 'cycles',
    label: 'Cycles',
    glyph: '∿',
    items: [
      { label: 'Cyclic lines', glyph: '║', available: false },
      { label: 'Time cycles', glyph: '∩', available: false },
      { label: 'Sine line', glyph: '∿', available: false },
    ],
  },
  {
    id: 'forecasting',
    label: 'Forecasting',
    glyph: '⌁',
    items: [
      { label: 'Long position', glyph: '↗', available: false },
      { label: 'Short position', glyph: '↘', available: false },
      { label: 'Position forecast', glyph: '▥', available: false },
      { label: 'Bars pattern', glyph: '▥', available: false },
      { label: 'Ghost feed', glyph: '◌', available: false },
      { label: 'Sector', glyph: '⌒', available: false },
    ],
  },
  {
    id: 'volume-based',
    label: 'Volume-based',
    glyph: '▥',
    items: [
      { label: 'Anchored VWAP', glyph: '▥', available: false },
      { label: 'Fixed range volume profile', glyph: '▥', available: false },
      { label: 'Anchored volume profile', glyph: '▥', available: false },
    ],
  },
  {
    id: 'measurers',
    label: 'Measurers',
    glyph: '⌗',
    items: [
      { label: 'Price range', glyph: '↕', tool: 'measurement' },
      { label: 'Date range', glyph: '↔', available: false },
      { label: 'Date and price range', glyph: '□', available: false },
    ],
  },
  {
    id: 'brushes',
    label: 'Brushes',
    glyph: '⌁',
    items: [
      { label: 'Brush', glyph: '⌁', available: false },
      { label: 'Highlighter', glyph: '╱', available: false },
    ],
  },
  {
    id: 'arrows',
    label: 'Arrows',
    glyph: '➚',
    items: [
      { label: 'Arrow marker', glyph: '➚', available: false },
      { label: 'Arrow', glyph: '➚', tool: 'arrow' },
      { label: 'Arrow mark up', glyph: '△', available: false },
      { label: 'Arrow mark down', glyph: '▽', available: false },
      { label: 'Arrow mark left', glyph: '◁', available: false },
      { label: 'Arrow mark right', glyph: '▷', available: false },
    ],
  },
  {
    id: 'shapes',
    label: 'Shapes',
    glyph: '□',
    items: [
      { label: 'Rectangle', glyph: '□', tool: 'rectangle', shortcut: 'Alt + Shift + R' },
      { label: 'Rotated rectangle', glyph: '◇', available: false },
      { label: 'Path', glyph: '⌁', available: false },
      { label: 'Circle', glyph: '○', tool: 'circle' },
      { label: 'Ellipse', glyph: '⬭', tool: 'ellipse' },
      { label: 'Polyline', glyph: '⌁', available: false },
      { label: 'Triangle', glyph: '△', available: false },
      { label: 'Arc', glyph: '⌒', available: false },
      { label: 'Curve', glyph: '⌁', available: false },
      { label: 'Double curve', glyph: '⌁', available: false },
    ],
  },
  {
    id: 'text-and-notes',
    label: 'Text and notes',
    glyph: 'T',
    items: [
      { label: 'Text', glyph: 'T', tool: 'text' },
      { label: 'Note', glyph: '▣', available: false },
      { label: 'Anchored note', glyph: '▤', available: false },
      { label: 'Price note', glyph: '$', available: false },
      { label: 'Pin', glyph: '⌖', available: false },
      { label: 'Table', glyph: '▦', available: false },
      { label: 'Callout', glyph: '▱', available: false },
      { label: 'Comment', glyph: '▭', available: false },
      { label: 'Price label', glyph: '▱', available: false },
      { label: 'Signpost', glyph: '☆', available: false },
      { label: 'Flag mark', glyph: '⚑', available: false },
    ],
  },
  {
    id: 'content',
    label: 'Content',
    glyph: '▧',
    items: [
      { label: 'Image', glyph: '▧', available: false },
      { label: 'Post', glyph: 'X', available: false },
      { label: 'Idea', glyph: '♧', available: false },
    ],
  },
  {
    id: 'emojis',
    label: 'Emojis',
    glyph: '☺',
    items: [
      { label: 'Emoji picker', glyph: '☺', available: false },
      { label: 'Stickers', glyph: '★', available: false },
      { label: 'Icons', glyph: '◆', available: false },
    ],
  },
  {
    id: 'eraser',
    label: 'Erase',
    glyph: '⌫',
    items: [
      { label: 'Eraser', glyph: '⌫', tool: 'eraser' },
      { label: 'Remove all drawings', glyph: '⌧', available: false },
    ],
  },
];

const registeredToolsByLabel = new Map<string, DrawingTool>(DRAWING_TOOL_DEFINITIONS.map((definition) => [definition.label, definition.id]));

/** The tool an item selects: its own, or the registered drawing tool with its label. */
export function drawingToolItemTool(item: DrawingToolItem): DrawingTool | undefined {
  return item.tool ?? registeredToolsByLabel.get(item.label);
}

function itemIsAvailable(item: DrawingToolItem): boolean {
  return drawingToolItemTool(item) !== undefined;
}

const favoritesStorageKey = 'omnix.trading.drawing-tool-favorites';

/** The starred tools that can be selected, in toolbar order, for the favourites toolbar (TVP-3.8). */
function favoriteTools(favorites: ReadonlySet<string>): FavoriteTool[] {
  return drawingToolGroups.flatMap((group) => group.items.flatMap((item) => {
    const tool = drawingToolItemTool(item);
    const id = `${group.id}:${item.label}`;
    return tool && favorites.has(id) ? [{ id, label: item.label, glyph: item.glyph, tool }] : [];
  }));
}

export function TradingDrawingTools({
  selectedTool,
  onSelect,
}: {
  selectedTool: DrawingTool;
  onSelect: (tool: DrawingTool) => void;
}) {
  const rootRef = useRef<HTMLElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ top: 8, left: 52 });
  const [favorites, setFavorites] = useState<Set<string>>(() => {
    if (typeof window === 'undefined') return new Set<string>();
    try {
      const stored = JSON.parse(window.localStorage.getItem(favoritesStorageKey) ?? '[]') as unknown;
      return new Set(Array.isArray(stored) ? stored.filter((value): value is string => typeof value === 'string') : []);
    } catch {
      return new Set<string>();
    }
  });

  useEffect(() => {
    if (!openGroup) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpenGroup(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenGroup(null);
    };
    window.addEventListener('pointerdown', closeOnOutsidePointer);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('pointerdown', closeOnOutsidePointer);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [openGroup]);

  useEffect(() => {
    try {
      window.localStorage.setItem(favoritesStorageKey, JSON.stringify([...favorites]));
    } catch {
      // Favorites remain usable for the current session when storage is unavailable.
    }
  }, [favorites]);

  useLayoutEffect(() => {
    if (!openGroup || !menuRef.current) return;
    const bounds = menuRef.current.getBoundingClientRect();
    const margin = 8;
    const top = Math.max(margin, Math.min(menuPosition.top, window.innerHeight - bounds.height - margin));
    const left = Math.max(margin, Math.min(menuPosition.left, window.innerWidth - bounds.width - margin));
    if (top !== menuPosition.top || left !== menuPosition.left) setMenuPosition({ top, left });
  }, [openGroup]);

  const toggleFavorite = (favoriteId: string) => {
    setFavorites((current) => {
      const next = new Set(current);
      if (next.has(favoriteId)) next.delete(favoriteId);
      else next.add(favoriteId);
      return next;
    });
  };

  return (
    <aside ref={rootRef} className="trading-tools trading-drawing-tools" aria-label="Chart drawing tools">
      {drawingToolGroups.filter((group) => group.id !== 'measurers').map((group) => {
        const isCursorGroup = group.id === 'cursor';
        const selectedItem = group.items.find((item) => drawingToolItemTool(item) === selectedTool);
        const active = selectedTool === 'cursor' && isCursorGroup ? true : selectedItem !== undefined;
        const expanded = openGroup === group.id;
        const activeLabel = selectedItem?.label ?? group.label;
        const activeGlyph = selectedItem?.glyph ?? group.glyph;
        return (
          <div key={group.id} className="trading-drawing-tool-group">
            {isCursorGroup ? (
              <div className="trading-drawing-tool-cursor-control">
                <button
                  type="button"
                  className={active ? 'active' : undefined}
                  aria-label={activeLabel}
                  aria-expanded={expanded}
                  aria-haspopup="menu"
                  title={`${group.label}: ${activeLabel}`}
                  onClick={(event) => {
                    if (openGroup === group.id) {
                      setOpenGroup(null);
                      return;
                    }
                    const bounds = event.currentTarget.getBoundingClientRect();
                    setMenuPosition({ top: bounds.top, left: bounds.right + 7 });
                    setOpenGroup(group.id);
                  }}
                >
                  <span aria-hidden="true">{activeGlyph}</span>
                </button>
                <button
                  type="button"
                  className="trading-drawing-tool-cursor-menu-toggle"
                  aria-label="More cursor tools"
                  aria-expanded={expanded}
                  aria-haspopup="menu"
                  title="More cursor tools"
                  onClick={(event) => {
                    if (openGroup === group.id) {
                      setOpenGroup(null);
                      return;
                    }
                    const bounds = event.currentTarget.getBoundingClientRect();
                    setMenuPosition({ top: bounds.top, left: bounds.right + 7 });
                    setOpenGroup(group.id);
                  }}
                >
                  <span aria-hidden="true">⌄</span>
                </button>
              </div>
            ) : (
              <button
                type="button"
                className={active ? 'active' : undefined}
                aria-label={activeLabel}
                aria-expanded={expanded}
                aria-haspopup="menu"
                title={`${group.label}: ${activeLabel}`}
                onClick={(event) => {
                  if (openGroup === group.id) {
                    setOpenGroup(null);
                    return;
                  }
                  const bounds = event.currentTarget.getBoundingClientRect();
                  setMenuPosition({ top: bounds.top, left: bounds.right + 7 });
                  setOpenGroup(group.id);
                }}
              >
                <span aria-hidden="true">{activeGlyph}</span>
              </button>
            )}
            {expanded ? (
              <div
                ref={menuRef}
                className="trading-drawing-tool-menu"
                role="menu"
                aria-label={group.label}
                style={{ top: menuPosition.top, left: menuPosition.left }}
              >
                <header>{group.label}</header>
                {group.items.map((item) => {
                  const available = itemIsAvailable(item);
                  const favoriteId = `${group.id}:${item.label}`;
                  const favorite = favorites.has(favoriteId);
                  return (
                    <div key={item.label} className={`trading-drawing-tool-row${drawingToolItemTool(item) === selectedTool ? ' selected' : ''}`}>
                      <button
                        type="button"
                        role="menuitem"
                        aria-label={item.label}
                        className="trading-drawing-tool-item"
                        disabled={!available}
                        title={available ? item.label : `${item.label} is not available yet`}
                        onClick={() => {
                          const tool = drawingToolItemTool(item);
                          if (!tool) return;
                          onSelect(tool);
                          setOpenGroup(null);
                        }}
                      >
                        <span className="trading-drawing-tool-glyph" aria-hidden="true">{item.glyph}</span>
                        <span className="trading-drawing-tool-label">{item.label}</span>
                        {item.shortcut ? <span className="trading-drawing-tool-shortcut">{item.shortcut}</span> : null}
                        {!available ? <span className="trading-drawing-tool-soon">Soon</span> : null}
                      </button>
                      <button
                        type="button"
                        className="trading-drawing-tool-favorite"
                        aria-label={`${favorite ? 'Remove' : 'Add'} ${item.label} ${favorite ? 'from' : 'to'} favorites`}
                        aria-pressed={favorite}
                        title={favorite ? 'Remove from favorites' : 'Add to favorites'}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          toggleFavorite(favoriteId);
                        }}
                      >
                        {favorite ? '★' : '☆'}
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
      <TradingDrawingToolToggles />
      <TradingDrawingFavoritesBar tools={favoriteTools(favorites)} selectedTool={selectedTool} onSelect={onSelect} />
    </aside>
  );
}
