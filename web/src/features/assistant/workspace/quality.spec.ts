import { describe, expect, it } from 'vitest';
import { getWorkspaceQualityStatus, summarizeWorkspaceQuality } from './quality';

describe('workspace quality contracts', () => {
  it('summarizes polish and QA signals', () => {
    const summary = summarizeWorkspaceQuality([
      { id: 'a11y', label: 'Accessibility labels', passed: true, severity: 'info' },
      { id: 'contrast', label: 'Contrast review', passed: false, severity: 'warning' },
    ]);

    expect(summary).toEqual({ total: 2, passed: 1, failed: 1, hasBlockingIssues: false });
    expect(getWorkspaceQualityStatus(summary)).toBe('review');
  });

  it('blocks release when an error signal fails', () => {
    const summary = summarizeWorkspaceQuality([
      { id: 'typecheck', label: 'Typecheck', passed: false, severity: 'error' },
    ]);

    expect(getWorkspaceQualityStatus(summary)).toBe('blocked');
  });
});
