export {};

declare global {
  interface Window {
    setTimeout(
      handler: TimerHandler,
      timeout?: number,
      ...arguments_: unknown[]
    ): number;
    clearTimeout(id: number | undefined): void;
  }
}
