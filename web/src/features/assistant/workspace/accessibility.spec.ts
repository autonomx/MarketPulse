import { describe, expect, it } from 'vitest';
import {
  getResponsivePanelCount,
  isWorkspaceAccessible,
  shouldUseAccessibleMotion,
  type WorkspaceAccessibilityProfile,
} from './accessibility';

describe('workspace accessibility contracts', () => {
  it('maps responsive breakpoints to deterministic panel counts', () => {
    expect(getResponsivePanelCount('mobile')).toBe(1);
    expect(getResponsivePanelCount('tablet')).toBe(2);
    expect(getResponsivePanelCount('desktop')).toBe(3);
  });

  it('derives accessibility and motion affordances', () => {
    const profile: WorkspaceAccessibilityProfile = {
      breakpoint: 'desktop',
      keyboardNavigation: true,
      screenReaderLabels: true,
      reducedMotion: true,
      highContrast: false,
    };

    expect(isWorkspaceAccessible(profile)).toBe(true);
    expect(shouldUseAccessibleMotion(profile)).toBe(false);
  });
});
