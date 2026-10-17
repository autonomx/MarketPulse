import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldAuthoringClient,
  type RpgAuthoringDossierPreviewResponse,
  type RpgAuthoringEntityCard,
  type RpgAuthoringEntityDossier,
  type RpgAuthoringTopic,
} from './api/rpgWorldAuthoringClient';
import { RpgWorldDossierSectionEditor } from './RpgWorldDossierSectionEditor';

interface RpgWorldEntityEditorProps {
  entity: RpgAuthoringEntityCard;
  topic: RpgAuthoringTopic;
  worldId: string;
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function parseDossier(value: string): RpgAuthoringEntityDossier | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const dossier = parsed as Partial<RpgAuthoringEntityDossier>;
    if (!Array.isArray(dossier.sections)) return null;
    return {
      schema_version: dossier.schema_version ?? 'rpg_world_entity_dossier_v1',
      subtitle: dossier.subtitle ?? '',
      quote: dossier.quote ?? null,
      quick_facts: Array.isArray(dossier.quick_facts) ? dossier.quick_facts : [],
      sections: dossier.sections,
      related_entity_ids: Array.isArray(dossier.related_entity_ids) ? dossier.related_entity_ids : [],
      generated_from_legacy: dossier.generated_from_legacy,
      quality_enriched: dossier.quality_enriched,
    };
  } catch {
    return null;
  }
}

