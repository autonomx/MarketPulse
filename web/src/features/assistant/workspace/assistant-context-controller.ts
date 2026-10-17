import { assistantContextStore, loadProfileResearchDefault, stopDesktopShare } from './assistant-context-store';
import type { DesktopTemporalCapture } from './desktop-temporal-capture';

export {
  desktopStatusLabel,
  localWorkspaceSummary,
  normalizeDeepResearchPageLimit,
  normalizeLocalWorkspaceSelection,
  normalizeResearchMode,
  readStoredAgentMode,
  webResearchModeLabel,
} from './assistant-context-store';
export type { LocalWorkspaceSelection } from './assistant-context-store';

export type DesktopCompanionCaptureSnapshot = {
  sessionId: string | null;
  characterId: string | null;
  sourceFingerprint: string;
  capture: DesktopTemporalCapture;
};

let disposeController: (() => void) | null = null;

/**
 * Activates Chat's context tools: loads the profile's research default and
 * stops desktop sharing when the page or Chat goes away. Chat sends its
 * messages through `sendChatWithAssistantContext` (WP-9.5); no request is
 * rewritten here.
 */
export function initializeAssistantContextController(): () => void {
  if (disposeController) return () => undefined;
  void loadProfileResearchDefault();
  const handleUnload = () => stopDesktopShare();
  window.addEventListener('beforeunload', handleUnload, { once: true });
  const dispose = () => {
    window.removeEventListener('beforeunload', handleUnload);
    stopDesktopShare();
    if (disposeController === dispose) disposeController = null;
  };
  disposeController = dispose;
  return dispose;
}

export function currentDesktopCompanionCapture(): DesktopCompanionCaptureSnapshot | null {
  const { desktopShare, activeSessionId } = assistantContextStore.getState();
  if (!desktopShare) return null;
  return {
    sessionId: activeSessionId,
    characterId: null,
    sourceFingerprint: desktopShare.sourceFingerprint,
    capture: desktopShare.capture,
  };
}
