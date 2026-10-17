import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_DRAWING_TOOL_SETTINGS } from './drawings/drawingToolSettings';
import { TradingDrawingFavoritesBar, TradingDrawingToolToggles } from './TradingDrawingToolToggles';
import { useTradingStore } from './tradingStore';

afterEach(() => {
  useTradingStore.setState({ drawingToolSettings: { ...DEFAULT_DRAWING_TOOL_SETTINGS }, drawingsHidden: false });
  window.localStorage.clear();
});

describe('drawing toolbar behaviour (TVP-3.8)', () => {
  it('toggles stay in drawing mode, lock all, hide all and sync, and remembers them', () => {
    render(<TradingDrawingToolToggles />);
    fireEvent.click(screen.getByRole('button', { name: 'Stay in drawing mode' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lock all drawings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Hide all drawings (Ctrl+Alt+H)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sync drawings between the charts of this tab' }));
    expect(useTradingStore.getState().drawingToolSettings).toMatchObject({ stayInDrawingMode: true, lockAll: true, syncDrawings: false });
    expect(useTradingStore.getState().drawingsHidden).toBe(true);
    expect(screen.getByRole('button', { name: 'Lock all drawings' })).toHaveAttribute('aria-pressed', 'true');
    expect(JSON.parse(window.localStorage.getItem('omnix.trading.drawing-tool-settings') ?? '{}')).toMatchObject({ lockAll: true });
  });

  it('the favourites toolbar shows starred tools and selects them', () => {
    const onSelect = vi.fn();
    const tools = [{ id: 'lines:Trend line', label: 'Trend line', glyph: '/', tool: 'trend-line' as const }];
    const { rerender } = render(<TradingDrawingFavoritesBar tools={tools} selectedTool="cursor" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Trend line' }));
    expect(onSelect).toHaveBeenCalledWith('trend-line');
    useTradingStore.setState({ drawingToolSettings: { ...DEFAULT_DRAWING_TOOL_SETTINGS, favoritesBar: false } });
    rerender(<TradingDrawingFavoritesBar tools={tools} selectedTool="cursor" onSelect={onSelect} />);
    expect(screen.queryByRole('toolbar', { name: 'Favourite drawing tools' })).toBeNull();
  });
});
