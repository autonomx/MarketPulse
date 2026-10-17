// The drawing action bus (TVP-0.4): a tool's context-menu action produces a
// request (`{ type, payload }`); a feature that can act on that type (the
// order ticket for `order-ticket`, say) subscribes here. Neither the context
// menu nor the chart panel knows any action.
//
// The bus is one per page, shared by every chart: each request carries its
// source (chart and drawing), and a feature that lives per chart handles only
// its own chart's requests.
import type { DrawingActionRequest } from './tools/types';

/** Where a request came from. */
export type DrawingActionSource = { chartId: string | null; drawingId: string | null };

type DrawingActionHandler = (payload: unknown, source: DrawingActionSource) => void;

const handlers = new Map<string, Set<DrawingActionHandler>>();

const UNKNOWN_SOURCE: DrawingActionSource = { chartId: null, drawingId: null };

/** Handles requests of one type until the returned function is called (calling it again does nothing). */
export function onDrawingActionRequest(type: string, handler: DrawingActionHandler): () => void {
  const set = handlers.get(type) ?? new Set<DrawingActionHandler>();
  set.add(handler);
  handlers.set(type, set);
  return () => {
    set.delete(handler);
    // A later subscriber may have created a new set for this type; only an empty set of our own is dropped.
    if (set.size === 0 && handlers.get(type) === set) handlers.delete(type);
  };
}

/** Whether any feature currently handles requests of this type (the menu disables actions nobody handles). */
export function hasDrawingActionHandler(type: string): boolean {
  return (handlers.get(type)?.size ?? 0) > 0;
}

/** Hands a request to its handlers; false when none is listening. A failing handler doesn't stop the others. */
export function dispatchDrawingActionRequest(request: DrawingActionRequest, source: DrawingActionSource = UNKNOWN_SOURCE): boolean {
  const set = handlers.get(request.type);
  if (!set || set.size === 0) return false;
  for (const handler of [...set]) {
    try {
      handler(request.payload, source);
    } catch (error) {
      console.error(`Drawing action "${request.type}" failed`, error);
    }
  }
  return true;
}
