import type { AnalyticsNumeric, PaperTradeJournalEntry } from './tradingPaperAnalyticsApi';

export type AutomatedReviewPriority = 'routine' | 'attention' | 'high';

export interface AutomatedReviewFinding {
  code: string;
  severity: 'info' | 'attention' | 'high';
  summary: string;
}

export interface AutomatedTradeReview {
  version: 'trade-review-v1';
  priority: AutomatedReviewPriority;
  findings: AutomatedReviewFinding[];
  prompts: string[];
  requires_operator_review: boolean;
  operator_review_state: string;
}

function numeric(value: AnalyticsNumeric | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function signed(value: number, digits = 2, suffix = ''): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}${suffix}`;
}

function hasCanonicalLifecycle(entry: PaperTradeJournalEntry): boolean {
  return Boolean(
    entry.trade_id
      && entry.entry_order_id
      && entry.exit_order_id
      && entry.session_id
      && entry.setup_id
      && entry.trade_intent_id
      && entry.risk_decision_id
      && entry.protection_id
      && entry.entry_fill_ids.length
      && entry.exit_fill_ids.length,
  );
}

/**
 * Deterministic, advisory post-trade review of persisted AUTO PAPER evidence.
 *
 * The review deliberately does not infer causality, mutate review_state, or
 * authorize execution. Human/operator review remains a separate persisted gate.
 */
export function deriveAutomatedTradeReview(entry: PaperTradeJournalEntry): AutomatedTradeReview {
  const findings: AutomatedReviewFinding[] = [];
  const prompts: string[] = [];
  const rResult = numeric(entry.r_result);
  const maeR = numeric(entry.mae_r);
  const mfeR = numeric(entry.mfe_r);
  const shortfallBps = numeric(entry.implementation_shortfall_bps);
  const lifecycleComplete = hasCanonicalLifecycle(entry);

  if (!lifecycleComplete) {
    findings.push({
      code: 'CANONICAL_LIFECYCLE_INCOMPLETE',
      severity: 'high',
      summary: 'One or more canonical lifecycle identities or fill IDs are missing.',
    });
    prompts.push('Resolve the missing lifecycle or fill evidence before relying on this trade for operational review.');
  }

  if (entry.outcome === 'loss') {
    findings.push({
      code: 'REALIZED_LOSS',
      severity: 'attention',
      summary: `Trade closed with a realized loss${rResult === null ? '.' : ` at ${signed(rResult, 3, 'R')}.`}`,
    });
    prompts.push('Compare the persisted setup state at signal time with the execution-time evidence before marking operator review complete.');
  }

  if (maeR !== null && maeR <= -1) {
    findings.push({
      code: 'MAE_REACHED_INITIAL_RISK',
      severity: 'attention',
      summary: `Maximum adverse excursion reached ${signed(maeR, 3, 'R')}, at or beyond the initial 1R risk distance.`,
    });
    prompts.push('Inspect stop/protection timing and the bar or quote evidence around the maximum adverse excursion.');
  }

  if (shortfallBps !== null && shortfallBps > 0) {
    findings.push({
      code: 'POSITIVE_IMPLEMENTATION_SHORTFALL',
      severity: 'attention',
      summary: `Implementation shortfall was ${signed(shortfallBps, 2, ' bps')}.`,
    });
    prompts.push('Review signal-to-executable movement and fill slippage separately before attributing the shortfall.');
  }

  if (mfeR !== null && rResult !== null && mfeR > rResult) {
    findings.push({
      code: 'MFE_EXCEEDED_REALIZED_R',
      severity: 'info',
      summary: `MFE ${signed(mfeR, 3, 'R')} exceeded realized ${signed(rResult, 3, 'R')}.`,
    });
    prompts.push('Compare the realized exit with MFE and the persisted exit reason; do not infer an alternative exit without replay evidence.');
  }

  const normalizedExit = (entry.exit_reason ?? '').toLowerCase();
  if (normalizedExit.includes('stop')) {
    findings.push({
      code: 'STOP_EXIT',
      severity: 'info',
      summary: `Persisted exit reason is ${entry.exit_reason}.`,
    });
  } else if (normalizedExit.includes('target') || normalizedExit.includes('take_profit')) {
    findings.push({
      code: 'TARGET_EXIT',
      severity: 'info',
      summary: `Persisted exit reason is ${entry.exit_reason}.`,
    });
  }

  if (entry.mae_r == null || entry.mfe_r == null) {
    findings.push({
      code: 'EXCURSION_EVIDENCE_INCOMPLETE',
      severity: 'attention',
      summary: 'MAE and/or MFE evidence is unavailable for this completed trade.',
    });
    prompts.push('Treat excursion-based review as incomplete until MAE/MFE evidence is available.');
  }

  if (!findings.length) {
    findings.push({
      code: 'NO_EXCEPTION_DETECTED',
      severity: 'info',
      summary: 'No deterministic review exception was detected from the retained trade evidence.',
    });
  }

  if (!prompts.length) {
    prompts.push('Confirm the setup, execution, and exit evidence before recording the separate operator review decision.');
  }

  const priority: AutomatedReviewPriority = findings.some((finding) => finding.severity === 'high')
    ? 'high'
    : findings.some((finding) => finding.severity === 'attention')
      ? 'attention'
      : 'routine';

  return {
    version: 'trade-review-v1',
    priority,
    findings,
    prompts: [...new Set(prompts)],
    requires_operator_review: entry.review_state !== 'reviewed',
    operator_review_state: entry.review_state,
  };
}
