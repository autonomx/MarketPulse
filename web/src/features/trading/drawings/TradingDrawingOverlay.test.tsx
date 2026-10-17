import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import type { DrawingTool, TradingDrawing } from './drawingCommands';
import { TradingDrawingOverlay, type TradingDrawingOverlayProps } from './TradingDrawingOverlay';
import { pointAt, testBarSeries, testProjector } from '../../../test/drawingTools';
import { type DrawingGeometryContext, type DrawingToolDefinition } from './tools/types';
import type { DrawingCanvasPrimitive } from './DrawingCanvasPrimitive';

// Test-only tools for the gestures no shipped tool uses yet (TVP-3 adds the real ones).
const testTools = vi.hoisted(() => {
  const polyline = (context: DrawingGeometryContext) => [{ kind: 'polyline' as const, points: [...context.points], stroke: '#fff', strokeWidth: 2 }];
  return {
    polyline,
    brushGeometry: vi.fn(polyline),
  };
});

vi.mock('./tools/registry', async (importOriginal) => {
  const original = await importOriginal<typeof import('./tools/registry')>();
  const { constrainTo45Degrees } = await import('./tools/shapes');
  const base = { group: 'shapes', defaultProperties: {}, propertySchema: [] } as const;
  const extra: DrawingToolDefinition[] = [
    { ...base, id: 'pitchfork', label: 'Pitchfork', creation: { gesture: 'click-click', anchors: 3 }, draftPreview: 'shapes', previewAnchors: 2, geometry: testTools.polyline },
    { ...base, id: 'path', label: 'Path', creation: { gesture: 'click-click' }, constrain: constrainTo45Degrees, geometry: testTools.polyline },
    { ...base, id: 'brush', label: 'Brush', creation: { gesture: 'freehand', simplifyTolerance: 0 }, handles: 'ends', geometry: testTools.brushGeometry },
    { ...base, id: 'note', label: 'Anchored note', creation: { gesture: 'click' }, anchoring: 'screen', geometry: (context) => [{ kind: 'marker', x: context.points[0].x, y: context.points[0].y, radius: 3, fill: '#fff' }] },
    // A position-like tool: one click, a default stop from onCreate, a stop handle that edits a property, an order action.
    {
      ...base,
      id: 'position',
      label: 'Long position',
      creation: { gesture: 'click' },
      defaultProperties: { stopDistance: 0 },
      onCreate: (anchors, services) => ({ points: [...anchors], properties: { stopDistance: services.instrument.tickSize === null ? 5 : services.instrument.tickSize * 100 } }),
      geometry: (context) => {
        const [entry] = context.points;
        const stop = context.project({ time: context.rawPoints[0].time, price: context.rawPoints[0].price - Number(context.properties.stopDistance) })!;
        return [{ kind: 'segment', x1: entry.x, y1: entry.y, x2: entry.x + 50, y2: entry.y, stroke: '#0f0' }, { kind: 'segment', x1: entry.x, y1: stop.y, x2: entry.x + 50, y2: stop.y, stroke: '#f00' }];
      },
      handles: (context) => {
        const [entry] = context.points;
        const stopY = context.project({ time: context.rawPoints[0].time, price: context.rawPoints[0].price - Number(context.properties.stopDistance) })!.y;
        return [
          { id: 'stop', x: entry.x + 25, y: stopY, drag: ({ points, point }) => ({ properties: { stopDistance: points[0].price - point.price } }) },
        ];
      },
      contextActions: [{ id: 'ticket', label: 'Order ticket', request: (drawing) => ({ type: 'order-ticket', payload: { entry: drawing.points[0].price, stop: drawing.points[0].price - Number(drawing.properties.stopDistance) } }) }],
    },
  ];
  const byId = new Map(extra.map((definition) => [definition.id, definition]));
  return {
    ...original,
    drawingToolDefinition: (id: string) => byId.get(id) ?? original.drawingToolDefinition(id),
    isDrawingToolId: (id: string) => byId.has(id) || original.isDrawingToolId(id),
  };
});

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  // jsdom has no PointerEvent; without it fireEvent drops clientX/clientY.
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    readonly pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  });
  Object.assign(Element.prototype, { setPointerCapture: () => undefined });
  vi.spyOn(SVGSVGElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, toJSON: () => ({}),
  });
});

