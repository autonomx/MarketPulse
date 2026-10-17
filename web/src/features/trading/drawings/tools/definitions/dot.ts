import { chartPalette } from '../../../chartPalette';
import { defineDrawingTool } from '../types';

export const dotTool = defineDrawingTool({
  id: 'dot',
  label: 'Dot',
  group: 'cursor',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: ({ points: [point], style, selected }) => [{
    kind: 'marker',
    x: point.x,
    y: point.y,
    radius: selected ? 5 : 4,
    fill: style.color,
    stroke: selected ? chartPalette.yellow : style.color,
    strokeWidth: style.lineWidth,
    className: `drawing-dot${selected ? ' selected' : ''}`,
  }],
});
