/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import type { HermesRpgExecutionLedgerItem } from './api/hermesRpgApprovedFlowClient';

interface RpgHermesExecutionHistoryProps {
  items?: HermesRpgExecutionLedgerItem[];
  isLoading?: boolean;
}

/**
 * Retained as a no-op compatibility export while older workspace wiring and
 * extensions are migrated. Hermes execution history is no longer rendered in RPG.
 */
export function RpgHermesExecutionHistory(_props: RpgHermesExecutionHistoryProps) {
  return null;
}
