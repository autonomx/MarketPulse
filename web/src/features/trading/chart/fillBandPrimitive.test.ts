import { describe, expect, it, vi } from 'vitest';
import type { Time } from 'lightweight-charts';
import { FillBandPrimitive } from './fillBandPrimitive';

function attach(primitive: FillBandPrimitive) {
  const timeScale = {
    timeToCoordinate: (time: Time) => Number(time) * 10,
    timeToIndex: (time: Time) => Number(time),
  };
  const requestUpdate = vi.fn();
  primitive.attached({
    chart: { timeScale: () => timeScale }, series: { priceToCoordinate: (price: number) => 100 - price }, requestUpdate,
  } as never);
  return requestUpdate;
}

function paint(primitive: FillBandPrimitive) {
  const context = { beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), fill: vi.fn(), fillStyle: '' };
  const fills: string[] = [];
  context.fill.mockImplementation(() => fills.push(context.fillStyle));
  const renderer = primitive.paneViews()[0].renderer()!;
  renderer.drawBackground!({ useMediaCoordinateSpace: (draw: (scope: { context: typeof context }) => void) => draw({ context }) } as never);
  return { context, fills };
}

const point = (time: number, upper: number, lower: number, color = 'blue') => ({ time: time as Time, upper, lower, color });

describe('fill band primitive (TVP-11.1)', () => {
  it('paints a quad between each pair of bars with both values, in the later bar colour, and breaks at gaps', () => {
    const primitive = new FillBandPrimitive();
    const requestUpdate = attach(primitive);
    primitive.setPoints([point(1, 10, 5), point(2, 12, 6, 'green'), point(3, Number.NaN, Number.NaN), point(4, 9, 8), point(5, 11, 7, 'red')]);
    expect(requestUpdate).toHaveBeenCalled();
    const { context, fills } = paint(primitive);
    // Bars 1-2 and 4-5: bar 3 is a gap, so nothing joins 2 to 4.
    expect(fills).toEqual(['green', 'red']);
    expect(context.moveTo.mock.calls).toEqual([[10, 90], [40, 91]]);
    expect(context.lineTo.mock.calls.slice(0, 3)).toEqual([[20, 88], [20, 94], [10, 95]]);
  });

  it('widens the price scale to the band over the bars in view', () => {
    const primitive = new FillBandPrimitive();
    attach(primitive);
    primitive.setPoints([point(1, 50, -50), point(2, 12, 6), point(3, Number.NaN, Number.NaN), point(4, 9, 3)]);
    expect(primitive.autoscaleInfo(2 as never, 4 as never)).toEqual({ priceRange: { minValue: 3, maxValue: 12 } });
    expect(primitive.autoscaleInfo(3 as never, 3 as never)).toBeNull();
    primitive.detached();
    expect(primitive.autoscaleInfo(1 as never, 4 as never)).toBeNull();
  });
});
