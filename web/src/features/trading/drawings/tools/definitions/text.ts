import { defineDrawingTool } from '../types';

const DEFAULT_TEXT = 'Market note';

export const textTool = defineDrawingTool({
  id: 'text',
  label: 'Text',
  displayName: 'Text note',
  editableText: true,
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  defaultText: DEFAULT_TEXT,
  geometry: ({ points: [point], style, selected, text }) => [{
    kind: 'text',
    x: point.x,
    y: point.y,
    text: text || DEFAULT_TEXT,
    fill: style.color,
    className: selected ? 'selected' : undefined,
  }],
});
