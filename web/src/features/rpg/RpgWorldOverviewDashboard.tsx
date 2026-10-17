import type { CSSProperties } from 'react';
import type {
  RpgAuthoringDocumentPage,
  RpgAuthoringEntityCard,
  RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import { RpgWorldDocumentBlock } from './RpgWorldDocumentBlocks';
import './RpgWorldOverviewDesign.css';

interface RpgWorldOverviewDashboardProps {
  bannerAssetId?: string;
  onEdit: () => void;
  onOpenSection?: (sectionId: string) => void;
  page: RpgAuthoringDocumentPage;
  sections: RpgAuthoringSection[];
  world: RpgWorldSummary;
}

interface ProgressGroup {
  label: string;
  sectionIds: string[];
}

const PROGRESS_GROUPS: ProgressGroup[] = [
  { label: 'Concept & Foundation', sectionIds: ['overview', 'realm', 'realm_overview', 'cosmology', 'magic_technology'] },
  { label: 'Geography & Regions', sectionIds: ['regions', 'locations', 'points_of_interest', 'map'] },
  { label: 'Races & Peoples', sectionIds: ['races', 'cultures'] },
  { label: 'Factions & Politics', sectionIds: ['factions', 'institutions', 'current_conflicts'] },
  { label: 'Lore & History', sectionIds: ['history', 'calendar', 'pantheon', 'hero_system'] },
  { label: 'Creatures & Bestiary', sectionIds: ['monsters', 'npcs'] },
  { label: 'Magic & Systems', sectionIds: ['classes', 'spells', 'feats', 'items'] },
];

function assetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}