afterEach(cleanup);

function fakeAdapter() {
  let offset = 0;
  const listeners = new Set<() => void>();
  const primitives: DrawingCanvasPrimitive[] = [];
  const adapter = {
    attachPriceSeriesPrimitive: (primitive: DrawingCanvasPrimitive) => {
      primitives.push(primitive);
      return () => primitives.splice(primitives.indexOf(primitive), 1);
    },
    projectDrawingPoint: (point: { time: string; price: number }) => {
      const projected = testProjector(point);
      return projected ? { x: projected.x + offset, y: projected.y } : null;
    },
    // Bars are 10px apart: times snap to them unless the caller asks for the exact time.
    drawingPointFromCoordinate: (x: number, y: number, options: { exactTime?: boolean } = {}) => (
      pointAt(options.exactTime ? x - offset : Math.round((x - offset) / 10) * 10, y)
    ),
    drawingBars: () => testBarSeries(),
    drawingTimeAfterBars: (time: string, count: number) => new Date(Date.parse(time) + count * 60_000).toISOString(),
    drawingBarIndexForTime: (time: string) => (Date.parse(time) - Date.parse(pointAt(0, 0).time)) / 60_000,
    drawingTimeForBarIndex: (index: number) => pointAt(index, 0).time,
    drawingVisibleBars: () => null,
    drawingBarIndexMatchesBars: () => true,
    formatDrawingPrice: (price: number) => price.toFixed(2),
    onViewportChange: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    onCrosshair: () => () => undefined,
    indicatorPaneIdAtClientY: () => null,
    indicatorValueFromClientY: () => null,
    zoomAtCoordinate: vi.fn(),
  };
  const pan = (dx: number) => act(() => {
    offset += dx;
    listeners.forEach((listener) => listener());
  });
  return { adapter: adapter as unknown as TradingChartAdapter, pan, primitives };
}

function drawing(toolType: TradingDrawing['toolType'], pixels: [number, number][], extra: Partial<TradingDrawing> = {}): TradingDrawing {
  return {
    drawingId: `${toolType}-1`,
    instrumentId: 'fixture',
    toolType,
    points: pixels.map(([x, y]) => pointAt(x, y)),
    selected: false,
    revision: 1,
    ...extra,
  };
}

function renderOverlay(props: Partial<TradingDrawingOverlayProps> & { tool?: DrawingTool } = {}) {
  const { adapter, pan, primitives } = fakeAdapter();
  const handlers = {
    onAdd: vi.fn(), onSelect: vi.fn(), onMovePoint: vi.fn(), onTranslateDrawing: vi.fn(), onRemove: vi.fn(),
    onToolComplete: vi.fn(), onAlertAtPoint: vi.fn(), onContextMenu: vi.fn(),
  };
  const view = render(
    <div>
      <TradingDrawingOverlay
        adapter={adapter}
        instrumentId="fixture"
        interval="1m"
        tool="cursor"
        snapMode="none"
        drawings={[]}
        selectedId={null}
        // These tests read the SVG host's elements; the canvas host (the default) has its own test below.
        renderer="svg"
        {...handlers}
        {...props}
      />
    </div>,
  );
  const svg = view.container.querySelector('svg')!;
  return { ...view, svg, pan, handlers, primitives };
}

