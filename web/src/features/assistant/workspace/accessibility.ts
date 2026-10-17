export type WorkspaceBreakpoint = 'mobile' | 'tablet' | 'desktop';

export type WorkspaceAccessibilityProfile = {
  breakpoint: WorkspaceBreakpoint;
  keyboardNavigation: boolean;
  screenReaderLabels: boolean;
  reducedMotion: boolean;
  highContrast: boolean;
};

export function createWorkspaceAccessibilityProfile(
  profile: WorkspaceAccessibilityProfile,
): WorkspaceAccessibilityProfile {
  return { ...profile };
}

export function getResponsivePanelCount(breakpoint: WorkspaceBreakpoint): number {
  if (breakpoint === 'mobile') {
    return 1;
  }

  if (breakpoint === 'tablet') {
    return 2;
  }

  return 3;
}

export function isWorkspaceAccessible(profile: WorkspaceAccessibilityProfile): boolean {
  return profile.keyboardNavigation && profile.screenReaderLabels;
}

export function shouldUseAccessibleMotion(profile: WorkspaceAccessibilityProfile): boolean {
  return !profile.reducedMotion;
}
