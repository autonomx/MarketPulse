import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type {
  RpgAuthoringDocumentBlock,
  RpgAuthoringEntityCard,
  RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import {
  rpgWorldGenerationReviewClient,
  type RpgWorldGenerationTopicResult,
} from './api/rpgWorldGenerationReviewClient';
import { documentAnchors, presentLoreBlocks } from './RpgWorldCompletionModels';
import { RpgWorldEntityCard } from './RpgWorldEntityCard';
import { RpgWorldEntityDetail } from './RpgWorldEntityDetail';
import { RpgWorldLoreLayout } from './RpgWorldLoreLayout';
import './RpgWorldGenerationCandidateReview.css';

interface Props {
  onAccepted: () => Promise<void> | void;
  onClose: () => void;
  onRetryStarted: () => Promise<void> | void;
  reviewEnabled: boolean;
  result: RpgWorldGenerationTopicResult;
  runId: string;
  section: RpgAuthoringSection;
  worldId: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    : [];
}

function text(value: unknown, fallback = ''): string {
  return value == null || String(value).trim() === '' ? fallback : String(value).trim();
}

function label(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function meaningful(value: unknown): boolean {
  if (value == null || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length > 0;
  return true;
}

function entityCards(
  candidate: Record<string, unknown>,
  section: RpgAuthoringSection,
): RpgAuthoringEntityCard[] {
  const defaultKind = section.entity_kind || section.id.replace(/s$/, '') || 'entity';
  return rows(candidate.entities).map((metadata, index) => {
    const kind = text(metadata.kind ?? metadata.type, defaultKind);
    const id = text(
      metadata.id ?? metadata.entity_id,
      `ent:${section.id}:${String(index + 1).padStart(3, '0')}`,
    );
    const idParts = id.split(':');
    const title = text(
      metadata.name ?? metadata.title ?? metadata.label,
      label(idParts[idParts.length - 1] || id),
    );
    const summary = text(
      metadata.short_summary
      ?? metadata.summary
      ?? metadata.description
      ?? record(metadata.dossier).short_summary,
      'No overview has been written yet.',
    );
    const highlights = Object.entries(metadata)
      .filter(([key, value]) => ![
        'id', 'entity_id', 'name', 'title', 'label', 'kind', 'type', 'summary',
        'short_summary', 'description', 'dossier', 'visibility', 'status',
      ].includes(key) && meaningful(value))
      .filter(([, value]) => !Array.isArray(value) && typeof value !== 'object')
      .slice(0, 4)
      .map(([key, value]) => ({ label: label(key), value }));
    const groups = Object.entries(metadata)
      .filter(([, value]) => Array.isArray(value) && value.length)
      .slice(0, 5)
      .map(([key, value]) => ({
        label: label(key),
        items: value as unknown[],
        style: /(_ids|_refs|tags|languages|regions|locations|factions|classes)$/.test(key)
          ? 'chips'
          : 'list',
      }));
    return {
      id,
      title,
      summary,
      short_summary: summary,
      dossier: metadata.dossier && typeof metadata.dossier === 'object'
        ? metadata.dossier as RpgAuthoringEntityCard['dossier']
        : undefined,
      kind,
      card_type: section.id,
      presentation: {
        variant: kind,
        eyebrow: label(kind),
        badges: [metadata.visibility, metadata.status].filter(meaningful),
        highlights,
        groups,
      },
      metadata,
    };
  });
}

function documentBody(candidate: Record<string, unknown>): RpgAuthoringDocumentBlock[] {
  const blocks: RpgAuthoringDocumentBlock[] = rows(candidate.documents)
    .map((document) => ({
      kind: 'section',
      title: text(document.title ?? document.name, 'Lore'),
      body: text(
        document.full_text
        ?? document.body
        ?? document.text
        ?? document.content
        ?? document.summary,
      ),
    }))
    .filter((block) => Boolean(block.body));
  const facts = rows(candidate.facts);
  if (facts.length) blocks.push({ kind: 'facts', title: 'Canon facts', items: facts });
  if (!blocks.length) blocks.push({ kind: 'json', title: 'Structured canon', value: candidate });
  return blocks;
}

function candidateTitle(candidate: Record<string, unknown>, section: RpgAuthoringSection): string {
  const document = rows(candidate.documents)[0] ?? {};
  const entity = rows(candidate.entities)[0] ?? {};
  return text(document.title ?? entity.name ?? entity.title, section.label);
}

function candidateSummary(candidate: Record<string, unknown>, section: RpgAuthoringSection): string {
  const document = rows(candidate.documents)[0] ?? {};
  const entity = rows(candidate.entities)[0] ?? {};
  return text(
    candidate.summary
    ?? candidate.description
    ?? document.summary
    ?? entity.short_summary
    ?? entity.summary
    ?? entity.description,
    `${section.label} was recovered from a structurally invalid generation response and awaits review.`,
  );
}

function reviewSummary(result: RpgWorldGenerationTopicResult): string {
  const summary = result.validation.summary?.trim() ?? '';
  // Provider validation codes are useful in the issue list, but are not prose
  // and can be a single enormous unbroken token in the review banner.
  if (!summary || summary.length > 240 || summary.includes('world_forge_') || summary.includes('provider_')) {
    return 'Generation completed with validation issues. Review the details below before accepting this topic.';
  }
  return summary;
}

export function RpgWorldGenerationCandidateReview({
  onAccepted,
  onClose,
  onRetryStarted,
  reviewEnabled,
  result,
  runId,
  section,
  worldId,
}: Props) {
  const [contentJson, setContentJson] = useState(JSON.stringify(result.candidate ?? {}, null, 2));
  const [preview, setPreview] = useState<Record<string, unknown>>(result.candidate ?? {});
  const [editing, setEditing] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [inspectedEntityId, setInspectedEntityId] = useState('');

  useEffect(() => {
    const next = result.candidate ?? {};
    setContentJson(JSON.stringify(next, null, 2));
    setPreview(next);
    setEditing(false);
    setFeedback('');
    setInspectedEntityId('');
  }, [result]);

  const entities = useMemo(() => entityCards(preview, section), [preview, section]);
  const inspectedEntity = entities.find((entity) => entity.id === inspectedEntityId);
  const body = useMemo(() => documentBody(preview), [preview]);
  const blocks = useMemo(() => presentLoreBlocks(section.id, body), [body, section.id]);
  const toc = useMemo(() => documentAnchors(blocks), [blocks]);

  const acceptMutation = useMutation({
    mutationFn: () => {
      if (!reviewEnabled) throw new Error('Review opens after world generation finishes.');
      return rpgWorldGenerationReviewClient.accept(runId, result.topic_id, {
        candidate: preview,
        expected_candidate_hash: result.candidate_hash,
      });
    },
    onSuccess: async () => {
      setFeedback('Candidate accepted and promoted to editable authoring canon.');
      await onAccepted();
    },
    onError: (cause) => setFeedback(
      cause instanceof Error ? cause.message : 'Candidate could not be accepted.',
    ),
  });

  const retryMutation = useMutation({
    mutationFn: () => {
      if (!reviewEnabled) throw new Error('Review opens after world generation finishes.');
      return rpgWorldGenerationReviewClient.retry(runId, {
        topic_ids: [result.topic_id],
      });
    },
    onSuccess: async () => {
      setFeedback('A targeted retry run was started. The retained candidate remains available.');
      await onRetryStarted();
    },
    onError: (cause) => setFeedback(
      cause instanceof Error ? cause.message : 'Retry could not be started.',
    ),
  });

  const applyPreview = () => {
    try {
      const parsed = JSON.parse(contentJson) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Candidate must be one JSON object.');
      }
      setPreview(parsed as Record<string, unknown>);
      setFeedback('Edited candidate is shown below. It is still not canon until accepted.');
    } catch (cause) {
      setFeedback(cause instanceof Error ? cause.message : 'Candidate JSON is invalid.');
    }
  };

  return (
    <section className="rpg-generation-candidate-review" aria-label="Recovered lore review page">
      <header className="rpg-generation-candidate-review-banner">
        <div>
          <p className="eyebrow">
            {reviewEnabled ? 'Recovered — Needs Review' : 'Provisional candidate — Generation continuing'}
          </p>
          <h2>{candidateTitle(preview, section)}</h2>
          <p>
            {reviewEnabled
              ? reviewSummary(result)
              : 'This candidate is feeding dependent topics provisionally. Review actions unlock after every topic finishes.'}
          </p>
        </div>
        <div>
          <button type="button" disabled={!reviewEnabled} onClick={() => setEditing((value) => !value)}>
            {editing ? 'Close Editor' : 'Edit Candidate'}
          </button>
          <button type="button" disabled={!reviewEnabled || retryMutation.isPending} onClick={() => retryMutation.mutate()}>
            {retryMutation.isPending ? 'Starting Retry…' : 'Retry Generation'}
          </button>
          <button
            type="button"
            disabled={!reviewEnabled || acceptMutation.isPending || !result.candidate}
            onClick={() => acceptMutation.mutate()}
          >
            {acceptMutation.isPending ? 'Accepting…' : 'Accept Candidate'}
          </button>
          <button type="button" onClick={onClose}>Close</button>
        </div>
      </header>

      {feedback ? (
        <p className="rpg-generation-candidate-review-feedback" aria-live="polite">{feedback}</p>
      ) : null}

      {editing ? (
        <section className="rpg-generation-candidate-review-editor">
          <div>
            <h3>Edit retained candidate</h3>
            <p>Edits remain local to this review page until you accept the candidate.</p>
          </div>
          <textarea
            aria-label={`Edit recovered ${result.topic_id} candidate`}
            rows={24}
            value={contentJson}
            onChange={(event) => setContentJson(event.currentTarget.value)}
          />
          <button type="button" onClick={applyPreview}>Apply Preview</button>
        </section>
      ) : null}

      {result.validation.issues.length ? (
        <details className="rpg-generation-candidate-review-issues">
          <summary>
            {result.validation.issues.length} validation issue
            {result.validation.issues.length === 1 ? '' : 's'}
          </summary>
          {result.validation.issues.map((issue, index) => (
            <article key={`${issue.code}-${index}`}>
              <strong>{label(issue.code)}</strong>
              <span>{[issue.entity_id, issue.field_id].filter(Boolean).join(' · ') || result.topic_id}</span>
              <p>{issue.message || result.validation.summary}</p>
            </article>
          ))}
        </details>
      ) : null}

      {section.page_kind === 'collection' ? (
        <section className="rpg-authoring-page rpg-generation-candidate-collection">
          <div className="rpg-authoring-page-heading">
            <div>
              <p className="eyebrow">Review candidate</p>
              <h2>{candidateTitle(preview, section)}</h2>
              <p>{candidateSummary(preview, section)}</p>
            </div>
            <span>{entities.length} entr{entities.length === 1 ? 'y' : 'ies'}</span>
          </div>
          <div className="rpg-authoring-entity-grid">
            {entities.map((entity) => (
              <RpgWorldEntityCard
                entity={entity}
                key={entity.id}
                onOpen={() => setInspectedEntityId(entity.id)}
                worldId={worldId}
              />
            ))}
          </div>
        </section>
      ) : (
        <section className="rpg-authoring-page">
          <RpgWorldLoreLayout
            blocks={blocks}
            sectionId={section.id}
            summary={candidateSummary(preview, section)}
            title={candidateTitle(preview, section)}
            toc={toc}
          >
            {entities.length ? (
              <section className="rpg-authoring-related-entities">
                <div className="rpg-authoring-page-heading">
                  <div><p className="eyebrow">Connected canon</p><h3>Related entries</h3></div>
                  <span>{entities.length}</span>
                </div>
                <div className="rpg-authoring-entity-grid">
                  {entities.map((entity) => (
                    <RpgWorldEntityCard
                      entity={entity}
                      key={entity.id}
                      onOpen={() => setInspectedEntityId(entity.id)}
                      worldId={worldId}
                    />
                  ))}
                </div>
              </section>
            ) : null}
          </RpgWorldLoreLayout>
        </section>
      )}
      {inspectedEntity ? (
        <RpgWorldEntityDetail
          entity={inspectedEntity}
          onClose={() => setInspectedEntityId('')}
          worldId={worldId}
        />
      ) : null}
    </section>
  );
}
