import { describe, expect, it } from 'vitest';
import { PRODUCT_NAME, PRODUCT_NAME_WITH_ARTICLE } from './productIdentity';

describe('product identity', () => {
  it('defaults to Omnix when the build sets no product name', () => {
    expect(PRODUCT_NAME).toBe('Omnix');
    expect(PRODUCT_NAME_WITH_ARTICLE).toBe('an Omnix');
  });
});
