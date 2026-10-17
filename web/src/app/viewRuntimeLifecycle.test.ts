/**
 * WP-9.1 acceptance: leaving a workspace removes what its runtime installed.
 * Navigating Chat -> Trading -> Chat three times leaves the same number of
 * live listeners, observers, intervals and fetch middlewares after each cycle,
 * and disposing Chat returns them to what was there before it was activated.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { activeFetchMiddlewares, resetFetchPipelineForTests } from '../api/fetchPipeline';
import { activateViewRuntime, activeViewRuntimes } from './viewRuntime';
import { createTestQueryClient } from '../test/renderWithProviders';

type ListenerKey = string;

const liveListeners = new Map<ListenerKey, number>();
const listenerIds = new WeakMap<object, number>();
const observing = new Set<MutationObserver>();
const intervals = new Set<number>();
let nextListenerId = 1;

const originalAdd = EventTarget.prototype.addEventListener;
const originalRemove = EventTarget.prototype.removeEventListener;
// jsdom's window has its own EventTarget methods.
const originalWindowAdd = window.addEventListener;
const originalWindowRemove = window.removeEventListener;
const originalSetInterval = window.setInterval;
const originalClearInterval = window.clearInterval;
const OriginalMutationObserver = window.MutationObserver;

function targetName(target: EventTarget): string {
  if (target === window) return 'window';
  if (target === document) return 'document';
  if (target === document.body) return 'body';
  return '';
}

function key(target: EventTarget, type: string, listener: unknown, options?: boolean | EventListenerOptions): ListenerKey | null {
  const name = targetName(target);
  if (!name || !listener || (typeof listener !== 'function' && typeof listener !== 'object')) return null;
  let id = listenerIds.get(listener as object);
  if (id === undefined) {
    id = nextListenerId++;
    listenerIds.set(listener as object, id);
  }
  const capture = typeof options === 'boolean' ? options : Boolean(options?.capture);
  return `${name}:${type}:${id}:${capture}`;
}

function counts() {
  return {
    listeners: [...liveListeners.values()].filter((count) => count > 0).length,
    observers: observing.size,
    intervals: intervals.size,
    fetchMiddlewares: activeFetchMiddlewares().length,
  };
}

function tracked(add: typeof originalAdd, remove: typeof originalRemove) {
  return {
    add(this: EventTarget, type: string, listener: EventListenerOrEventListenerObject | null, options?: boolean | AddEventListenerOptions) {
      const listenerKey = key(this, type, listener, options);
      if (listenerKey && !(typeof options === 'object' && options?.once)) liveListeners.set(listenerKey, 1);
      return add.call(this, type, listener, options);
    },
    remove(this: EventTarget, type: string, listener: EventListenerOrEventListenerObject | null, options?: boolean | EventListenerOptions) {
      const listenerKey = key(this, type, listener, options);
      if (listenerKey) liveListeners.delete(listenerKey);
      return remove.call(this, type, listener, options);
    },
  };
}

beforeAll(() => {
  const shared = tracked(originalAdd, originalRemove);
  EventTarget.prototype.addEventListener = shared.add;
  EventTarget.prototype.removeEventListener = shared.remove;
  const own = tracked(originalWindowAdd, originalWindowRemove);
  window.addEventListener = own.add as typeof window.addEventListener;
  window.removeEventListener = own.remove as typeof window.removeEventListener;
  window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
    const id = originalSetInterval(handler, timeout, ...args);
    intervals.add(id);
    return id;
  }) as typeof window.setInterval;
  window.clearInterval = ((id?: number) => {
    if (id !== undefined) intervals.delete(id);
    originalClearInterval(id);
  }) as typeof window.clearInterval;
  window.MutationObserver = class TrackedMutationObserver extends OriginalMutationObserver {
    observe(target: Node, options?: MutationObserverInit): void {
      observing.add(this);
      super.observe(target, options);
    }

    disconnect(): void {
      observing.delete(this);
      super.disconnect();
    }
  };
  // No backend: every request answers 404 quickly.
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404, headers: { 'content-type': 'application/json' } })));
  document.body.replaceChildren(document.createElement('main'));
});

afterAll(() => {
  EventTarget.prototype.addEventListener = originalAdd;
  EventTarget.prototype.removeEventListener = originalRemove;
  window.addEventListener = originalWindowAdd;
  window.removeEventListener = originalWindowRemove;
  window.setInterval = originalSetInterval;
  window.clearInterval = originalClearInterval;
  window.MutationObserver = OriginalMutationObserver;
  resetFetchPipelineForTests();
  vi.unstubAllGlobals();
});

describe('view runtime lifecycle', () => {
  it('leaves nothing behind when Chat is left, cycle after cycle', async () => {
    const queryClient = createTestQueryClient();
    const afterEachCycle: Array<ReturnType<typeof counts>> = [];
    let beforeChat: ReturnType<typeof counts> | null = null;
    let afterChatDisposed: ReturnType<typeof counts> | null = null;

    for (let cycle = 0; cycle < 3; cycle += 1) {
      if (cycle > 0) beforeChat = counts();
      const chat = activateViewRuntime('chatbot', { queryClient });
      await chat.ready;
      // The whole activation ran: the first and the last steps' middleware are in place.
      expect(activeFetchMiddlewares()).toEqual(expect.arrayContaining(['chat-response-metrics', 'research-release']));
      if (cycle === 0) {
        const active = counts();
        expect(active.listeners).toBeGreaterThan(40);
        // Chat renders its UI with React; its runtime watches no DOM (WP-9.4).
        expect(active.observers).toBe(0);
      }
      chat.dispose();
      if (cycle > 0) afterChatDisposed = counts();
      const trading = activateViewRuntime('trading', { queryClient });
      await trading.ready;
      trading.dispose();
      afterEachCycle.push(counts());
    }

    expect(activeViewRuntimes()).toEqual([]);
    // Module imports happen in the first cycle; from then on Chat's runtime adds and removes the same things.
    expect(afterChatDisposed).toEqual(beforeChat);
    expect(afterEachCycle[1]).toEqual(afterEachCycle[0]);
    expect(afterEachCycle[2]).toEqual(afterEachCycle[0]);
    expect(afterEachCycle[2].fetchMiddlewares).toBe(0);
  }, 60_000);
});
