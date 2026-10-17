import type {
  RpgAuthoringEntityCard,
  RpgAuthoringTopic,
} from './api/rpgWorldAuthoringClient';
import { RpgWorldEntityEditor } from './RpgWorldEntityEditor';
import { formatAuthoringValue } from './RpgWorldEntityCard';
import './RpgWorldRichPresentation.css';
import './RpgWorldDossierDesign.css';

interface RpgWorldEntityDetailProps {
  entity: RpgAuthoringEntityCard;
  imageAssetId?: string;
  onClose: () => void;
  onOpenRelated?: (entityId: string) => void;
  topic?: RpgAuthoringTopic;
  worldId: string;
}

const RESERVED_FIELDS = new Set([
  'id',
  'entity_id',
  'name',
  'title',
  'label',
  'kind',
  'description',
  'summary',
  'short_summary',
  'subtitle',
  'quote',
  'dossier',
  'visibility',
  'schema_version',
]);

function humanize(value: string): string {
  const candidate = value.includes(':') ? value.split(':').slice(1).join(':') : value;
  return candidate
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function assetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}

function meaningful(value: unknown): boolean {
  if (value == null || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length > 0;
  return true;
}

function fieldStyle(key: string, value: unknown): 'chips' | 'list' | 'facts' {
  if (!Array.isArray(value)) return 'facts';
  if (/(^|_)(ids?|refs?|tags?|languages?|regions?|locations?|factions?|classes?|schools?)$/.test(key)) {
    return 'chips';
  }
  return value.some((item) => typeof item === 'object') ? 'facts' : 'list';
}

function DetailValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <ul>
        {value.map((item, index) => (
          <li key={`${formatAuthoringValue(item)}:${index}`}>{formatAuthoringValue(item)}</li>
        ))}
      </ul>
    );
  }
  if (value && typeof value === 'object') {
    return (
      <dl className="rpg-authoring-detail-nested-facts">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key}><dt>{humanize(key)}</dt><dd>{formatAuthoringValue(item)}</dd></div>
        ))}
      </dl>
    );
  }
  return <p>{formatAuthoringValue(value)}</p>;
}

