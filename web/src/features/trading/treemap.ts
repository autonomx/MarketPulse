/**
 * A squarified treemap (Bruls, Huizing and van Wijk): rectangles whose areas follow the items' sizes, as square as
 * the layout allows. Used by the heatmaps (TVP-9.3) for groups and for the tiles inside each group.
 */
export type Rect = { x: number; y: number; width: number; height: number };

function worst(row: readonly number[], side: number): number {
  const total = row.reduce((sum, value) => sum + value, 0);
  const largest = Math.max(...row);
  const smallest = Math.min(...row);
  return Math.max((side * side * largest) / (total * total), (total * total) / (side * side * smallest));
}

/** Rectangles for `sizes` (any order; returned in the same order) filling `area`. Sizes of 0 or less get none. */
export function squarify(sizes: readonly number[], area: Rect): Array<Rect | null> {
  const result: Array<Rect | null> = sizes.map(() => null);
  const order = sizes.map((size, index) => ({ size, index })).filter((item) => item.size > 0).sort((left, right) => right.size - left.size);
  const total = order.reduce((sum, item) => sum + item.size, 0);
  if (total <= 0 || area.width <= 0 || area.height <= 0) return result;
  const scale = (area.width * area.height) / total;
  const items = order.map((item) => ({ index: item.index, area: item.size * scale }));
  let rect = { ...area };
  let row: typeof items = [];
  const layout = (current: typeof items, bounds: Rect): Rect => {
    const rowArea = current.reduce((sum, item) => sum + item.area, 0);
    const horizontal = bounds.width >= bounds.height;
    const side = horizontal ? bounds.height : bounds.width;
    const thickness = rowArea / side;
    let offset = 0;
    for (const item of current) {
      const length = item.area / thickness;
      result[item.index] = horizontal
        ? { x: bounds.x, y: bounds.y + offset, width: thickness, height: length }
        : { x: bounds.x + offset, y: bounds.y, width: length, height: thickness };
      offset += length;
    }
    return horizontal
      ? { x: bounds.x + thickness, y: bounds.y, width: bounds.width - thickness, height: bounds.height }
      : { x: bounds.x, y: bounds.y + thickness, width: bounds.width, height: bounds.height - thickness };
  };
  for (const item of items) {
    const side = Math.min(rect.width, rect.height);
    const areas = row.map((entry) => entry.area);
    if (row.length === 0 || worst([...areas, item.area], side) <= worst(areas, side)) {
      row.push(item);
    } else {
      rect = layout(row, rect);
      row = [item];
    }
  }
  if (row.length) layout(row, rect);
  return result;
}

/** A change's colour: deeper red or green up to ±3%, grey when flat. */
export function changeColor(change: number): string {
  const strength = Math.min(1, Math.abs(change) / 3);
  if (Math.abs(change) < 0.05) return '#5d606b';
  const [r, g, b] = change > 0 ? [8, 153, 129] : [242, 54, 69];
  const mix = (channel: number) => Math.round(66 + (channel - 66) * (0.35 + 0.65 * strength));
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}
