/**
 * Heatmaps (TVP-9.3): the day's moves of the most traded US stocks, grouped by sector, or of crypto pairs; each tile
 * sized by market cap (stocks), dollar or quote volume, and coloured by its change. A click shows the symbol on the
 * active chart.
 */
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import { changeColor, squarify, type Rect } from './treemap';
import './TradingHeatmap.css';

type Heatmap = components['schemas']['Heatmap'];
type Tile = components['schemas']['HeatmapTile'];
type Market = 'stocks' | 'crypto';
type SizeBy = 'market_cap' | 'dollar_volume';

const WIDTH = 1000;
const HEIGHT = 560;

function loadHeatmap(market: Market, sizeBy: SizeBy): Promise<Heatmap> {
  return market === 'stocks'
    ? unwrapLabelled(api.GET('/api/trading/heatmaps/stocks', { params: { query: { size_by: sizeBy, limit: 300 } } }), 'Heatmap')
    : unwrapLabelled(api.GET('/api/trading/heatmaps/crypto', { params: { query: { limit: 100 } } }), 'Heatmap');
}

type Laid = { tile: Tile; rect: Rect };
type Group = { name: string; rect: Rect; tiles: Laid[]; change: number };

/** Groups (sectors) laid out by their total size, then each group's tiles inside it under a header strip. */
function layout(tiles: readonly Tile[]): Group[] {
  const byGroup = new Map<string, Tile[]>();
  for (const tile of tiles) byGroup.set(tile.group, [...(byGroup.get(tile.group) ?? []), tile]);
  const groups = [...byGroup.entries()];
  const rects = squarify(groups.map(([, items]) => items.reduce((sum, tile) => sum + tile.size, 0)), { x: 0, y: 0, width: WIDTH, height: HEIGHT });
  return groups.flatMap(([name, items], index) => {
    const rect = rects[index];
    if (!rect) return [];
    const header = groups.length > 1 && rect.height > 30 ? 14 : 0;
    const inner = { x: rect.x + 1, y: rect.y + header + 1, width: Math.max(0, rect.width - 2), height: Math.max(0, rect.height - header - 2) };
    const tileRects = squarify(items.map((tile) => tile.size), inner);
    const weight = items.reduce((sum, tile) => sum + tile.size, 0) || 1;
    return [{
      name, rect,
      tiles: items.flatMap((tile, position) => (tileRects[position] ? [{ tile, rect: tileRects[position]! }] : [])),
      change: items.reduce((sum, tile) => sum + tile.change_percent * tile.size, 0) / weight,
    }];
  });
}

export function TradingHeatmap({ onShowInstrument }: { onShowInstrument?: (instrumentId: string) => void }) {
  const [market, setMarket] = useState<Market>('stocks');
  const [sizeBy, setSizeBy] = useState<SizeBy>('market_cap');
  const query = useQuery({ queryKey: ['trading', 'heatmap', market, sizeBy], queryFn: () => loadHeatmap(market, sizeBy), staleTime: 60_000, refetchInterval: 5 * 60_000 });
  const groups = useMemo(() => layout(query.data?.tiles ?? []), [query.data]);
  return (
    <section className="trading-heatmap" aria-label="Heatmap">
      <header>
        <nav role="tablist" aria-label="Heatmap market">
          {(['stocks', 'crypto'] as const).map((item) => (
            <button key={item} type="button" role="tab" aria-selected={market === item} onClick={() => setMarket(item)}>{item === 'stocks' ? 'US stocks' : 'Crypto'}</button>
          ))}
        </nav>
        {market === 'stocks' ? (
          <label>Size by<select aria-label="Size tiles by" value={sizeBy} onChange={(event) => setSizeBy(event.target.value as SizeBy)}>
            <option value="market_cap">Market cap</option><option value="dollar_volume">Dollar volume</option>
          </select></label>
        ) : <span>Sized by 24-hour volume</span>}
        {query.data?.unclassified ? <span className="trading-heatmap-note">{query.data.unclassified} stock{query.data.unclassified === 1 ? ' is' : 's are'} still being classified by sector.</span> : null}
      </header>
      {query.isLoading ? <p role="status">Loading the heatmap…</p> : null}
      {query.isError ? <p role="alert">The heatmap's market data could not load.</p> : null}
      {query.data ? (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${market === 'stocks' ? 'US stock' : 'Crypto'} heatmap, ${query.data.tiles.length} symbols`} className="trading-heatmap-map">
          {groups.map((group) => (
            <g key={group.name}>
              <rect x={group.rect.x} y={group.rect.y} width={group.rect.width} height={group.rect.height} className="trading-heatmap-group" />
              {group.rect.height > 30 && groups.length > 1 ? (
                <text x={group.rect.x + 4} y={group.rect.y + 11} className="trading-heatmap-group-label">{group.name} {group.change >= 0 ? '+' : ''}{group.change.toFixed(2)}%</text>
              ) : null}
              {group.tiles.map(({ tile, rect }) => {
                const big = rect.width > 34 && rect.height > 22;
                return (
                  <g key={tile.instrument_id} className="trading-heatmap-tile" role="button" tabIndex={0}
                    aria-label={`${tile.symbol} ${tile.change_percent >= 0 ? '+' : ''}${tile.change_percent.toFixed(2)}%`}
                    onClick={() => onShowInstrument?.(tile.instrument_id)}
                    onKeyDown={(event) => { if (event.key === 'Enter') onShowInstrument?.(tile.instrument_id); }}>
                    <rect x={rect.x} y={rect.y} width={Math.max(0, rect.width - 1)} height={Math.max(0, rect.height - 1)} fill={changeColor(tile.change_percent)}>
                      <title>{`${tile.symbol}${tile.name ? ` · ${tile.name}` : ''}\n${tile.change_percent >= 0 ? '+' : ''}${tile.change_percent.toFixed(2)}% · ${tile.price.toLocaleString()}${tile.industry ? `\n${tile.industry}` : ''}`}</title>
                    </rect>
                    {big ? (
                      <text x={rect.x + rect.width / 2} y={rect.y + rect.height / 2} textAnchor="middle" fontSize={Math.min(16, Math.max(8, Math.min(rect.width / 5, rect.height / 3)))}>
                        <tspan x={rect.x + rect.width / 2} dy="-0.2em">{tile.symbol}</tspan>
                        <tspan x={rect.x + rect.width / 2} dy="1.1em">{tile.change_percent >= 0 ? '+' : ''}{tile.change_percent.toFixed(2)}%</tspan>
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
      ) : null}
    </section>
  );
}
