export type WorkspaceQualitySignal = {
  id: string;
  label: string;
  passed: boolean;
  severity: 'info' | 'warning' | 'error';
};

export type WorkspaceQualitySummary = {
  total: number;
  passed: number;
  failed: number;
  hasBlockingIssues: boolean;
};

export function summarizeWorkspaceQuality(signals: WorkspaceQualitySignal[]): WorkspaceQualitySummary {
  const passed = signals.filter((signal) => signal.passed).length;
  const failed = signals.length - passed;
  const hasBlockingIssues = signals.some((signal) => !signal.passed && signal.severity === 'error');

  return {
    total: signals.length,
    passed,
    failed,
    hasBlockingIssues,
  };
}

export function getWorkspaceQualityStatus(summary: WorkspaceQualitySummary): 'ready' | 'review' | 'blocked' {
  if (summary.hasBlockingIssues) {
    return 'blocked';
  }

  if (summary.failed > 0) {
    return 'review';
  }

  return 'ready';
}