describe('TradingDrawingOverlay', () => {
  it('renders each drawing from its tool geometry with selection and lock state', () => {
    const { svg } = renderOverlay({
      drawings: [
        drawing('trend-line', [[100, 300], [200, 200]]),
        drawing('rectangle', [[100, 100], [200, 150]], { drawingId: 'box', locked: true }),
        drawing('measurement', [[300, 300], [400, 200]], { drawingId: 'measure' }),
        drawing('text', [[50, 50]], { drawingId: 'hidden', hidden: true }),
      ],
      selectedId: 'measure',
    });
    const trend = svg.querySelector('g[data-drawing-id="trend-line-1"]')!;
    expect(trend.getAttribute('data-selected')).toBe('false');
    expect(trend.querySelector('line')).toHaveAttribute('x1', '100');
    expect(trend.querySelector('line')).toHaveAttribute('y2', '200');
    expect(svg.querySelector('g[data-drawing-id="box"]')).toHaveAttribute('data-locked', 'true');
    const measure = svg.querySelector('g[data-drawing-id="measure"]')!;
    expect(measure).toHaveAttribute('data-selected', 'true');
    expect(measure.querySelector('.trading-measurement-label-text')?.textContent).toBe('100.00 (14.29%)');
    expect([...measure.querySelectorAll('[data-drawing-point-index]')].map((handle) => handle.getAttribute('class'))).toEqual([
      'trading-measurement-handle', 'trading-measurement-handle',
    ]);
    expect(svg.querySelector('g[data-drawing-id="hidden"]')).toBeNull();
  });

  it('follows the chart viewport by patching the mounted elements', () => {
    const { svg, pan } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [200, 200]])], selectedId: 'trend-line-1' });
    const line = svg.querySelector('line')!;
    pan(50);
    expect(svg.querySelector('line')).toBe(line);
    expect(line).toHaveAttribute('x1', '150');
    expect(line).toHaveAttribute('x2', '250');
    expect(svg.querySelector('[data-drawing-point-index="1"]')).toHaveAttribute('cx', '250');
  });

  it('creates a one-click drawing with time/price anchors only', () => {
    const { svg, handlers } = renderOverlay({ tool: 'text' });
    fireEvent.pointerDown(svg, { clientX: 120, clientY: 80, pointerId: 1 });
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({
      toolType: 'text', text: 'Market note', points: [pointAt(120, 80)], properties: {},
    }));
    expect(handlers.onToolComplete).toHaveBeenCalled();
  });

  it('creates a drag drawing and previews it while dragging', () => {
    const { svg, handlers } = renderOverlay({ tool: 'fibonacci' });
    fireEvent.pointerDown(svg, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(svg, { clientX: 200, clientY: 300, pointerId: 1 });
    expect(svg.querySelector('line.draft')).toHaveAttribute('x2', '200');
    fireEvent.pointerUp(svg, { clientX: 200, clientY: 300, pointerId: 1 });
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({
      toolType: 'fibonacci',
      points: [pointAt(100, 100), pointAt(200, 300)],
      properties: expect.objectContaining({ levels: expect.arrayContaining([{ value: 0.618, color: '', visible: true }]) }),
    }));
    expect(svg.querySelector('line.draft')).toBeNull();
  });

  it('previews a measurement with its own shapes', () => {
    const { svg } = renderOverlay({ tool: 'measurement' });
    fireEvent.pointerDown(svg, { clientX: 100, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(svg, { clientX: 220, clientY: 200, pointerId: 1 });
    expect(svg.querySelector('[data-drawing-draft] .trading-measurement-label-text')?.textContent).toBe('100.00 (14.29%)');
  });

  it('passes trend line anchors to the context menu, and a rectangle its edges (TVP-1.4)', () => {
    const trend = drawing('trend-line', [[100, 300], [200, 200]]);
    const { svg, handlers } = renderOverlay({ drawings: [trend, drawing('rectangle', [[300, 100], [400, 200]], { drawingId: 'box' })] });
    fireEvent.contextMenu(svg.querySelector('g[data-drawing-id="trend-line-1"] line')!, { clientX: 150, clientY: 250 });
    expect(handlers.onContextMenu).toHaveBeenLastCalledWith(expect.objectContaining({
      source: 'context-menu', drawingId: 'trend-line-1', drawingTool: 'trend-line', trendlinePoints: trend.points,
      drawingAlertLevels: [expect.objectContaining({ anchors: trend.points, interpolation: 'bars' })],
    }), undefined);
    fireEvent.contextMenu(svg.querySelector('g[data-drawing-id="box"] rect')!, { clientX: 350, clientY: 150 });
    expect(handlers.onContextMenu).toHaveBeenLastCalledWith(expect.objectContaining({
      drawingId: 'box',
      drawingAlertLevels: [expect.objectContaining({ key: 'top', extend: 'right' }), expect.objectContaining({ key: 'bottom', extend: 'right' })],
    }), undefined);
  });

  it('erases a drawing with the eraser and selects one with the cursor', () => {
    const erasing = renderOverlay({ tool: 'eraser', drawings: [drawing('dot', [[100, 100]])] });
    fireEvent.pointerDown(erasing.svg.querySelector('circle')!, { clientX: 100, clientY: 100 });
    expect(erasing.handlers.onRemove).toHaveBeenCalledWith('dot-1');
    cleanup();
    const selecting = renderOverlay({ drawings: [drawing('dot', [[100, 100]])] });
    fireEvent.pointerDown(selecting.svg.querySelector('circle')!, { clientX: 100, clientY: 100 });
    expect(selecting.handlers.onSelect).toHaveBeenCalledWith('dot-1');
  });

  it('with the canvas renderer, paints through a chart primitive and hit-tests presses', () => {
    const trend = drawing('trend-line', [[100, 300], [200, 200]]);
    const { svg, handlers, primitives } = renderOverlay({ renderer: 'canvas', drawings: [trend, drawing('dot', [[400, 100]], { drawingId: 'dot' })] });
    expect(svg.querySelector('g[data-drawing-id]')).toBeNull();
    expect(primitives).toHaveLength(1);
    const painted: string[] = [];
    const context = new Proxy({}, { get: (_, key: string) => (...args: unknown[]) => painted.push(`${key}(${args.join(',')})`), set: () => true });
    primitives[0].paneViews()[0].renderer()!.draw({ useMediaCoordinateSpace: (paint: (scope: unknown) => void) => paint({ context, mediaSize: { width: 800, height: 600 } }) } as never);
    expect(painted).toContain('moveTo(100,300)');
    expect(painted).toContain('arc(400,100,4,0,6.283185307179586)');

    fireEvent.pointerMove(window, { clientX: 150, clientY: 250, buttons: 0 });
    expect(svg.style.pointerEvents).toBe('auto');
    expect(svg.dataset.drawingId).toBe('trend-line-1');
    fireEvent.pointerDown(svg, { clientX: 150, clientY: 250, pointerId: 1 });
    expect(handlers.onSelect).toHaveBeenCalledWith('trend-line-1');
    fireEvent.pointerMove(window, { clientX: 600, clientY: 500, buttons: 0 });
    expect(svg.style.pointerEvents).toBe('');
    expect(svg.dataset.drawingId).toBeUndefined();
  });

  it('places click-click anchors, previews the tool from two anchors and completes on the last', () => {
    const { svg, handlers } = renderOverlay({ tool: 'pitchfork' as DrawingTool });
    click(svg, 100, 100);
    fireEvent.pointerMove(svg, { clientX: 150, clientY: 150 });
    expect(svg.querySelector('[data-drawing-draft] polyline')).toHaveAttribute('points', '100,100 150,150');
    click(svg, 200, 200);
    expect(handlers.onAdd).not.toHaveBeenCalled();
    click(svg, 300, 100);
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({ points: [pointAt(100, 100), pointAt(200, 200), pointAt(300, 100)] }));
  });

  it('places creation anchors on top of existing drawings instead of selecting them', () => {
    const existing = drawing('horizontal-line', [[0, 200]], { drawingId: 'line' });
    const { svg, handlers } = renderOverlay({ tool: 'pitchfork' as DrawingTool, drawings: [existing], selectedId: 'line' });
    click(svg, 100, 100);
    click(svg.querySelector('g[data-drawing-id="line"] line')!, 200, 200);
    click(svg.querySelector('g[data-drawing-id="line"] [data-drawing-point-index]')!, 0, 200);
    expect(handlers.onSelect).not.toHaveBeenCalled();
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({ points: [pointAt(100, 100), pointAt(200, 200), pointAt(0, 200)] }));
  });

  it('keeps the creation preview on the chart while it pans between clicks', () => {
    const { svg, pan } = renderOverlay({ tool: 'trend-line' });
    fireEvent.pointerDown(svg, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(svg, { clientX: 200, clientY: 200 });
    const line = svg.querySelector('line.draft')!;
    expect(line).toHaveAttribute('x2', '200');
    pan(50);
    expect(svg.querySelector('line.draft')).toBe(line);
    expect(line).toHaveAttribute('x1', '150');
    expect(line).toHaveAttribute('x2', '250');
  });
});

