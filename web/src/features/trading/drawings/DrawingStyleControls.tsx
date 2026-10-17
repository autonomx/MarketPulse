import { DEFAULT_DRAWING_STYLE, type DrawingStyle, type TradingDrawing } from './drawingCommands';

const WIDTHS = [1, 2, 3, 4] as const;

/** Line width and dash of the selected drawing, beside its colour (TradingView's floating toolbar). */
export function DrawingStyleControls({ drawing, onChange }: { drawing: TradingDrawing; onChange: (style: DrawingStyle) => void }) {
  const style = drawing.style ?? DEFAULT_DRAWING_STYLE;
  return (
    <>
      <select aria-label="Drawing line width" value={style.lineWidth} onChange={(event) => onChange({ ...style, lineWidth: Number(event.target.value) })}>
        {(WIDTHS as readonly number[]).includes(style.lineWidth) ? null : <option value={style.lineWidth}>{style.lineWidth}px</option>}
        {WIDTHS.map((width) => <option key={width} value={width}>{width}px</option>)}
      </select>
      <select aria-label="Drawing line style" value={style.lineStyle} onChange={(event) => onChange({ ...style, lineStyle: event.target.value === 'dashed' ? 'dashed' : 'solid' })}>
        <option value="solid">Solid</option>
        <option value="dashed">Dashed</option>
      </select>
    </>
  );
}
