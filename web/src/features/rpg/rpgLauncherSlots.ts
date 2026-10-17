import type { Dispatch, SetStateAction } from 'react';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

/**
 * Where the campaign launcher lets the workspace header render (WP-9.4): the
 * launcher's dialog and its home grid, registered by callback refs while they
 * are mounted, and requests to open the launcher at its home view.
 */
export interface RpgLauncherSlots {
  dialog: HTMLElement | null;
  homeGrid: HTMLElement | null;
  /** Increments on every request to open the launcher at home. */
  openRequests: number;
}

const INITIAL: RpgLauncherSlots = { dialog: null, homeGrid: null, openRequests: 0 };
let slots = INITIAL;
const listeners = new Set<() => void>();

function publish(next: Partial<RpgLauncherSlots>): void {
  const merged = { ...slots, ...next };
  if (merged.dialog === slots.dialog && merged.homeGrid === slots.homeGrid && merged.openRequests === slots.openRequests) {
    return;
  }
  slots = merged;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): RpgLauncherSlots {
  return slots;
}

export function setRpgLauncherDialog(element: HTMLElement | null): void {
  publish({ dialog: element });
}

export function setRpgLauncherHomeGrid(element: HTMLElement | null): void {
  publish({ homeGrid: element });
}

export function requestRpgLauncherHome(): void {
  publish({ openRequests: slots.openRequests + 1 });
}

export function useRpgLauncherSlots(): RpgLauncherSlots {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** A launcher's view state; it moves to ``home`` on each request made after the launcher mounted. */
export function useRpgLauncherView<View extends string>(initial: View, home: View): [View, Dispatch<SetStateAction<View>>] {
  const [view, setView] = useState<View>(initial);
  const { openRequests } = useRpgLauncherSlots();
  const handled = useRef(openRequests);
  useEffect(() => {
    if (openRequests === handled.current) return;
    handled.current = openRequests;
    setView(home);
  }, [home, openRequests]);
  return [view, setView];
}

export function resetRpgLauncherSlotsForTests(): void {
  slots = INITIAL;
  listeners.forEach((listener) => listener());
}
