import { vi } from 'vitest';

/**
 * Evaluates an audio worklet module with the AudioWorkletGlobalScope names stubbed
 * and returns the processor class it registers. The stubs stay in place because
 * processors read `sampleRate` when constructed; call `vi.unstubAllGlobals()` after each test.
 */
export async function loadWorkletProcessor<Processor>(
  load: () => Promise<unknown>,
  AudioWorkletProcessor: abstract new () => unknown,
  sampleRate = 24_000,
): Promise<Processor> {
  let registered: Processor | null = null;
  vi.stubGlobal('AudioWorkletProcessor', AudioWorkletProcessor);
  vi.stubGlobal('sampleRate', sampleRate);
  vi.stubGlobal('registerProcessor', (_name: string, processor: Processor) => {
    registered = processor;
  });
  vi.resetModules();
  await load();
  if (!registered) throw new Error('The audio worklet module did not register a processor.');
  return registered;
}