export function RpgWorldEntityDetail({
  entity,
  imageAssetId,
  onClose,
  onOpenRelated,
  topic,
  worldId,
}: RpgWorldEntityDetailProps) {
  const presentation = entity.presentation;
  const dossier = entity.dossier;
  // Imported bundles may provide curated, multi-paragraph canon as structured
  // entity fields instead of the editable dossier envelope.  The backend
  // projects those fields into legacy dossier sections; they are real reading
  // content and must not be hidden behind the LLM-generation gate.
  const dossierNeedsLlmLore = Boolean(
    dossier?.generated_from_legacy && topic?.source !== 'imported',
  );
  const sections = dossierNeedsLlmLore ? [] : dossier?.sections ?? [];
  const representedFields = new Set<string>();
  for (const highlight of presentation.highlights) representedFields.add(highlight.label.toLowerCase().replace(/\s+/g, '_'));
  for (const group of presentation.groups) representedFields.add(group.label.toLowerCase().replace(/\s+/g, '_'));

  const remainingFields = Object.entries(entity.metadata)
    .filter(([key, value]) => !RESERVED_FIELDS.has(key) && meaningful(value))
    .filter(([key]) => !representedFields.has(key))
    .sort(([left], [right]) => left.localeCompare(right));

  return (
    <div className="rpg-authoring-detail-backdrop is-routed-subpage is-rich-dossier">
      <main
        aria-label={`${entity.title} details`}
        className="rpg-authoring-entity-detail"
      >
        <header className="rpg-authoring-entity-detail-header">
          <div>
            <button className="rpg-authoring-detail-breadcrumb" type="button" onClick={onClose}>← Back to {humanize(entity.card_type || entity.kind)}</button>
            <p className="rpg-authoring-card-eyebrow">{presentation.eyebrow}</p>
            <h2>{entity.title}</h2>
            {!dossierNeedsLlmLore && dossier?.subtitle ? <p className="rpg-authoring-dossier-subtitle">{dossier.subtitle}</p> : null}
          </div>
          <button aria-label={`Close ${entity.title} details`} className="rpg-secondary-button" type="button" onClick={onClose}>Back to collection</button>
        </header>

        <div className="rpg-authoring-entity-detail-hero">
          <div
            className={`rpg-authoring-entity-detail-art${imageAssetId ? ' has-image' : ''}`}
            aria-hidden="true"
            style={imageAssetId ? {
              backgroundImage: `url(${JSON.stringify(assetUrl(imageAssetId))})`,
              backgroundPosition: 'center',
              backgroundRepeat: 'no-repeat',
              backgroundSize: 'cover',
            } : undefined}
          >
            {!imageAssetId ? entity.title.slice(0, 1).toUpperCase() : null}
          </div>
          <div className="rpg-authoring-entity-detail-intro">
            {presentation.badges.length ? (
              <div className="rpg-authoring-card-badges">
                {presentation.badges.map((badge, index) => (
                  <span key={`${formatAuthoringValue(badge)}:${index}`}>{formatAuthoringValue(badge)}</span>
                ))}
              </div>
            ) : null}
            {!dossierNeedsLlmLore ? <p>{entity.short_summary || entity.summary || 'No overview has been written yet.'}</p> : null}
            <small>{humanize(entity.kind)} · {entity.id}</small>
          </div>
        </div>

        {!dossierNeedsLlmLore && dossier?.quote?.text ? (
          <blockquote className="rpg-authoring-dossier-quote">
            <p>“{dossier.quote.text}”</p>
            {dossier.quote.attribution ? <cite><span aria-hidden="true">— </span><span>{dossier.quote.attribution}</span></cite> : null}
          </blockquote>
        ) : null}

        <div className="rpg-authoring-dossier-layout">
          <article className="rpg-authoring-dossier-stream">
            {dossierNeedsLlmLore ? (
              <section className="rpg-authoring-detail-section rpg-authoring-feedback" aria-label="LLM dossier required">
                <h3>LLM-authored lore required</h3>
                <p>This entry's structured canon was accepted, but it has no approved long-form lore yet. Generate a dossier preview, review it, then apply it to publish this as a reading page.</p>
                {topic ? <RpgWorldEntityEditor entity={entity} topic={topic} worldId={worldId} /> : null}
              </section>
            ) : null}
            {dossier?.quick_facts.length ? (
              <section className="rpg-authoring-detail-section rpg-authoring-dossier-facts" id="quick-facts">
                <h3>Quick Facts</h3>
                <dl>
                  {dossier.quick_facts.map((fact, index) => (
                    <div key={`${fact.label}:${index}`}><dt>{fact.label}</dt><dd>{formatAuthoringValue(fact.value)}</dd></div>
                  ))}
                </dl>
              </section>
            ) : null}

            {sections.map((section) => (
              <section className="rpg-authoring-detail-section rpg-authoring-dossier-section" id={section.id} key={section.id}>
                <h3>{section.title}</h3>
                {section.paragraphs.map((paragraph, index) => <p key={`${section.id}:${index}`}>{paragraph}</p>)}
              </section>
            ))}

            {!sections.length && presentation.highlights.length ? (
              <section className="rpg-authoring-detail-section">
                <h3>At a glance</h3>
                <dl className="rpg-authoring-card-highlights">
                  {presentation.highlights.map((highlight) => (
                    <div key={highlight.label}><dt>{highlight.label}</dt><dd>{formatAuthoringValue(highlight.value)}</dd></div>
                  ))}
                </dl>
              </section>
            ) : null}

            {!sections.length ? presentation.groups.map((group) => (
              <section className="rpg-authoring-detail-section" key={group.label}>
                <h3>{group.label}</h3>
                {group.style === 'chips' ? (
                  <div className="rpg-authoring-card-chip-list">
                    {group.items.map((item, index) => (
                      <span key={`${formatAuthoringValue(item)}:${index}`}>{formatAuthoringValue(item)}</span>
                    ))}
                  </div>
                ) : (
                  <DetailValue value={group.items} />
                )}
              </section>
            )) : null}

            {dossier?.related_entity_ids.length ? (
              <section className="rpg-authoring-detail-section rpg-authoring-dossier-related" id="related-entries">
                <h3>Related Entries</h3>
                <div className="rpg-authoring-card-chip-list">
                  {dossier.related_entity_ids.map((entityId) => onOpenRelated ? (
                    <button key={entityId} type="button" onClick={() => onOpenRelated(entityId)}>{humanize(entityId)} →</button>
                  ) : <span key={entityId}>{humanize(entityId)}</span>)}
                </div>
              </section>
            ) : null}

            {remainingFields.length ? (
              <details className="rpg-authoring-detail-section rpg-authoring-dossier-canon-details">
                <summary><h3>Additional details</h3></summary>
                <div className="rpg-authoring-detail-field-grid">
                  {remainingFields.map(([key, value]) => (
                    <article className={`is-${fieldStyle(key, value)}`} key={key}>
                      <h4>{humanize(key)}</h4>
                      {fieldStyle(key, value) === 'chips' && Array.isArray(value) ? (
                        <div className="rpg-authoring-card-chip-list">
                          {value.map((item, index) => (
                            <span key={`${formatAuthoringValue(item)}:${index}`}>{formatAuthoringValue(item)}</span>
                          ))}
                        </div>
                      ) : <DetailValue value={value} />}
                    </article>
                  ))}
                </div>
              </details>
            ) : null}

            {!dossierNeedsLlmLore && topic ? <RpgWorldEntityEditor entity={entity} topic={topic} worldId={worldId} /> : null}
            <details className="rpg-authoring-structured-data">
              <summary>Advanced structured data</summary>
              <pre>{JSON.stringify(entity.metadata, null, 2)}</pre>
            </details>
          </article>

          {sections.length ? (
            <nav className="rpg-authoring-dossier-toc" aria-label={`${entity.title} sections`}>
              <strong>On this page</strong>
              {dossier?.quick_facts.length ? <a href="#quick-facts">Quick Facts</a> : null}
              {sections.map((section) => <a href={`#${section.id}`} key={section.id}>{section.title}</a>)}
              {dossier?.related_entity_ids.length ? <a href="#related-entries">Related Entries</a> : null}
            </nav>
          ) : null}
        </div>
      </main>
    </div>
  );
}
