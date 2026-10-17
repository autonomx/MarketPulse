import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rpgWorldAuthoringClient } from './api/rpgWorldAuthoringClient';
import './RpgWorldDossierQualityPanel.css';

interface RpgWorldDossierQualityPanelProps {
  worldId: string;
}

function issueLabel(value: string): string {
  const [code, actual, target] = value.split(':');
  if (code === 'dossier_word_count') return `${actual ?? 0} of ${target ?? '?'} required words`;
  if (code === 'dossier_section_count') return `${actual ?? 0} of ${target ?? '?'} required sections`;
  return code.replace(/[_-]+/g, ' ');
}

export function RpgWorldDossierQualityPanel({ worldId }: RpgWorldDossierQualityPanelProps) {
  const queryClient = useQueryClient();
  const [limit, setLimit] = useState(10);
  const [planOpen, setPlanOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const qualityQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-dossier-quality', worldId],
    queryFn: () => rpgWorldAuthoringClient.dossierQuality(worldId),
  });
  const plan = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.enrichDossiers(worldId, {
      dry_run: true,
      limit,
    }),
    onSuccess: (result) => {
      setPlanOpen(true);
      setFeedback(`Prepared an editorial-only enrichment plan for ${result.candidate_count ?? 0} entries.`);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Enrichment plan could not be prepared.'),
  });
  const enrich = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.enrichDossiers(worldId, {
      dry_run: false,
      limit,
      directives: {
        focus: 'Expand thin or projected lore into distinct, multi-paragraph sections without changing canon.',
      },
    }),
    onSuccess: async (result) => {
      const completed = result.completed?.length ?? 0;
      const failed = result.failed?.length ?? 0;
      setPlanOpen(false);
      setFeedback(`Enriched ${completed} dossier${completed === 1 ? '' : 's'}${failed ? `; ${failed} failed and remain unchanged` : ''}.`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-dossier-quality', worldId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
      ]);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Dossiers could not be enriched.'),
  });

  if (qualityQuery.isPending) {
    return <section className="rpg-dossier-quality-panel"><p>Measuring dossier quality…</p></section>;
  }
  if (qualityQuery.isError || !qualityQuery.data) {
    return <section className="rpg-dossier-quality-panel"><p className="rpg-world-catalog-error">Dossier quality could not be measured.</p></section>;
  }

  const { metrics, enrichment_candidates: candidates, by_topic: topics } = qualityQuery.data;
  const plannedCandidates = candidates.slice(0, limit);

  return (
    <section className="rpg-dossier-quality-panel" aria-label="Dossier quality">
      <header>
        <div>
          <p className="eyebrow">Editorial quality</p>
          <h3>Rich dossier coverage</h3>
          <p>Measure long-form lore without changing canonical mechanics, IDs, facts, or relationships.</p>
        </div>
        <strong>{metrics.coverage_percent}%</strong>
      </header>

      <div role="img" className="rpg-dossier-quality-meter" aria-label={`${metrics.coverage_percent} percent rich dossier coverage`}>
        <i style={{ width: `${Math.max(0, Math.min(100, metrics.coverage_percent))}%` }} />
      </div>

      <div className="rpg-dossier-quality-metrics">
        <article><small>Entities</small><strong>{metrics.entities}</strong></article>
        <article><small>Rich dossiers</small><strong>{metrics.rich_dossiers}</strong></article>
        <article><small>Thin / invalid</small><strong>{metrics.invalid_or_thin_dossiers}</strong></article>
        <article><small>Projected legacy</small><strong>{metrics.projected_legacy_dossiers}</strong></article>
        <article><small>Average words</small><strong>{metrics.average_words}</strong></article>
        <article><small>Broken links</small><strong>{metrics.unresolved_related_entity_ids}</strong></article>
      </div>

      <details className="rpg-dossier-quality-topics">
        <summary>Coverage by topic ({topics.length})</summary>
        <div>
          {topics.map((topic) => (
            <article key={topic.topic_id}>
              <strong>{topic.topic_id.replace(/[_-]+/g, ' ')}</strong>
              <span>{topic.coverage_percent}%</span>
              <small>{topic.rich}/{topic.entities} rich · {topic.average_words} avg words</small>
            </article>
          ))}
        </div>
      </details>

      <div className="rpg-dossier-quality-actions">
        <label>
          <span>Batch size</span>
          <select value={limit} onChange={(event) => setLimit(Number(event.currentTarget.value))}>
            {[5, 10, 15, 25].map((value) => <option key={value} value={value}>{value} entries</option>)}
          </select>
        </label>
        <button type="button" disabled={!candidates.length || plan.isPending} onClick={() => plan.mutate()}>
          {plan.isPending ? 'Preparing…' : 'Preview enrichment'}
        </button>
      </div>

      {planOpen ? (
        <section className="rpg-dossier-enrichment-plan">
          <header><h4>Editorial-only enrichment preview</h4><span>{plannedCandidates.length} entries</span></header>
          <p>Only <code>short_summary</code> and <code>dossier</code> will be replaced. Every candidate keeps its canonical identity and structured fields.</p>
          <div>
            {plannedCandidates.map((candidate) => (
              <article key={`${candidate.topic_id}:${candidate.entity_id}`}>
                <strong>{candidate.title}</strong>
                <small>{candidate.topic_id.replace(/[_-]+/g, ' ')} · {candidate.word_count} words</small>
                <p>{candidate.issues.map(issueLabel).join(' · ')}</p>
              </article>
            ))}
          </div>
          <footer>
            <button className="rpg-secondary-button" type="button" onClick={() => setPlanOpen(false)}>Cancel</button>
            <button type="button" disabled={enrich.isPending} onClick={() => enrich.mutate()}>
              {enrich.isPending ? 'Enriching…' : `Enrich ${plannedCandidates.length} dossiers`}
            </button>
          </footer>
        </section>
      ) : null}

      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
    </section>
  );
}
