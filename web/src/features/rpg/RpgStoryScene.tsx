/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { RpgHeroSummaryPreview, RpgSessionSummaryPreview, RpgStoryMessagePreview } from './rpgUiState';
import {
  markRpgTurnReactCommitted,
  markRpgTurnVisible,
  rpgDiagnosticsEnabled,
  useLatestRpgTurnDiagnostics,
} from './rpgTurnDiagnostics';
import { storyMessageIdentity, useRpgTurnUiMessages } from './rpgTurnUiStore';
import './RpgVisualAssets.css';

const SCENE_ART_SRC = '/rpg/glimmerdeep-pass-scene.svg';

interface RpgStorySceneProps {
  children: ReactNode;
  heroSummary: RpgHeroSummaryPreview;
  recentEvents: string[];
  selectedSessionSummary: RpgSessionSummaryPreview;
  storyMessages?: RpgStoryMessagePreview[];
}

export function RpgStoryScene({ children, heroSummary, recentEvents, selectedSessionSummary, storyMessages = [] }: RpgStorySceneProps) {
  const isPreview = selectedSessionSummary.source === 'preview';
  const [recentEventsExpanded, setRecentEventsExpanded] = useState(false);
  const visibleStoryMessages = useRpgTurnUiMessages(selectedSessionSummary.id, [...storyMessages]);
  const diagnostics = useLatestRpgTurnDiagnostics(selectedSessionSummary.id);
  const showDiagnostics = rpgDiagnosticsEnabled();
  const dialogueRef = useRef<HTMLDivElement | null>(null);
  const previousScrollHeight = useRef(0);
  const interactionIds = useMemo(
    () => [...new Set(visibleStoryMessages.map((message) => message.interactionId).filter((value): value is string => Boolean(value)))],
    [visibleStoryMessages],
  );
  const interactionKey = interactionIds.join('|');

  useLayoutEffect(() => {
    const element = dialogueRef.current;
    if (element) {
      const priorHeight = previousScrollHeight.current;
      const wasNearBottom = priorHeight === 0 || element.scrollTop + element.clientHeight >= priorHeight - 24;
      if (!wasNearBottom && priorHeight > 0) {
        element.scrollTop += Math.max(0, element.scrollHeight - priorHeight);
      }
      previousScrollHeight.current = element.scrollHeight;
    }

    markRpgTurnReactCommitted(selectedSessionSummary.id, interactionIds);
    if (typeof requestAnimationFrame !== 'function') {
      markRpgTurnVisible(selectedSessionSummary.id, interactionIds);
      return undefined;
    }
    const frame = requestAnimationFrame(() => {
      markRpgTurnVisible(selectedSessionSummary.id, interactionIds);
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedSessionSummary.id, interactionKey]);

  return (
    <section className="rpg-card rpg-story-card" aria-labelledby="rpg-story-scene-title">
      <div className="rpg-story-heading">
        <div>
          <p className="eyebrow">Story / scene</p>
          <h3 id="rpg-story-scene-title">📍 {selectedSessionSummary.location}</h3>
          <div className="rpg-chip-row">
            <span>{selectedSessionSummary.title}</span>
            <span>{selectedSessionSummary.turnLabel}</span>
            <span>{selectedSessionSummary.updatedAt}</span>
          </div>
        </div>
        <div role="img"
          className={isPreview ? 'rpg-scene-art rpg-scene-art-has-image' : 'rpg-scene-art'}
          aria-label={isPreview ? `${selectedSessionSummary.location} scene preview` : `${selectedSessionSummary.location} scene`}
        >
          {isPreview ? <img src={SCENE_ART_SRC} alt="" aria-hidden="true" /> : <span className="rpg-live-visual-label">Live scene</span>}
        </div>
      </div>
      <p className="rpg-scene-copy">{selectedSessionSummary.summary}</p>
      <div role="log" ref={dialogueRef} className="rpg-dialogue-stack" aria-label="Conversation" aria-live="polite">
        {isPreview ? (
          <>
            <article>
              <span className="rpg-avatar rpg-avatar-small">{heroSummary.avatar}</span>
              <div>
                <strong>{heroSummary.name} (You)</strong>
                <p>“I scan the current scene for useful details before committing to the next deterministic turn.”</p>
              </div>
            </article>
            <article>
              <span className="rpg-avatar rpg-avatar-small rpg-avatar-omnix">O</span>
              <div>
                <strong>Omnix (Narrator)</strong>
                <p>The preview session is ready for a replay-preserving command.</p>
              </div>
            </article>
          </>
        ) : visibleStoryMessages.length ? (
          visibleStoryMessages.map((message, index) => (
            <article key={storyMessageIdentity(message, index)} data-interaction-id={message.interactionId}>
              <span className={`rpg-avatar rpg-avatar-small${message.tone === 'narrator' ? ' rpg-avatar-omnix' : ''}`}>
                {message.avatar}
              </span>
              <div>
                <strong>{message.speaker}</strong>
                <p>{message.text}</p>
              </div>
            </article>
          ))
        ) : (
          recentEvents.slice(0, 2).map((event, index) => (
            <article key={`recent-event:${index}`}>
              <span className="rpg-avatar rpg-avatar-small rpg-avatar-omnix">{index + 1}</span>
              <div>
                <strong>Live session event</strong>
                <p>{event}</p>
              </div>
            </article>
          ))
        )}
      </div>
      {showDiagnostics && diagnostics ? (
        <details className="rpg-turn-diagnostics">
          <summary>Turn diagnostics</summary>
          <dl>
            <DiagnosticRow label="Trace" value={diagnostics.traceId} />
            <DiagnosticRow label="Submission" value={diagnostics.submissionId} />
            <DiagnosticRow label="Interaction" value={diagnostics.interactionId} />
            <DiagnosticRow label="Response bytes" value={diagnostics.responseBytes} />
            <DiagnosticRow label="Server attribution" value={formatPercent(diagnostics.serverAttributionPercent)} />
            <DiagnosticRow label="Request → headers" value={formatMs(diagnostics.client.requestToHeadersMs)} />
            <DiagnosticRow label="Headers → body" value={formatMs(diagnostics.client.headersToBodyMs)} />
            <DiagnosticRow label="Body → parse" value={formatMs(diagnostics.client.bodyToParseMs)} />
            <DiagnosticRow label="Parse → store" value={formatMs(diagnostics.client.parseToStoreMs)} />
            <DiagnosticRow label="Store → React commit" value={formatMs(diagnostics.client.storeToCommitMs)} />
            <DiagnosticRow label="Commit → visible" value={formatMs(diagnostics.client.commitToVisibleMs)} />
            <DiagnosticRow label="Request → visible" value={formatMs(diagnostics.client.requestToVisibleMs)} />
          </dl>
          {diagnostics.serverTiming ? <code>{diagnostics.serverTiming}</code> : null}
          {diagnostics.serverPayloadTiming ? <pre>{JSON.stringify(diagnostics.serverPayloadTiming, null, 2)}</pre> : null}
        </details>
      ) : null}
      {children}
      <div className={`rpg-event-strip${recentEventsExpanded ? ' is-expanded' : ' is-collapsed'}`}>
        <button
          aria-controls="rpg-recent-events-list"
          aria-expanded={recentEventsExpanded}
          className="rpg-event-strip-summary"
          onClick={() => setRecentEventsExpanded((expanded) => !expanded)}
          title={recentEventsExpanded ? 'Collapse recent events' : 'Expand recent events'}
          type="button"
        >
          <strong>Recent events</strong>
          <span aria-hidden="true" className="rpg-event-strip-toggle" />
        </button>
        <ul id="rpg-recent-events-list">
          {recentEvents.map((event, index) => (
            <li key={`recent-event-list:${index}`}>{event}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function DiagnosticRow({ label, value }: { label: string; value: string | number | undefined }) {
  if (value === undefined || value === '') return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function formatMs(value: number | undefined): string | undefined {
  return value === undefined ? undefined : `${value.toFixed(1)} ms`;
}

function formatPercent(value: number | undefined): string | undefined {
  return value === undefined ? undefined : `${value.toFixed(1)}%`;
}
