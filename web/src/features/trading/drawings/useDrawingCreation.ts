import { useEffect, useRef, useState } from 'react';
import { simplifyPolyline } from './tools/shapes';
import { anchorCount, type DrawingPoint, type DrawingToolDefinition, type ScreenPoint } from './tools/types';

export type PointerPoint = DrawingPoint & ScreenPoint;

/** Minimum pointer travel, in pixels, before a freehand stroke takes another point. */
const FREEHAND_STEP = 2;

/** The stored anchor of a pointer point: time, price and (screen-anchored tools) pane fractions. */
export function anchorOf(point: PointerPoint): DrawingPoint {
  return point.screen ? { time: point.time, price: point.price, screen: point.screen } : { time: point.time, price: point.price };
}

/**
 * Creation gestures for every tool, driven by its `creation` declaration. The
 * draft (anchors placed so far; for drag and click-click the last one follows
 * the pointer) lives in a ref: pointer moves update it in place and call
 * `moved`, so the host patches the preview without a React render. React
 * renders only when the draft starts, gains an anchor or ends.
 */
export function useDrawingCreation(definition: DrawingToolDefinition | undefined, complete: (points: DrawingPoint[]) => void, moved: () => void) {
  const draftRef = useRef<PointerPoint[] | null>(null);
  const [, setRevision] = useState(0);
  const creation = definition?.creation;
  const replace = (points: PointerPoint[] | null) => {
    draftRef.current = points;
    setRevision((value) => value + 1);
  };

  useEffect(() => {
    draftRef.current = null;
    setRevision((value) => value + 1);
  }, [definition]);

  const active = draftRef.current !== null;
  useEffect(() => {
    if (!active || creation?.gesture !== 'click-click') return;
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      draftRef.current = null;
      setRevision((value) => value + 1);
    };
    window.addEventListener('keydown', cancel);
    return () => window.removeEventListener('keydown', cancel);
  }, [active, creation]);

  const finish = (points: PointerPoint[]) => {
    replace(null);
    complete(points.map(anchorOf));
  };

  const pointerDown = (point: PointerPoint): void => {
    if (!creation) return;
    switch (creation.gesture) {
      case 'click':
        finish([point]);
        return;
      case 'drag':
        replace([point, point]);
        return;
      case 'freehand':
        replace([point]);
        return;
      case 'click-click': {
        const current = draftRef.current;
        const placed = current ? [...current.slice(0, -1), point] : [point];
        const { max } = anchorCount(creation);
        if (max !== null && placed.length >= max) finish(placed);
        else replace([...placed, point]);
      }
    }
  };

  const pointerMove = (point: PointerPoint) => {
    const current = draftRef.current;
    if (!current || !creation) return;
    if (creation.gesture === 'freehand') {
      const last = current[current.length - 1];
      if (Math.hypot(point.x - last.x, point.y - last.y) < FREEHAND_STEP) return;
      current.push(point);
    } else {
      current[current.length - 1] = point;
    }
    moved();
  };

  const pointerUp = () => {
    const current = draftRef.current;
    if (!current || !creation) return;
    if (creation.gesture === 'drag') {
      finish(current);
    } else if (creation.gesture === 'freehand') {
      const points = simplifyPolyline(current, creation.simplifyTolerance ?? 1);
      if (points.length >= anchorCount(creation).min) finish(points);
      else replace(null);
    }
  };

  /** Completes an open-ended click-click drawing. */
  const doubleClick = () => {
    const current = draftRef.current;
    if (!current || creation?.gesture !== 'click-click' || creation.anchors !== undefined) return;
    // The double click's two pointer downs placed its anchor twice, plus the floating one.
    const placed = current.slice(0, -2);
    if (placed.length >= anchorCount(creation).min) finish(placed);
  };

  return { draftRef, active, pointerDown, pointerMove, pointerUp, doubleClick };
}