describe('TradingDrawingOverlay', () => {
  it('completes an open-ended path on double click and constrains with Shift', () => {
    const { svg, handlers } = renderOverlay({ tool: 'path' as DrawingTool });
    click(svg, 100, 100);
    fireEvent.pointerMove(svg, { clientX: 200, clientY: 110, shiftKey: true });
    expect(svg.querySelector('line.draft')).toHaveAttribute('y2', '100');
    click(svg, 200, 200);
    click(svg, 300, 100);
    click(svg, 300, 100);
    fireEvent.doubleClick(svg, { clientX: 300, clientY: 100 });
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({ points: [pointAt(100, 100), pointAt(200, 200), pointAt(300, 100)] }));
  });

  it('records freehand strokes at exact times without re-rendering the chart drawings per move', () => {
    const existing = drawing('brush' as TradingDrawing['toolType'], [[10, 10], [20, 20]], { drawingId: 'stroke' });
    const { svg, handlers } = renderOverlay({ tool: 'brush' as DrawingTool, drawings: [existing] });
    fireEvent.pointerDown(svg, { clientX: 103, clientY: 100, pointerId: 1 });
    testTools.brushGeometry.mockClear();
    for (let step = 1; step <= 30; step += 1) fireEvent.pointerMove(svg, { clientX: 103 + step * 3, clientY: 100 + (step % 2) * 5 });
    expect(svg.querySelector('[data-drawing-draft] polyline')?.getAttribute('points')?.split(' ')).toHaveLength(31);
    // Moves patch only the preview; React re-renders (and reruns the existing stroke's
    // geometry) only when the preview's structure changes: outline segment, then polyline.
    expect(testTools.brushGeometry.mock.calls.length).toBeLessThanOrEqual(2);
    fireEvent.pointerUp(svg, { clientX: 193, clientY: 100, pointerId: 1 });
    const added = handlers.onAdd.mock.calls[0][0] as TradingDrawing;
    expect(added.points).toHaveLength(31);
    // Freehand keeps times between bars (bars are 10px apart in this fixture).
    expect(added.points[0]).toEqual(pointAt(103, 100));
  });

  it('keeps screen-anchored drawings in place while the chart pans', () => {
    const { svg, handlers, pan } = renderOverlay({ tool: 'note' as DrawingTool });
    click(svg, 400, 300);
    const note = handlers.onAdd.mock.calls[0][0] as TradingDrawing;
    expect(note.points[0].screen).toEqual({ x: 0.5, y: 0.5 });
    cleanup();
    const view = renderOverlay({ drawings: [note] });
    view.pan(80);
    expect(view.svg.querySelector('circle')).toHaveAttribute('cx', '400');
    void pan;
  });

  it('lets a tool define handles that edit properties, defaults via onCreate and actions with requests', () => {
    const created = renderOverlay({ tool: 'position' as DrawingTool, instrument: { tickSize: 0.01, pointValue: 1 } });
    click(created.svg, 100, 200);
    const position = created.handlers.onAdd.mock.calls[0][0] as TradingDrawing;
    expect(position.properties).toEqual({ stopDistance: 1 });
    cleanup();

    const onEditDrawing = vi.fn();
    const view = renderOverlay({ drawings: [{ ...position, drawingId: 'p' }], selectedId: 'p', onEditDrawing });
    const handle = view.svg.querySelector('[data-handle-id="stop"]')!;
    expect(handle).toHaveAttribute('cy', '201');
    // A click without moving edits nothing, so it adds no undo step.
    fireEvent.pointerDown(handle, { clientX: 125, clientY: 201, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 125, clientY: 201 });
    expect(onEditDrawing).not.toHaveBeenCalled();
    fireEvent.pointerDown(handle, { clientX: 125, clientY: 201, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 125, clientY: 210 });
    // The preview applies the handle's property edit before it is committed.
    expect(view.svg.querySelectorAll('g[data-drawing-id="p"] line')[1]).toHaveAttribute('y1', '210');
    fireEvent.pointerUp(window, { clientX: 125, clientY: 210 });
    expect(onEditDrawing).toHaveBeenCalledWith('p', { properties: { stopDistance: 10 } });

    fireEvent.contextMenu(view.svg.querySelector('g[data-drawing-id="p"] line')!, { clientX: 110, clientY: 200 });
    expect(view.handlers.onContextMenu).toHaveBeenLastCalledWith(expect.objectContaining({
      drawingActions: [{ id: 'ticket', label: 'Order ticket', request: { type: 'order-ticket', payload: { entry: 800, stop: 799 } } }],
    }), undefined);
  });

  it('offers no line alert for a level that only extends back in time (TVP-1.4)', () => {
    // Drawn right to left, this ray extends to the left: the server's line would alert where nothing is drawn.
    const ray = drawing('ray', [[200, 300], [100, 200]], { drawingId: 'ray' });
    const { svg, handlers } = renderOverlay({ drawings: [ray] });
    fireEvent.contextMenu(svg.querySelector('g[data-drawing-id="ray"] line')!, { clientX: 150, clientY: 250 });
    expect(handlers.onContextMenu).toHaveBeenLastCalledWith(expect.objectContaining({
      trendlinePoints: undefined,
      drawingAlertLevels: undefined,
    }), undefined);
  });

  it('offers a ray extending forward (TVP-1.4)', () => {
    const ray = drawing('ray', [[100, 200], [200, 300]], { drawingId: 'ray' });
    const { svg, handlers } = renderOverlay({ drawings: [ray] });
    fireEvent.contextMenu(svg.querySelector('g[data-drawing-id="ray"] line')!, { clientX: 150, clientY: 250 });
    expect(handlers.onContextMenu).toHaveBeenLastCalledWith(expect.objectContaining({
      drawingId: 'ray',
      drawingAlertLevels: [expect.objectContaining({ extend: 'right' })],
    }), undefined);
  });

  it('constrains with Shift against where the placed anchor is after a pan', () => {
    const { svg, handlers, pan } = renderOverlay({ tool: 'path' as DrawingTool });
    click(svg, 100, 100);
    pan(30);
    // The first anchor is now at (130, 100), so (230, 200) is exactly 45 degrees from it and stays put.
    // Against the anchor's old position (100, 100) Shift would have moved it to about (215, 215).
    fireEvent.pointerMove(svg, { clientX: 230, clientY: 200, shiftKey: true });
    const line = svg.querySelector('line.draft')!;
    expect(Number(line.getAttribute('x2'))).toBeCloseTo(230, 6);
    expect(Number(line.getAttribute('y2'))).toBeCloseTo(200, 6);
    void handlers;
  });
});
function click(element: Element, x: number, y: number) {
  fireEvent.pointerDown(element, { clientX: x, clientY: y, pointerId: 1 });
  fireEvent.pointerUp(element, { clientX: x, clientY: y, pointerId: 1 });
}

