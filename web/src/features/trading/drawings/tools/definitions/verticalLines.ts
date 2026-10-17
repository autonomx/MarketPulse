import { lineStroke } from '../shapes';
import { defineDrawingTool } from '../types';

export const verticalLineTool = defineDrawingTool({
  id: 'vertical-line',
  label: 'Vertical line',
  icon: 'M12 3v18',
  group: 'lines',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [point] = context.points;
    return [{ kind: 'segment', x1: point.x, y1: 0, x2: point.x, y2: context.viewport.height, ...lineStroke(context) }];
  },
});

export const crosslineTool = defineDrawingTool({
  id: 'crossline',
  label: 'Crossline',
  displayName: 'Cross line',
  group: 'lines',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [point] = context.points;
    const stroke = lineStroke(context);
    return [
      { kind: 'segment', x1: 0, y1: point.y, x2: context.viewport.width, y2: point.y, ...stroke },
      { kind: 'segment', x1: point.x, y1: 0, x2: point.x, y2: context.viewport.height, ...stroke },
    ];
  },
});
