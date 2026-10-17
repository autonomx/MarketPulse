import { arrowHead, constrainTo45Degrees, lineStroke } from '../shapes';
import { defineDrawingTool } from '../types';

export const arrowTool = defineDrawingTool({
  id: 'arrow',
  label: 'Arrow',
  group: 'arrows',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    return [
      { kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke },
      arrowHead(first, second, context.style.lineWidth, { fill: context.style.color, className: stroke.className }),
      // A wider, invisible target at the tip so the arrow is easy to pick up.
      { kind: 'marker', x: second.x, y: second.y, radius: 11, fill: 'transparent', className: 'drawing-hit-target' },
    ];
  },
});