describe('selection gestures (TVP-2.2)', () => {
  const lineOf = (svg: SVGSVGElement, id: string) => svg.querySelector(`g[data-drawing-id="${id}"] line`)!;

  it('Ctrl+click adds to the selection without selecting alone', () => {
    const onToggleSelect = vi.fn();
    const { svg, handlers } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [300, 100]])], onToggleSelect });
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, ctrlKey: true, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 200, clientY: 200, ctrlKey: true });
    expect(onToggleSelect).toHaveBeenCalledWith('trend-line-1');
    expect(handlers.onSelect).not.toHaveBeenCalled();
  });

  it('Ctrl+drag shows a ghost copy and clones on release, leaving the original', () => {
    const onCloneDrawings = vi.fn();
    const { svg, handlers } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [300, 100]])], onCloneDrawings, onToggleSelect: vi.fn() });
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, ctrlKey: true, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 250, clientY: 200, ctrlKey: true });
    expect(svg.querySelector('g[data-drawing-id="trend-line-1#clone"]')).not.toBeNull();
    expect(lineOf(svg, 'trend-line-1')).toHaveAttribute('x1', '100');
    fireEvent.pointerUp(window, { clientX: 250, clientY: 200, ctrlKey: true });
    expect(onCloneDrawings).toHaveBeenCalledWith(['trend-line-1'], pointAt(200, 200), pointAt(250, 200));
    expect(handlers.onTranslateDrawing).not.toHaveBeenCalled();
    expect(svg.querySelector('g[data-drawing-id="trend-line-1#clone"]')).toBeNull();
  });

  it('a drag of a selected drawing moves the selection; a click on it selects it alone', () => {
    const first = { ...drawing('trend-line', [[100, 300], [300, 100]]), selected: true };
    const second = { ...drawing('trend-line', [[100, 500], [300, 400]], { drawingId: 'second' }), selected: true };
    const { svg, handlers } = renderOverlay({ drawings: [first, second], selectedId: 'second' });
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 230, clientY: 200 });
    // Both selected drawings preview the move.
    expect(lineOf(svg, 'second')).toHaveAttribute('x1', '130');
    fireEvent.pointerUp(window, { clientX: 230, clientY: 200 });
    expect(handlers.onTranslateDrawing).toHaveBeenCalledWith('trend-line-1', pointAt(200, 200), pointAt(230, 200));
    expect(handlers.onSelect).not.toHaveBeenCalled();
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 200, clientY: 200 });
    expect(handlers.onSelect).toHaveBeenCalledWith('trend-line-1');
  });

  it('a press that moves less than the drag threshold is still a click', () => {
    const onToggleSelect = vi.fn();
    const onCloneDrawings = vi.fn();
    const { svg } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [300, 100]])], onToggleSelect, onCloneDrawings });
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, ctrlKey: true, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 202, clientY: 201, ctrlKey: true });
    expect(svg.querySelector('g[data-drawing-id="trend-line-1#clone"]')).toBeNull();
    fireEvent.pointerUp(window, { clientX: 202, clientY: 201, ctrlKey: true });
    expect(onToggleSelect).toHaveBeenCalledWith('trend-line-1');
    expect(onCloneDrawings).not.toHaveBeenCalled();
  });

  it('a locked drawing in the selection stays put in the drag preview', () => {
    const first = { ...drawing('trend-line', [[100, 300], [300, 100]]), selected: true };
    const locked = { ...drawing('trend-line', [[100, 500], [300, 400]], { drawingId: 'locked', locked: true }), selected: true };
    const { svg } = renderOverlay({ drawings: [first, locked], selectedId: 'trend-line-1' });
    fireEvent.pointerDown(lineOf(svg, 'trend-line-1'), { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 230, clientY: 200 });
    expect(lineOf(svg, 'locked')).toHaveAttribute('x1', '100');
    fireEvent.pointerUp(window, { clientX: 230, clientY: 200 });
  });

  it('hides every drawing while all drawings are hidden', () => {
    const { svg } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [300, 100]])], allHidden: true });
    expect(svg.querySelector('[data-drawing-id]')).toBeNull();
  });
});

