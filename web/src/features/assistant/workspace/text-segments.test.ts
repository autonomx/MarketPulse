import { describe, expect, it } from 'vitest';
import { createTextSegment, getCompleteText, replaceDraftTextSegment } from './text-segments';

describe('text segment contracts', () => {
  it('joins complete text', () => {
    expect(getCompleteText([
      { id: 'a', text: ' hello ', kind: 'complete', createdAt: 't1' },
      { id: 'b', text: 'there', kind: 'draft', createdAt: 't2' },
    ])).toBe('hello');
  });

  it('replaces draft text segments', () => {
    const draft = createTextSegment({ id: 'd2', text: 'next', kind: 'draft', createdAt: 't2' });
    expect(replaceDraftTextSegment([{ id: 'd1', text: 'old', kind: 'draft', createdAt: 't1' }], draft)).toEqual([draft]);
  });
});