function operationLabel(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function RpgWorldEntityEditor({ entity, topic, worldId }: RpgWorldEntityEditorProps) {
  const queryClient = useQueryClient();
  const initialEntity = JSON.stringify(entity.metadata, null, 2);
  const initialDossier = JSON.stringify(entity.dossier ?? {}, null, 2);
  const [open, setOpen] = useState(false);
  const [rawEntity, setRawEntity] = useState(initialEntity);
  const rawEntityRef = useRef(initialEntity);
  const [entityDraftDirty, setEntityDraftDirty] = useState(false);
  const [shortSummary, setShortSummary] = useState(entity.short_summary || entity.summary || '');
  const [rawDossier, setRawDossier] = useState(initialDossier);
  const rawDossierRef = useRef(initialDossier);
  const [dossierDraftDirty, setDossierDraftDirty] = useState(false);
  const [dossierPreview, setDossierPreview] = useState<RpgAuthoringDossierPreviewResponse | null>(null);
  const [rawDirectives, setRawDirectives] = useState('{}');
  const [feedback, setFeedback] = useState('');
  const queryKey = ['feature', 'rpg', 'world-entity', worldId, topic.topic_id, entity.id];
  const entityQuery = useQuery({
    queryKey,
    queryFn: () => rpgWorldAuthoringClient.entity(worldId, topic.topic_id, entity.id),
    enabled: open,
  });
  const dossierDraft = parseDossier(rawDossier);

  const replaceEntityDraft = (value: string) => {
    rawEntityRef.current = value;
    setRawEntity(value);
  };

  const replaceDossierDraft = (value: string) => {
    rawDossierRef.current = value;
    setRawDossier(value);
  };

  const updateDossierDraft = (value: RpgAuthoringEntityDossier) => {
    setDossierDraftDirty(true);
    replaceDossierDraft(JSON.stringify(value, null, 2));
  };

  const discardPreview = () => {
    setDossierPreview(null);
    setDossierDraftDirty(false);
    setShortSummary(entity.short_summary || entity.summary || '');
    replaceDossierDraft(JSON.stringify(entity.dossier ?? {}, null, 2));
    setFeedback('Discarded the regeneration preview; stored lore was not changed.');
  };

  useEffect(() => {
    if (!entityDraftDirty) replaceEntityDraft(JSON.stringify(entity.metadata, null, 2));
    if (!dossierDraftDirty) {
      setShortSummary(entity.short_summary || entity.summary || '');
      replaceDossierDraft(JSON.stringify(entity.dossier ?? {}, null, 2));
    }
  }, [entity.id, entity.metadata, entity.dossier, entity.short_summary, entity.summary, entityDraftDirty, dossierDraftDirty, topic.content_hash]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId, topic.topic_id] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-dossier-quality', worldId] }),
    ]);
  };

  const saveDossier = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.updateEntityDossier(
      worldId,
      topic.topic_id,
      entity.id,
      {
        expected_draft_revision: topic.draft_revision,
        expected_content_hash: topic.content_hash,
        short_summary: shortSummary,
        dossier: parseObject(rawDossierRef.current, 'Dossier'),
      },
    ),
    onSuccess: async (result) => {
      setDossierPreview(null);
      setDossierDraftDirty(false);
      const storedDossier = result.entity.dossier && typeof result.entity.dossier === 'object'
        ? result.entity.dossier
        : {};
      replaceDossierDraft(JSON.stringify(storedDossier, null, 2));
      setShortSummary(String(result.entity.short_summary || shortSummary));
      setFeedback(`Saved the editorial dossier for ${entity.title}. Canonical IDs, mechanics, and relationships were preserved.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Dossier could not be saved.'),
  });

  const previewDossier = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.previewEntityDossier(
      worldId,
      topic.topic_id,
      entity.id,
      {
        expected_draft_revision: topic.draft_revision,
        expected_content_hash: topic.content_hash,
        directives: parseObject(rawDirectives, 'Regeneration directives'),
      },
    ),
    onSuccess: (result) => {
      setDossierPreview(result);
      setDossierDraftDirty(true);
      setShortSummary(result.short_summary);
      replaceDossierDraft(JSON.stringify(result.dossier, null, 2));
      setFeedback(`Generated a preview for ${entity.title}. Nothing has been stored; review or edit it before applying.`);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Dossier preview could not be generated.'),
  });

  const applyPreview = useMutation({
    mutationFn: () => {
      if (!dossierPreview) throw new Error('No dossier preview is available.');
      return rpgWorldAuthoringClient.updateEntityDossier(
        worldId,
        topic.topic_id,
        entity.id,
        {
          expected_draft_revision: dossierPreview.expected_draft_revision,
          expected_content_hash: dossierPreview.expected_content_hash,
          short_summary: shortSummary,
          dossier: parseObject(rawDossierRef.current, 'Dossier preview'),
        },
      );
    },
    onSuccess: async (result) => {
      setDossierPreview(null);
      setDossierDraftDirty(false);
      const storedDossier = result.entity.dossier && typeof result.entity.dossier === 'object'
        ? result.entity.dossier
        : {};
      replaceDossierDraft(JSON.stringify(storedDossier, null, 2));
      setShortSummary(String(result.entity.short_summary || shortSummary));
      setFeedback(`Applied the reviewed prose preview for ${entity.title}; no canonical structured fields changed.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Dossier preview could not be applied.'),
  });

  const save = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.updateEntity(
      worldId,
      topic.topic_id,
      entity.id,
      {
        expected_draft_revision: topic.draft_revision,
        expected_content_hash: topic.content_hash,
        changes: parseObject(rawEntityRef.current, 'Entity'),
      },
    ),
    onSuccess: async (result) => {
      setEntityDraftDirty(false);
      replaceEntityDraft(JSON.stringify(result.entity, null, 2));
      setFeedback(`Saved ${entity.title}. ${result.stale_entity_ids.length} dependent entities need review.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Entity could not be saved.'),
  });

  const regenerate = useMutation({
    mutationFn: () => rpgWorldAuthoringClient.regenerateEntity(
      worldId,
      topic.topic_id,
      entity.id,
      {
        expected_draft_revision: topic.draft_revision,
        expected_content_hash: topic.content_hash,
        directives: parseObject(rawDirectives, 'Regeneration directives'),
      },
    ),
    onSuccess: async (result) => {
      setEntityDraftDirty(false);
      setFeedback(`Regenerated ${entity.title} while preserving ${Math.max(0, ((topic.content.entities as unknown[]) ?? []).length - 1)} sibling entities.`);
      replaceEntityDraft(JSON.stringify(result.entity, null, 2));
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Entity could not be regenerated.'),
  });

  return (
    <details
      className="rpg-authoring-entity-editor"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{entity.dossier?.generated_from_legacy ? 'Generate LLM dossier' : 'Edit or regenerate'}</summary>
      <div className="rpg-authoring-entity-editor-body">
        {entityQuery.isPending ? <p>Loading entity history…</p> : null}
        {entityQuery.isError ? <p className="rpg-world-catalog-error">Unable to load entity history.</p> : null}

        <section className="rpg-authoring-dossier-editor">
          <div>
            <h4>Editorial dossier</h4>
            <p>These controls update reading prose only. Entity identity, mechanics, references, facts, and relationships remain unchanged.</p>
          </div>
          {dossierPreview ? (
            <div className="rpg-authoring-feedback" role="status">
              Preview mode: the generated prose is local and editable. Stored canon remains unchanged until Apply Preview is selected.
            </div>
          ) : null}
          <label>
            <span>Short catalogue summary</span>
            <textarea
              aria-label={`Short summary for ${entity.title}`}
              rows={3}
              value={shortSummary}
              onChange={(event) => {
                setDossierDraftDirty(true);
                setShortSummary(event.currentTarget.value);
              }}
            />
          </label>

          {dossierDraft ? (
            <RpgWorldDossierSectionEditor
              dossier={dossierDraft}
              entityTitle={entity.title}
              onChange={updateDossierDraft}
            />
          ) : (
            <p className="rpg-world-catalog-error">The dossier JSON is invalid. Repair it in Advanced dossier JSON before using the section editor.</p>
          )}

          <details className="rpg-authoring-structured-data">
            <summary>Advanced dossier JSON</summary>
            <label>
              <span>Rich dossier JSON</span>
              <textarea
                aria-label={`Dossier JSON for ${entity.title}`}
                rows={18}
                value={rawDossier}
                onChange={(event) => {
                  setDossierDraftDirty(true);
                  replaceDossierDraft(event.currentTarget.value);
                }}
              />
            </label>
          </details>

          <label>
            <span>Dossier regeneration directives</span>
            <textarea
              aria-label={`Regeneration directives for ${entity.title}`}
              rows={4}
              value={rawDirectives}
              onChange={(event) => setRawDirectives(event.currentTarget.value)}
              placeholder={'{"focus":"Deepen motives without changing identity"}'}
            />
          </label>
          <div className="rpg-authoring-entity-editor-actions">
            {dossierPreview ? (
              <>
                <button type="button" disabled={applyPreview.isPending || !dossierDraft} onClick={() => applyPreview.mutate()}>
                  {applyPreview.isPending ? 'Applying preview…' : 'Apply Preview'}
                </button>
                <button className="rpg-secondary-button" type="button" onClick={discardPreview}>Discard Preview</button>
              </>
            ) : (
              <>
                <button type="button" disabled={saveDossier.isPending || !dossierDraft} onClick={() => saveDossier.mutate()}>
                  {saveDossier.isPending ? 'Saving dossier…' : 'Save Dossier Only'}
                </button>
                <button className="rpg-secondary-button" type="button" disabled={previewDossier.isPending} onClick={() => previewDossier.mutate()}>
                  {previewDossier.isPending ? 'Generating preview…' : 'Preview Dossier Regeneration'}
                </button>
              </>
            )}
          </div>
        </section>

        <details className="rpg-authoring-canonical-entity-editor">
          <summary>Canonical structured entity</summary>
          <label>
            <span>Structured entity JSON</span>
            <textarea
              aria-label={`Entity JSON for ${entity.title}`}
              rows={14}
              value={rawEntity}
              onChange={(event) => {
                setEntityDraftDirty(true);
                replaceEntityDraft(event.currentTarget.value);
              }}
            />
          </label>
          <div className="rpg-authoring-entity-editor-actions">
            <button type="button" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save Canonical Entity'}
            </button>
          </div>
        </details>

        <label>
          <span>Entire entity regeneration directives</span>
          <textarea
            aria-label={`Entire entity regeneration directives for ${entity.title}`}
            rows={5}
            value={rawDirectives}
            onChange={(event) => setRawDirectives(event.currentTarget.value)}
            placeholder={'{"focus":"Deepen motives without changing identity"}'}
          />
        </label>
        <button
          className="rpg-secondary-button"
          type="button"
          disabled={regenerate.isPending}
          onClick={() => regenerate.mutate()}
        >
          {regenerate.isPending ? 'Regenerating…' : 'Regenerate Entire Entity'}
        </button>
        {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
        {entityQuery.data?.history.length ? (
          <details className="rpg-authoring-entity-history">
            <summary>Entity history ({entityQuery.data.history.length})</summary>
            {entityQuery.data.history.map((entry) => (
              <article key={entry.history_sequence}>
                <strong>{operationLabel(entry.operation)}</strong>
                <time>{new Date(entry.created_at).toLocaleString()}</time>
                <details><summary>Previous entity</summary><pre>{JSON.stringify(entry.before, null, 2)}</pre></details>
              </article>
            ))}
          </details>
        ) : null}
      </div>
    </details>
  );
}