describe('tool behaviour (TVP-3.8)', () => {
  it('shows only the drawings visible on the chart\'s interval', () => {
    const daily = { ...drawing('trend-line', [[100, 300], [300, 100]]), visibility: { minutes: { visible: false } } };
    const { svg } = renderOverlay({ drawings: [daily, drawing('trend-line', [[100, 500], [300, 400]], { drawingId: 'any' })], interval: '1m' });
    expect(svg.querySelector('g[data-drawing-id="trend-line-1"]')).toBeNull();
    expect(svg.querySelector('g[data-drawing-id="any"]')).not.toBeNull();
  });

  it('lock all keeps every drawing in place', () => {
    const { svg, handlers } = renderOverlay({ drawings: [drawing('trend-line', [[100, 300], [300, 100]])], allLocked: true });
    fireEvent.pointerDown(svg.querySelector('g[data-drawing-id="trend-line-1"] line')!, { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 240, clientY: 200 });
    fireEvent.pointerUp(window, { clientX: 240, clientY: 200 });
    expect(handlers.onTranslateDrawing).not.toHaveBeenCalled();
  });

  it('a new drawing starts with its tool\'s remembered style', () => {
    window.localStorage.setItem('omnix.trading.drawing-tool-defaults', JSON.stringify({ dot: { style: { color: '#123456', lineWidth: 4, lineStyle: 'dashed' }, properties: {} } }));
    const { svg, handlers } = renderOverlay({ tool: 'dot' });
    click(svg, 100, 200);
    expect(handlers.onAdd).toHaveBeenCalledWith(expect.objectContaining({ style: { color: '#123456', lineWidth: 4, lineStyle: 'dashed' } }));
    window.localStorage.clear();
  });
});
