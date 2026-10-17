import { chartPalette } from '../../../chartPalette';
import { priceLine, rangeStats } from './ranges';
import { DEFAULT_DRAWING_STYLE, defineDrawingTool } from '../types';

export const measurementTool = defineDrawingTool({
  id: 'measurement',
  label: 'Price range',
  displayName: 'Measure',
  group: 'measurers',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  handleClassName: 'trading-measurement-handle',
  geometry: (context) => {
    const { points: [first, second], rawPoints, style } = context;
    const color = style.color === DEFAULT_DRAWING_STYLE.color ? chartPalette.drawingBlue : style.color;
    const left = Math.min(first.x, second.x);
    const top = Math.min(first.y, second.y);
    const width = Math.abs(second.x - first.x);
    const height = Math.abs(second.y - first.y);
    const bottom = top + height;
    const centerX = left + width / 2;
    // TradingView's price range label: change, percent and ticks.
    const label = priceLine(rangeStats(rawPoints[0], rawPoints[1], context), context.formatPrice);
    const labelWidth = Math.max(126, label.length * 7.2 + 18);
    const labelTop = Math.max(5, top - 40);
    const edge = { stroke: color, strokeWidth: 2, hit: 'none' } as const;
    return [
      { kind: 'rect', x: left, y: top, width, height, fill: color, fillOpacity: 0.14, className: 'trading-measurement-area' },
      { kind: 'segment', x1: left, y1: top, x2: left + width, y2: top, ...edge, className: 'trading-measurement-edge' },
      { kind: 'segment', x1: left, y1: bottom, x2: left + width, y2: bottom, ...edge, className: 'trading-measurement-edge' },
      { kind: 'segment', x1: centerX, y1: top, x2: centerX, y2: bottom, ...edge, strokeWidth: 2.25, className: 'trading-measurement-axis' },
      {
        kind: 'polyline',
        points: [{ x: centerX - 7, y: top + 8 }, { x: centerX, y: top }, { x: centerX + 7, y: top + 8 }],
        ...edge,
        className: 'trading-measurement-arrow',
      },
      {
        kind: 'rect',
        x: centerX - labelWidth / 2,
        y: labelTop,
        width: labelWidth,
        height: 30,
        radius: 5,
        fill: '#ffffff',
        stroke: 'rgba(92, 99, 106, 0.28)',
        strokeWidth: 1,
        className: 'trading-measurement-label-box',
      },
      {
        kind: 'text',
        x: centerX,
        y: labelTop + 19,
        text: label,
        align: 'middle',
        fontSize: 13,
        fontWeight: 500,
        fill: '#202124',
        halo: false,
        className: 'trading-measurement-label-text',
      },
    ];
  },
});
