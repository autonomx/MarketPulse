import type { PaperTradeJournalEntry } from './tradingPaperAnalyticsApi';
import { deriveAutomatedTradeReview } from './deriveAutomatedTradeReview';
import './TradingAutomatedReview.css';

function label(value: string): string {
  return value.toLowerCase().replaceAll('_', ' ');
}

export function TradingAutomatedReview({ entry }: { entry: PaperTradeJournalEntry }) {
  const review = deriveAutomatedTradeReview(entry);
  return (
    <section className="trading-automated-review" data-priority={review.priority} aria-label="Automated trade review">
      <header>
        <div>
          <strong>Automated review</strong>
          <small>Deterministic advisory · {review.version}</small>
        </div>
        <span>{review.priority.toUpperCase()}</span>
      </header>

      <div className="automated-review-boundary">
        <strong>Operator gate unchanged</strong>
        <span>
          Persisted operator review: {label(review.operator_review_state)}. This automated review cannot mark a trade reviewed or authorize AUTO PAPER.
        </span>
      </div>

      <div className="automated-review-findings">
        {review.findings.map((finding) => (
          <div key={finding.code} data-severity={finding.severity}>
            <span>{finding.severity === 'high' ? '!' : finding.severity === 'attention' ? '△' : '•'}</span>
            <div><strong>{label(finding.code)}</strong><p>{finding.summary}</p></div>
          </div>
        ))}
      </div>

      <div className="automated-review-prompts">
        <strong>Operator review prompts</strong>
        <ul>{review.prompts.map((prompt) => <li key={prompt}>{prompt}</li>)}</ul>
      </div>
    </section>
  );
}
