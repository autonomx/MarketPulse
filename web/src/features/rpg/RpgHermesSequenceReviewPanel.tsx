/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import type { HermesSequencePreviewState } from './HermesSequencePreview';

interface RpgHermesSequenceReviewPanelProps {
  assistMode?: string;
  error?: Error | null;
  isPending?: boolean;
  onAssistModeChange?: (mode: string) => void;
  onReview: () => void;
  onUseFirstItem: (command: string) => void;
  sequence?: HermesSequencePreviewState | null;
}

/**
 * Retained as a no-op compatibility export while older workspace wiring and
 * extensions are migrated. Hermes sequence review is no longer rendered in RPG.
 */
export function RpgHermesSequenceReviewPanel(_props: RpgHermesSequenceReviewPanelProps) {
  return null;
}
