type RpgWindowTimerHandle = ReturnType<typeof setTimeout>;

declare global {
  interface Window {
    setTimeout(handler: TimerHandler, timeout?: number, ...args: unknown[]): RpgWindowTimerHandle;
    clearTimeout(handle?: RpgWindowTimerHandle): void;
  }
}

export {};
