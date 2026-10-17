import { horizontalAlertLevel } from '../alertLevels';
import { lineStroke } from '../shapes';
import { defineDrawingTool } from '../types';

const HORIZONTAL_ICON = 'M3 12h18';

export const horizontalLineTool = defineDrawingTool({
  id: 'horizontal-line',
  label: 'Horizontal line',
  icon: HORIZONTAL_ICON,
  group: 'lines',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [point] = context.points;
    return [{ kind: 'segment', x1: 0, y1: point.y, x2: context.viewport.width, y2: point.y, ...lineStroke(context) }];
  },
  alertLevels: ([point], _properties, services) => (point ? [horizontalAlertLevel('line', 'Horizontal line', point, 'both', services)].filter((level) => level !== null) : []),
});

export const horizontalRayTool = defineDrawingTool({
  id: 'horizontal-ray',
  label: 'Horizontal ray',
  icon: HORIZONTAL_ICON,
  group: 'lines',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [point] = context.points;
    return [{ kind: 'segment', x1: point.x, y1: point.y, x2: context.viewport.width, y2: point.y, ...lineStroke(context) }];
  },
  alertLevels: ([point], _properties, services) => (point ? [horizontalAlertLevel('ray', 'Horizontal ray', point, 'right', services)].filter((level) => level !== null) : []),
});
