import { horizontalAlertLevel } from '../alertLevels';
import { areaFill, constrainToSquare, lineStroke } from '../shapes';
import { defineDrawingTool } from '../types';

export const rectangleTool = defineDrawingTool({
  id: 'rectangle',
  label: 'Rectangle',
  group: 'shapes',
  creation: { gesture: 'drag' },
  constrain: constrainToSquare,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    return [{
      kind: 'rect',
      x: Math.min(first.x, second.x),
      y: Math.min(first.y, second.y),
      width: Math.abs(second.x - first.x),
      height: Math.abs(second.y - first.y),
      ...lineStroke(context),
      ...areaFill(context.style.color),
    }];
  },
  // Its top and bottom as levels from its left edge onwards (TVP-1.4).
  alertLevels: ([first, second], _properties, services) => {
    if (!first || !second) return [];
    const left = Date.parse(first.time) <= Date.parse(second.time) ? first.time : second.time;
    const top = Math.max(first.price, second.price);
    const bottom = Math.min(first.price, second.price);
    return [
      horizontalAlertLevel('top', 'Top', { time: left, price: top }, 'right', services),
      horizontalAlertLevel('bottom', 'Bottom', { time: left, price: bottom }, 'right', services),
    ].filter((level) => level !== null);
  },
});

export const circleTool = defineDrawingTool({
  id: 'circle',
  label: 'Circle',
  group: 'shapes',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    const radius = Math.max(Math.abs(second.x - first.x), Math.abs(second.y - first.y)) / 2;
    return [{
      kind: 'ellipse',
      cx: (first.x + second.x) / 2,
      cy: (first.y + second.y) / 2,
      rx: radius,
      ry: radius,
      ...lineStroke(context),
      ...areaFill(context.style.color),
    }];
  },
});

export const ellipseTool = defineDrawingTool({
  id: 'ellipse',
  label: 'Ellipse',
  group: 'shapes',
  creation: { gesture: 'drag' },
  constrain: constrainToSquare,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    return [{
      kind: 'ellipse',
      cx: (first.x + second.x) / 2,
      cy: (first.y + second.y) / 2,
      rx: Math.abs(second.x - first.x) / 2,
      ry: Math.abs(second.y - first.y) / 2,
      ...lineStroke(context),
      ...areaFill(context.style.color),
    }];
  },
});
