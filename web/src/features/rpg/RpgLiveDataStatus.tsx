import { useState, type ReactNode } from 'react';

export type RpgLiveDataStatusState = 'loading' | 'refreshing' | 'empty' | 'error' | 'ready';

export interface RpgLiveDataStatusCard {
  id: string;
  label: string;
  state: RpgLiveDataStatusState;
  detail: string;
}

const statusLabels: Record<RpgLiveDataStatusState, string> = {
  loading: 'Loading',
  refreshing: 'Refreshing',
  empty: 'Empty',
  error: 'Error',
  ready: 'Ready',
};

function summarize(cards: RpgLiveDataStatusCard[]) {
  const errorCount = cards.filter((card) => card.state === 'error').length;
  const loadingCount = cards.filter((card) => card.state === 'loading' || card.state === 'refreshing').length;
  const emptyCount = cards.filter((card) => card.state === 'empty').length;

  if (errorCount) {
    return `${errorCount} source${errorCount === 1 ? '' : 's'} need attention`;
  }

  if (loadingCount) {
    return `${loadingCount} source${loadingCount === 1 ? '' : 's'} updating`;
  }

  if (emptyCount) {
    return `${emptyCount} source${emptyCount === 1 ? '' : 's'} empty`;
  }

  return 'All live sources ready';
}

function renderDetail(detail: string): ReactNode {
  const match = /^(Omnix API request failed with status \d+)(?::\s*(.*))?$/.exec(detail);
  if (!match) {
    return detail;
  }

  return (
    <>
      <span>{match[1]}</span>
      {match[2] ? <span>: {match[2]}</span> : null}
    </>
  );
}

interface RpgLiveDataStatusProps {
  cards: RpgLiveDataStatusCard[];
  expanded?: boolean;
  hideWhenCollapsed?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  showToggle?: boolean;
}

export function RpgLiveDataStatus({
  cards,
  expanded,
  hideWhenCollapsed = false,
  onExpandedChange,
  showToggle = true,
}: RpgLiveDataStatusProps) {
  const [internalExpanded, setInternalExpanded] = useState(false);
  const isExpanded = expanded ?? internalExpanded;
  const setIsExpanded = (value: boolean) => {
    if (expanded === undefined) {
      setInternalExpanded(value);
    }
    onExpandedChange?.(value);
  };
  const detailsId = 'rpg-live-data-status-details';
  const className = isExpanded ? 'rpg-card rpg-live-data-status' : 'rpg-card rpg-live-data-status rpg-live-data-status-collapsed';

  if (hideWhenCollapsed && !isExpanded) {
    // A source that failed is said even while the details are hidden (WP-9.10).
    const failed = cards.filter((card) => card.state === 'error');
    return failed.length ? (
      <div className="rpg-live-data-errors" role="alert">
        {failed.map((card) => <p className="platform-empty" key={card.id}>{card.label} could not be loaded: {renderDetail(card.detail)}</p>)}
      </div>
    ) : null;
  }

  return (
    <section className={className} aria-label="RPG live data status">
      <div className="rpg-section-heading">
        <p className="eyebrow">Live data status</p>
        <div className="rpg-live-data-summary">
          <span>{summarize(cards)}</span>
          {showToggle ? (
            <button
              className="rpg-secondary-button rpg-live-data-toggle"
              type="button"
              aria-controls={detailsId}
              aria-expanded={isExpanded}
              onClick={() => setIsExpanded(!isExpanded)}
            >
              {isExpanded ? 'Collapse live data' : 'Expand live data'}
            </button>
          ) : null}
        </div>
      </div>
      <div id={detailsId} className="rpg-data-status-grid" hidden={!isExpanded}>
        {cards.map((card) => (
          <article
            aria-label={`${card.label} status`}
            className={`rpg-data-status-card rpg-data-status-${card.state}`}
            key={card.id}
          >
            <div>
              <strong>{card.label}</strong>
              <span>{statusLabels[card.state]}</span>
            </div>
            <p>{renderDetail(card.detail)}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
