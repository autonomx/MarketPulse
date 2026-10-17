import { describe, expect, it } from 'vitest';
import { isProcessComplete } from './process-steps';

describe('process step contracts', () => {
  it('returns true when all stages are done', () => {
    expect(isProcessComplete([
      { id: '1', stage: 'input', completed: true },
      { id: '2', stage: 'context', completed: true },
      { id: '3', stage: 'run', completed: true },
      { id: '4', stage: 'output', completed: true },
    ])).toBe(true);
  });
});
