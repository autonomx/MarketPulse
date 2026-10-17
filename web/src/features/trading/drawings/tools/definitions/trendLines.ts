import { lineAlertLevel } from '../alertLevels';
import { constrainTo45Degrees, extendedSegment, lineStroke, rayEnd } from '../shapes';
import { booleanProperty } from '../properties';
import { defineDrawingTool, type DrawingAlertLevel } from '../types';

export const trendLineTool = defineDrawingTool({
  id: 'trend-line',
  label: 'Trend line',
  displayName: 'Trendline',
  group: 'lines',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: { extendLeft: false, extendRight: false },
  propertySchema: [
    { key: 'extendLeft', label: 'Extend left', type: 'boolean' },
    { key: 'extendRight', label: 'Extend right', type: 'boolean' },
  ],
  geometry: (context) => {
    const [first, second] = context.points;
    const [start, end] = extendedSegment(
      first,
      second,
      context.viewport,
      booleanProperty(context.properties, 'extendLeft', false),
      booleanProperty(context.properties, 'extendRight', false),
    );
    return [{ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...lineStroke(context) }];
  },
  alertLevels: ([first, second], properties) => {
    if (!first || !second) return [];
    const left = booleanProperty(properties, 'extendLeft', false);
    const right = booleanProperty(properties, 'extendRight', false);
    const level = lineAlertLevel('line', 'Trend line', first, second, left && right ? 'both' : left ? 'left' : right ? 'right' : 'none');
    return level ? [level] : [];
  },
});

export const rayTool = defineDrawingTool({
  id: 'ray',
  label: 'Ray',
  group: 'lines',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    const end = rayEnd(first, second, context.viewport);
    return [{ kind: 'segment', x1: first.x, y1: first.y, x2: end.x, y2: end.y, ...lineStroke(context) }];
  },
  alertLevels: ([first, second]): DrawingAlertLevel[] => {
    if (!first || !second) return [];
    // A ray starts at its first anchor and runs through the second, never back.
    const rightward = Date.parse(second.time) > Date.parse(first.time);
    const level = lineAlertLevel('ray', 'Ray', first, second, rightward ? 'right' : 'left');
    return level ? [level] : [];
  },
});