function label(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function projectedEntities(page: RpgAuthoringDocumentPage): RpgAuthoringEntityCard[] {
  return page.related_entities
    .filter((row) => (
      Boolean(row)
      && typeof row === 'object'
      && typeof row.id === 'string'
      && typeof row.title === 'string'
      && typeof row.kind === 'string'
      && typeof row.presentation === 'object'
    ))
    .map((row) => row as unknown as RpgAuthoringEntityCard);
}

function sectionFor(sections: RpgAuthoringSection[], ids: string[]): RpgAuthoringSection | undefined {
  return ids.map((id) => sections.find((section) => section.id === id)).find(Boolean);
}

function relatedGroup(entities: RpgAuthoringEntityCard[], types: string[]): RpgAuthoringEntityCard[] {
  const allowed = new Set(types);
  return entities.filter((entity) => allowed.has(entity.card_type) || allowed.has(entity.kind)).slice(0, 3);
}

function statusIcon(status: string): string {
  if (status === 'complete') return '✓';
  if (status === 'failed') return '!';
  if (status === 'generating') return '↗';
  return '·';
}

export function RpgWorldOverviewDashboard({
  bannerAssetId,
  onEdit,
  onOpenSection,
  page,
  sections,
  world,
}: RpgWorldOverviewDashboardProps) {
  const generated = sections.filter((section) => section.operational_status === 'complete').length;
  const generation = world.generation;
  const progress = generation?.progress ?? {};
  const percent = Number(progress.percent ?? (generated / Math.max(1, sections.length)) * 100);
  const entities = projectedEntities(page);
  const featuredRegions = relatedGroup(entities, ['regions', 'region', 'locations', 'location']);
  const keyFactions = relatedGroup(entities, ['factions', 'faction']);
  const featuredCharacters = relatedGroup(entities, ['npcs', 'npc', 'character']);
  const activity = sections
    .filter((section) => !['empty', 'waiting'].includes(section.operational_status))
    .sort((left, right) => {
      const order = { failed: 0, generating: 1, complete: 2 } as Record<string, number>;
      return (order[left.operational_status] ?? 3) - (order[right.operational_status] ?? 3);
    })
    .slice(0, 5);
  const stats = [
    { label: 'Regions', section: sectionFor(sections, ['regions']) },
    { label: 'Races & Ancestries', section: sectionFor(sections, ['races']) },
    { label: 'Factions', section: sectionFor(sections, ['factions']) },
    { label: 'Classes', section: sectionFor(sections, ['classes']) },
    { label: 'Quests', section: sectionFor(sections, ['quests']) },
    { label: 'Lore Topics', value: sections.filter((section) => section.group === 'lore').length },
  ];
  const progressStyle = {
    '--world-progress': `${Math.max(0, Math.min(100, percent)) * 3.6}deg`,
  } as CSSProperties;

  return (
    <section className="rpg-authoring-page rpg-world-overview-dashboard is-mockup-layout">
      <div
        className={`rpg-world-overview-hero${bannerAssetId ? ' has-image' : ''}`}
        style={bannerAssetId ? {
          backgroundImage: `linear-gradient(90deg, color-mix(in srgb, var(--c-blue-130) 96%, transparent), color-mix(in srgb, var(--c-blue-130) 30%, transparent)), url(${JSON.stringify(assetUrl(bannerAssetId))})`,
        } : undefined}
      >
        <div className="rpg-world-overview-identity">
          <span className="rpg-world-overview-emblem" aria-hidden="true">✥</span>
          <div>
            <p className="eyebrow">World dossier</p>
            <h2>{world.title}</h2>
            <p>{world.description || page.summary || 'No world summary has been written yet.'}</p>
            <div className="rpg-world-overview-badges">
              <span>{label(world.genre)}</span><span>{label(world.tone)}</span><span>{label(world.status)}</span>
            </div>
            <div className="rpg-world-overview-publication"><span className="is-live" />{label(world.status)}<span aria-hidden="true">·</span>Last updated {new Date(world.updated_at).toLocaleString()}</div>
          </div>
        </div>
        <button className="rpg-secondary-button" type="button" onClick={onEdit}>Edit world details</button>
      </div>

      <div role="group" className="rpg-world-overview-stat-strip" aria-label="World statistics">
        {stats.map((stat) => (
          <button key={stat.label} type="button" onClick={() => stat.section && onOpenSection?.(stat.section.id)} disabled={!stat.section}>
            <strong>{stat.value ?? stat.section?.entity_count ?? 0}</strong>
            <span>{stat.label}</span>
          </button>
        ))}
      </div>

      <div className="rpg-world-overview-primary-grid">
        <section className="rpg-world-overview-panel">
          <div className="rpg-world-overview-panel-heading"><h3>Featured Regions</h3><button type="button" onClick={() => onOpenSection?.('regions')}>View all</button></div>
          <div className="rpg-world-overview-feature-list">
            {featuredRegions.map((entity) => <article key={entity.id}><span className="rpg-world-overview-mini-art">{entity.title.slice(0, 1)}</span><div><strong>{entity.title}</strong><p>{entity.summary}</p></div></article>)}
            {!featuredRegions.length ? <p className="rpg-world-overview-empty">Related region entries appear here as canon links are generated.</p> : null}
          </div>
        </section>

        <section className="rpg-world-overview-panel">
          <div className="rpg-world-overview-panel-heading"><h3>Key Factions</h3><button type="button" onClick={() => onOpenSection?.('factions')}>View all</button></div>
          <div className="rpg-world-overview-feature-list">
            {keyFactions.map((entity) => <article key={entity.id}><span className="rpg-world-overview-roundel">{entity.title.slice(0, 1)}</span><div><strong>{entity.title}</strong><p>{entity.summary}</p></div></article>)}
            {!keyFactions.length ? <p className="rpg-world-overview-empty">Related factions appear here when the realm dossier references them.</p> : null}
          </div>
        </section>

        <section className="rpg-world-overview-panel">
          <div className="rpg-world-overview-panel-heading"><h3>Featured Characters</h3><button type="button" onClick={() => onOpenSection?.('npcs')}>View all</button></div>
          <div className="rpg-world-overview-feature-list">
            {featuredCharacters.map((entity) => <article key={entity.id}><span className="rpg-world-overview-avatar">{entity.title.slice(0, 1)}</span><div><strong>{entity.title}</strong><p>{entity.summary}</p></div></article>)}
            {!featuredCharacters.length ? <p className="rpg-world-overview-empty">Central characters appear here when generated world canon links them.</p> : null}
          </div>
        </section>

        <section className="rpg-world-overview-panel">
          <div className="rpg-world-overview-panel-heading"><h3>Recent World Activity</h3><button type="button" onClick={() => onOpenSection?.('generation')}>View all</button></div>
          <div className="rpg-world-overview-activity-list">
            {activity.map((section) => <button key={section.id} type="button" onClick={() => onOpenSection?.(section.id)}><span className={`is-${section.operational_status}`}>{statusIcon(section.operational_status)}</span><div><strong>{section.label}</strong><small>{label(section.operational_status)} · {section.entity_count} entries</small></div></button>)}
          </div>
        </section>
      </div>

      <div className="rpg-world-overview-secondary-grid">
        <section className="rpg-world-overview-panel rpg-world-overview-generation-card">
          <div className="rpg-world-overview-panel-heading"><h3>World Generation</h3><span>{Math.round(percent)}%</span></div>
          <div className="rpg-world-overview-generation-content">
            <div className="rpg-world-overview-ring" style={progressStyle}><strong>{Math.round(percent)}%</strong><small>Complete</small></div>
            <div className="rpg-world-overview-progress-groups">
              {PROGRESS_GROUPS.map((group) => {
                const rows = sections.filter((section) => group.sectionIds.includes(section.id));
                const completed = rows.filter((section) => section.operational_status === 'complete').length;
                const groupPercent = rows.length ? completed / rows.length * 100 : 0;
                return <div key={group.label}><span>{group.label}</span><div><i style={{ width: `${groupPercent}%` }} /></div><small>{completed}/{rows.length}</small></div>;
              })}
            </div>
          </div>
          <button type="button" onClick={() => onOpenSection?.('generation')}>Continue World Generation →</button>
        </section>

        <section className="rpg-world-overview-panel rpg-world-overview-dossier-card">
          <div className="rpg-world-overview-panel-heading"><h3>World Dossier</h3><span>{page.body.length} sections</span></div>
          <div className="rpg-world-overview-dossier-preview">
            {page.body.length ? page.body.slice(0, 3).map((block, index) => <RpgWorldDocumentBlock block={block} key={`${block.kind}:${index}`} />) : <p>No overview canon has been generated.</p>}
          </div>
        </section>

        <aside className="rpg-world-overview-panel rpg-world-overview-summary-card">
          <div className="rpg-world-overview-panel-heading"><h3>World Summary</h3></div>
          <blockquote>{world.description || page.summary || 'No world summary has been written yet.'}</blockquote>
          <dl>
            <div><dt>Primary theme</dt><dd>{label(world.genre)}</dd></div>
            <div><dt>Tone</dt><dd>{label(world.tone)}</dd></div>
            <div><dt>Status</dt><dd>{label(world.status)}</dd></div>
            <div><dt>Draft revision</dt><dd>{world.draft_revision}</dd></div>
            <div><dt>Generation</dt><dd>{label(generation?.status || 'not started')}</dd></div>
          </dl>
          <button type="button" onClick={onEdit}>Edit World Details ✎</button>
        </aside>
      </div>
    </section>
  );
}
