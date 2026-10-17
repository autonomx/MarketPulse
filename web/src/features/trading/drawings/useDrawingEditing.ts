import { useState, type PointerEvent as ReactPointerEvent } from 'react';
import type { DrawingPoint, DrawingProperties, DrawingTool, TradingDrawing } from './drawingCommands';
import { guardToolCall } from './tools/guard';
import { drawingPropertiesWithDefaults } from './tools/registry';
import type { DrawingEditPatch, DrawingHandle, DrawingModifiers, DrawingToolServices, ScreenPoint } from './tools/types';

export type HandlePreview = { drawingId: string; patch: DrawingEditPatch };
/**
 * A whole-drawing drag in progress: the drawings it moves (the selection when the pressed drawing is part of one),
 * or with `clone` the ghost copies (`cloneGhostId`) a Ctrl+drag will add, while the originals stay.
 */
export type TranslationPreview = { drawingIds: readonly string[]; from: DrawingPoint; to: DrawingPoint; clone?: boolean };

export const CLONE_GHOST_SUFFIX = '#clone';

/** The id of the ghost copy shown while `drawingId` is Ctrl+dragged. */
export function cloneGhostId(drawingId: string): string {
  return `${drawingId}${CLONE_GHOST_SUFFIX}`;
}

/** The ghost copies of a Ctrl+drag in progress, to render beside the originals. */
export function cloneGhosts(drawings: readonly TradingDrawing[], translation: TranslationPreview | null): TradingDrawing[] {
  if (!translation?.clone) return [];
  const wanted = new Set(translation.drawingIds);
  return drawings.flatMap((drawing) => (wanted.has(cloneGhostId(drawing.drawingId)) ? [{ ...drawing, drawingId: cloneGhostId(drawing.drawingId), selected: false }] : []));
}

/** A drawing's anchors and properties with an in-progress translation or handle drag applied. */
export function previewDrawing(
  drawing: TradingDrawing,
  translation: TranslationPreview | null,
  handle: HandlePreview | null,
): { points: DrawingPoint[]; properties: DrawingProperties | undefined } {
  let points = drawing.points;
  let properties = drawing.properties;
  if (translation?.drawingIds.includes(drawing.drawingId)) {
    const { from, to } = translation;
    const timeDelta = Date.parse(to.time) - Date.parse(from.time);
    const priceDelta = to.price - from.price;
    points = points.map((point) => ({
      ...point,
      time: new Date(Date.parse(point.time) + timeDelta).toISOString(),
      price: point.price + priceDelta,
      ...(point.screen && from.screen && to.screen
        ? { screen: { x: point.screen.x + to.screen.x - from.screen.x, y: point.screen.y + to.screen.y - from.screen.y } }
        : {}),
    }));
  }
  if (handle?.drawingId === drawing.drawingId) {
    if (handle.patch.points) points = [...handle.patch.points];
    if (handle.patch.properties) properties = { ...properties, ...handle.patch.properties };
  }
  return { points, properties };
}

function trackPointer(move: (event: PointerEvent) => void, up: (event: PointerEvent) => void): void {
  const release = (event: PointerEvent) => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', release);
    up(event);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', release);
}

/** Whether presses on existing drawings edit them; with any other tool they start or continue a creation. */
export function toolEditsDrawings(tool: DrawingTool): boolean {
  return tool === 'cursor' || tool === 'eraser';
}

/** Whether a handle's edit changes the drawing; a pixel of jitter that snaps back to the same anchor does not. */
export function patchChanges(drawing: TradingDrawing, patch: DrawingEditPatch): boolean {
  const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
  if (patch.points && !same(patch.points, drawing.points)) return true;
  if (!patch.properties) return false;
  const current = drawingPropertiesWithDefaults(drawing.toolType, drawing.properties);
  return Object.entries(patch.properties).some(([key, value]) => !same(value, current[key]));
}

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

/** Pointer travel (CSS pixels) below which a press is a click, not a drag: trackpad and pen jitter. */
export const DRAG_THRESHOLD = 4;

/** The modifiers of a pointer event; `ctrl` is Cmd on macOS, where Ctrl+click opens the context menu. */
export function modifiersOf(event: { shiftKey: boolean; altKey: boolean; ctrlKey: boolean; metaKey: boolean }, mac = MAC): DrawingModifiers {
  return { shift: event.shiftKey, alt: event.altKey, ctrl: mac ? event.metaKey : event.ctrlKey || event.metaKey };
}

/** The chart point under the pointer for one of a drawing's anchors (`anchorIndex`, Shift-constrained), or for the drawing as a whole. */
export type DrawingPointLocator = (
  drawing: TradingDrawing,
  clientX: number,
  clientY: number,
  modifiers: DrawingModifiers,
  anchorIndex?: number,
) => DrawingPoint | null;

/**
 * Handle drags, whole-drawing drags, selection and erasing on existing
 * drawings, for the cursor and eraser. Any other tool lets presses fall
 * through to creation, so anchors can be placed on top of drawings.
 */
export function useDrawingEditing({
  tool,
  enabled,
  pointFor,
  screenFor,
  services,
  groupOf = (id) => [id],
  onSelect,
  onToggleSelect,
  onCloneDrawings,
  onEdit,
  onTranslateDrawing,
  onRemove,
  onToolComplete,
}: {
  tool: DrawingTool;
  enabled: boolean;
  pointFor: DrawingPointLocator;
  /** The pointer in pane pixels. */
  screenFor: (clientX: number, clientY: number) => ScreenPoint | null;
  services: () => DrawingToolServices;
  /** The drawings a drag of `id` moves: the selection when `id` is part of a multi-selection. */
  groupOf?: (id: string) => readonly string[];
  onSelect: (id: string | null) => void;
  /** Ctrl+click: add to or remove from the selection (TVP-2.2). Without it Ctrl+click selects like a click. */
  onToggleSelect?: (id: string) => void;
  /** Ctrl+drag: copies of the drawings, moved. Without it Ctrl+drag moves like a drag. */
  onCloneDrawings?: (ids: readonly string[], from: DrawingPoint, to: DrawingPoint) => void;
  onEdit: (id: string, patch: DrawingEditPatch) => void;
  onTranslateDrawing: (id: string, from: DrawingPoint, to: DrawingPoint) => void;
  onRemove: (id: string) => void;
  onToolComplete?: () => void;
}) {
  const [handlePreview, setHandlePreview] = useState<HandlePreview | null>(null);
  const [translationPreview, setTranslationPreview] = useState<TranslationPreview | null>(null);
  const edits = toolEditsDrawings(tool);

  /** The edit a handle makes with the pointer at (clientX, clientY). */
  const handlePatch = (drawing: TradingDrawing, handle: DrawingHandle, pointer: PointerEvent): DrawingEditPatch | null => {
    const modifiers = modifiersOf(pointer);
    const point = pointFor(drawing, pointer.clientX, pointer.clientY, modifiers, handle.anchorIndex);
    const screen = screenFor(pointer.clientX, pointer.clientY);
    if (!point || !screen) return null;
    const input = {
      points: drawing.points,
      properties: drawingPropertiesWithDefaults(drawing.toolType, drawing.properties),
      point,
      screen,
      modifiers,
      services: services(),
    };
    return guardToolCall(drawing.toolType, 'handle drag', () => handle.drag(input), null);
  };

  const dragHandle = (drawing: TradingDrawing, handle: DrawingHandle) => (event: ReactPointerEvent<SVGElement>) => {
    if (!edits) return;
    event.preventDefault();
    event.stopPropagation();
    if (drawing.locked || !enabled) return;
    // A click on a handle without moving it edits nothing, so it adds no undo step.
    const start = { x: event.clientX, y: event.clientY };
    let moved = false;
    const movedFrom = (pointer: PointerEvent) => pointer.clientX !== start.x || pointer.clientY !== start.y;
    trackPointer((pointer) => {
      moved ||= movedFrom(pointer);
      if (!moved) return;
      const patch = handlePatch(drawing, handle, pointer);
      if (patch) setHandlePreview({ drawingId: drawing.drawingId, patch });
    }, (pointer) => {
      setHandlePreview(null);
      if (!moved && !movedFrom(pointer)) return;
      const patch = handlePatch(drawing, handle, pointer);
      if (patch && patchChanges(drawing, patch)) onEdit(drawing.drawingId, patch);
    });
  };

  /**
   * Pointer down on a drawing with the eraser or cursor: erase it, or select it and start moving it. With Ctrl (or
   * Cmd), a click adds it to the selection or removes it, and a drag clones it (the selection, when it is part of
   * one). A press on a drawing of a multi-selection moves the whole selection; a click without moving selects it alone.
   */
  const pressDrawing = (drawing: TradingDrawing, clientX: number, clientY: number, modifiers: DrawingModifiers) => {
    if (tool === 'eraser') {
      onRemove(drawing.drawingId);
      onToolComplete?.();
      return;
    }
    const cloning = modifiers.ctrl && Boolean(onCloneDrawings);
    const toggling = modifiers.ctrl && Boolean(onToggleSelect);
    const group = drawing.selected ? groupOf(drawing.drawingId) : [drawing.drawingId];
    if (!toggling && !drawing.selected) onSelect(drawing.drawingId);
    const start = enabled ? pointFor(drawing, clientX, clientY, modifiers) : null;
    let dragging = false;
    const travelled = (pointer: PointerEvent) => (dragging ||= Math.hypot(pointer.clientX - clientX, pointer.clientY - clientY) >= DRAG_THRESHOLD);
    const released = (pointer: PointerEvent) => {
      if (travelled(pointer)) return false;
      // A click: Ctrl toggles; a plain click on a multi-selection keeps only this drawing.
      if (toggling) onToggleSelect?.(drawing.drawingId);
      else if (group.length > 1) onSelect(drawing.drawingId);
      return true;
    };
    if (!start || (drawing.locked && !cloning)) {
      trackPointer(() => undefined, (pointer) => { released(pointer); });
      return;
    }
    const ids = cloning ? group.map(cloneGhostId) : group;
    trackPointer((pointer) => {
      if (!travelled(pointer)) return;
      const point = pointFor(drawing, pointer.clientX, pointer.clientY, modifiersOf(pointer));
      if (point) setTranslationPreview({ drawingIds: ids, from: start, to: point, clone: cloning });
    }, (pointer) => {
      const point = pointFor(drawing, pointer.clientX, pointer.clientY, modifiersOf(pointer));
      setTranslationPreview(null);
      if (released(pointer)) return;
      const moved = point && (point.time !== start.time || point.price !== start.price
        || point.screen?.x !== start.screen?.x || point.screen?.y !== start.screen?.y);
      if (!point || !moved) return;
      if (cloning) onCloneDrawings?.(group, start, point);
      else onTranslateDrawing(drawing.drawingId, start, point);
    });
  };

  const dragDrawing = (drawing: TradingDrawing) => (event: ReactPointerEvent<SVGElement>) => {
    if (!edits) return;
    event.preventDefault();
    event.stopPropagation();
    pressDrawing(drawing, event.clientX, event.clientY, modifiersOf(event));
  };

  return { handlePreview, translationPreview, dragHandle, dragDrawing, edits };
}
