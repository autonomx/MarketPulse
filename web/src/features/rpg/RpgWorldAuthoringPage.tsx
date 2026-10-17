/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type {
  RpgAuthoringDocumentPage,
  RpgAuthoringEntityCard,
  RpgAuthoringPage,
  RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import { rpgWorldImageClient } from './api/rpgWorldImageClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import {
  documentAnchors,
  presentLoreBlocks,
} from './RpgWorldCompletionModels';
import { RpgWorldEntityCard } from './RpgWorldEntityCard';
import { RpgWorldEntityDetail } from './RpgWorldEntityDetail';
import { RpgWorldLoreLayout } from './RpgWorldLoreLayout';
import { RpgWorldOverviewDashboard } from './RpgWorldOverviewDashboard';
import { RpgWorldTopicEditor } from './RpgWorldTopicEditor';
import './RpgWorldAuthoringPage.css';

interface RpgWorldAuthoringPageProps {
  error?: string;
  isLoading: boolean;
  isSaving: boolean;
  onOpenEntity?: (sectionId: string, entityId: string) => void;
  onOpenSection?: (sectionId: string) => void;
  onSaveWorld: (changes: Record<string, unknown>) => void;
  onSelectEntity?: (entityId: string | null) => void;
  page?: RpgAuthoringPage;
  section: RpgAuthoringSection;
  sections?: RpgAuthoringSection[];
  selectedEntityId?: string | null;
  world: RpgWorldSummary;
  worldId: string;
}

const ENTITY_SECTION_ALIASES: Record<string, string> = {
  class: 'classes',
  encounter: 'encounter_seeds',
  encounter_seed: 'encounter_seeds',
  faction: 'factions',
  feat: 'feats',
  institution: 'institutions',
  item: 'items',
  location: 'locations',
  monster: 'monsters',
  npc: 'npcs',
  one_shot: 'one_shots',
  opening: 'opening_scenarios',
  opening_scenario: 'opening_scenarios',
  poi: 'points_of_interest',
  point_of_interest: 'points_of_interest',
  quest: 'quests',
  race: 'races',
  region: 'regions',
  spell: 'spells',
};

function humanize(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function text(value: unknown, fallback = ''): string {
  return value == null || String(value).trim() === '' ? fallback : String(value).trim();
}

function meaningful(value: unknown): boolean {
  if (value == null || value === '') return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length > 0;
  return true;
}

function assetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}

function entitySectionId(entityId: string, sections: RpgAuthoringSection[]): string {
  const prefix = entityId.split(':', 1)[0].trim().toLowerCase();
  const alias = ENTITY_SECTION_ALIASES[prefix] ?? prefix;
  const matched = sections.find((candidate) => (
    candidate.id === alias
    || candidate.entity_kind === prefix
    || candidate.entity_kind === alias.replace(/s$/, '')
  ));
  return matched?.id ?? alias;
}

function relatedEntityCards(page: RpgAuthoringDocumentPage): RpgAuthoringEntityCard[] {
  const projected = page.related_entities
    .filter((row) => (
      Boolean(row)
      && typeof row === 'object'
      && typeof row.id === 'string'
      && typeof row.title === 'string'
      && typeof row.presentation === 'object'
    ))
    .map((row) => row as unknown as RpgAuthoringEntityCard);
  if (projected.length) return projected;

  const content = page.topic?.content ?? {};
  const entities = Array.isArray(content.entities)
    ? content.entities.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    : [];
  return entities.map((metadata, index) => {
    const kind = text(metadata.kind, page.section_id.replace(/s$/, '') || 'entity');
    const id = text(metadata.id ?? metadata.entity_id, `${kind}:${index + 1}`);
    const idParts = id.split(':');
    const title = text(metadata.name ?? metadata.title ?? metadata.label, humanize(idParts[idParts.length - 1] || id));
    const summary = text(
      metadata.short_summary
      ?? metadata.summary
      ?? metadata.description
      ?? metadata.personality
      ?? metadata.sensory_profile
      ?? metadata.purpose
      ?? metadata.premise,
      'No overview has been written yet.',
    );
    const badges = [metadata.featured ? 'Featured' : undefined, metadata.visibility, metadata.status, metadata.dossier_status].filter(meaningful);
    const highlights = Object.entries(metadata)
      .filter(([key, value]) => !['id', 'entity_id', 'name', 'title', 'label', 'kind', 'summary', 'short_summary', 'description', 'dossier', 'visibility', 'status', 'dossier_status', 'featured'].includes(key) && meaningful(value))
      .filter(([, value]) => !Array.isArray(value) && typeof value !== 'object')
      .slice(0, 3)
      .map(([key, value]) => ({ label: humanize(key), value }));
    const groups = Object.entries(metadata)
      .filter(([, value]) => Array.isArray(value) && value.length)
      .slice(0, 5)
      .map(([key, value]) => ({
        label: humanize(key),
        items: value as unknown[],
        style: /(_ids|_refs|tags|languages|regions|locations|factions|classes)$/.test(key) ? 'chips' : 'list',
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
      card_type: kind,
      presentation: {
        variant: kind,
        eyebrow: humanize(kind),
        badges,
        highlights,
        groups,
      },
      metadata,
    };
  });
}

export function RpgWorldAuthoringPage({
  error,
  isLoading,
  isSaving,
  onOpenEntity,
  onOpenSection,
  onSaveWorld,
  onSelectEntity,
  page,
  section,
  sections = [],
  selectedEntityId: controlledSelectedEntityId,
  world,
  worldId,
}: RpgWorldAuthoringPageProps) {
  const [isEditingOverview, setIsEditingOverview] = useState(false);
  const [title, setTitle] = useState(world.title);
  const [description, setDescription] = useState(world.description);
  const [genre, setGenre] = useState(world.genre);
  const [tone, setTone] = useState(world.tone);
  const [seed, setSeed] = useState(world.seed);
  const [collectionSearch, setCollectionSearch] = useState('');
  const [collectionKind, setCollectionKind] = useState('all');
  const [collectionSort, setCollectionSort] = useState('name');
  const [collectionView, setCollectionView] = useState<'grid' | 'list'>('grid');
  const [localSelectedEntityId, setLocalSelectedEntityId] = useState<string | null>(null);
  const selectedEntityId = controlledSelectedEntityId === undefined ? localSelectedEntityId : controlledSelectedEntityId;
  const setSelectedEntityId = (value: string | null) => {
    if (onSelectEntity) onSelectEntity(value);
    else setLocalSelectedEntityId(value);
  };
  const imagesQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-image-targets', worldId],
    queryFn: () => rpgWorldImageClient.list(worldId),
    enabled: Boolean(page) && section.id !== 'images',
    staleTime: 10_000,
  });
  // Prompt or canon edits can make a target stale while its replacement is
  // pending. Preserve the last active artwork throughout that review gap;
  // only an explicit rejection should remove it from the world presentation.
  const displayableTargets = (imagesQuery.data?.targets ?? [])
    .filter((target) => target.review_state !== 'rejected' && target.active_asset_id);
  const displayableAssets = useMemo(() => new Map(
    displayableTargets.map((target) => [target.entity_id, String(target.active_asset_id)]),
  ), [displayableTargets]);
  const bannerAssetId = displayableTargets.find((target) => target.role === 'banner')?.active_asset_id
    ?? displayableTargets.find((target) => target.role === 'cover')?.active_asset_id
    ?? undefined;
  const collectionEntities = page?.page_kind === 'collection' ? page.entities : [];
  const documentEntities = page?.page_kind === 'document' ? relatedEntityCards(page) : [];
  const allPageEntities = page?.page_kind === 'collection' ? collectionEntities : documentEntities;
  const selectedEntity = allPageEntities.find((entity) => entity.id === selectedEntityId);
  const collectionKinds = useMemo(
    () => Array.from(new Set(collectionEntities.map((entity) => entity.kind))).sort(),
    [collectionEntities],
  );
  const visibleEntities = useMemo(() => {
    const query = collectionSearch.trim().toLowerCase();
    return [...collectionEntities]
      .filter((entity) => collectionKind === 'all' || entity.kind === collectionKind)
      .filter((entity) => !query || [entity.title, entity.summary, entity.kind, entity.id]
        .some((value) => value.toLowerCase().includes(query)))
      .sort((left, right) => {
        if (collectionSort === 'featured') {
          const featured = Number(Boolean(right.metadata.featured)) - Number(Boolean(left.metadata.featured));
          if (featured) return featured;
        }
        if (collectionSort === 'type') {
          const kindOrder = left.kind.localeCompare(right.kind);
          if (kindOrder) return kindOrder;
        }
        return left.title.localeCompare(right.title);
      });
  }, [collectionEntities, collectionKind, collectionSearch, collectionSort]);

  const openRelatedEntity = (entityId: string) => {
    const targetSectionId = entitySectionId(entityId, sections);
    if (targetSectionId === section.id && allPageEntities.some((entity) => entity.id === entityId)) {
      setSelectedEntityId(entityId);
      return;
    }
    if (onOpenEntity) {
      onOpenEntity(targetSectionId, entityId);
      return;
    }
    onOpenSection?.(targetSectionId);
  };

  useEffect(() => {
    setTitle(world.title);
    setDescription(world.description);
    setGenre(world.genre);
    setTone(world.tone);
    setSeed(world.seed);
  }, [world]);

  useEffect(() => {
    setCollectionSearch('');
    setCollectionKind('all');
    setCollectionSort('name');
    setCollectionView('grid');
    if (controlledSelectedEntityId === undefined) setLocalSelectedEntityId(null);
  }, [controlledSelectedEntityId, section.id]);

  useEffect(() => {
    if (
      selectedEntityId
      && page
      && page.section_id === section.id
      && !selectedEntity
      && !isLoading
    ) setSelectedEntityId(null);
  }, [isLoading, page, section.id, selectedEntity, selectedEntityId]);

  if (isLoading) return <section className="rpg-authoring-page"><h2>{section.label}</h2><p>Loading world content…</p></section>;
  if (error) return <section className="rpg-authoring-page"><h2>{section.label}</h2><p className="rpg-world-catalog-error">{error}</p></section>;

  if (section.id === 'overview' && isEditingOverview) {
    return (
      <section className="rpg-authoring-page">
        <p className="eyebrow">Workspace</p>
        <div className="rpg-authoring-page-heading"><h2>Edit World Overview</h2><button className="rpg-secondary-button" type="button" onClick={() => setIsEditingOverview(false)}>Cancel</button></div>
        <form className="rpg-authoring-overview-form" onSubmit={(event) => {
          event.preventDefault();
          onSaveWorld({ title, description, genre, tone, seed });
          setIsEditingOverview(false);
        }}>
          <label><span>Title</span><input required value={title} onChange={(event) => setTitle(event.currentTarget.value)} /></label>
          <label><span>Description</span><textarea rows={5} value={description} onChange={(event) => setDescription(event.currentTarget.value)} /></label>
          <label><span>Genre</span><input value={genre} onChange={(event) => setGenre(event.currentTarget.value)} /></label>
          <label><span>Tone</span><input value={tone} onChange={(event) => setTone(event.currentTarget.value)} /></label>
          <label><span>Seed</span><input type="number" value={seed} onChange={(event) => setSeed(Number(event.currentTarget.value))} /></label>
          <button type="submit" disabled={isSaving}>{isSaving ? 'Saving…' : 'Save World'}</button>
        </form>
      </section>
    );
  }

  if (!page) {
    return <section className="rpg-authoring-page"><h2>{section.label}</h2><div className="rpg-authoring-empty"><h3>Not generated yet</h3><p>This canonical section is available in the authoring roadmap and will populate when it is generated.</p></div></section>;
  }

  if (selectedEntity) {
    return (
      <RpgWorldEntityDetail
        entity={selectedEntity}
        imageAssetId={displayableAssets.get(selectedEntity.id)}
        onClose={() => setSelectedEntityId(null)}
        onOpenRelated={openRelatedEntity}
        topic={selectedEntity.metadata.lore_origin === 'gameplay' ? undefined : page.topic}
        worldId={worldId}
      />
    );
  }

  if (section.id === 'overview' && page.page_kind === 'document') {
    return (
      <RpgWorldOverviewDashboard
        bannerAssetId={bannerAssetId ? String(bannerAssetId) : undefined}
        onEdit={() => setIsEditingOverview(true)}
        onOpenSection={onOpenSection}
        page={page}
        sections={sections}
        world={world}
      />
    );
  }

  if (page.page_kind === 'collection') {
    return (
      <section className="rpg-authoring-page">
        <div className="rpg-authoring-page-heading"><div><p className="eyebrow">{section.group}</p><h2>{page.title || section.label}</h2><p className="rpg-authoring-page-summary">Browse the collection, then open an entry for its complete illustrated dossier.</p></div><span>{visibleEntities.length === page.entities.length ? page.entities.length : `${visibleEntities.length} of ${page.entities.length}`} entr{page.entities.length === 1 ? 'y' : 'ies'}</span></div>
        {page.entities.length ? (
          <div className="rpg-authoring-collection-toolbar">
            <input aria-label={`Search ${page.title || section.label}`} placeholder={`Search ${page.title || section.label.toLowerCase()}…`} value={collectionSearch} onChange={(event) => setCollectionSearch(event.currentTarget.value)} />
            <select aria-label={`Filter ${page.title || section.label} by type`} value={collectionKind} onChange={(event) => setCollectionKind(event.currentTarget.value)}>
              <option value="all">All types</option>
              {collectionKinds.map((kind) => <option key={kind} value={kind}>{humanize(kind)}</option>)}
            </select>
            <select aria-label={`Sort ${page.title || section.label}`} value={collectionSort} onChange={(event) => setCollectionSort(event.currentTarget.value)}>
              <option value="name">Sort by name</option>
              <option value="featured">Featured first</option>
              <option value="type">Sort by type</option>
            </select>
            <div role="group" className="rpg-authoring-collection-view-toggle" aria-label="Collection view">
              <button className={collectionView === 'grid' ? 'is-active' : ''} type="button" onClick={() => setCollectionView('grid')}>Grid</button>
              <button className={collectionView === 'list' ? 'is-active' : ''} type="button" onClick={() => setCollectionView('list')}>List</button>
            </div>
          </div>
        ) : null}
        {!page.entities.length ? <div className="rpg-authoring-empty"><h3>No entries yet</h3><p>Generate this section to add structured world entities.</p></div> : null}
        {page.entities.length && !visibleEntities.length ? <div className="rpg-authoring-empty"><h3>No matching entries</h3><p>Clear the search or choose a different type.</p></div> : null}
        <div className={`rpg-authoring-entity-grid is-${collectionView}`}>
          {visibleEntities.map((entity) => (
            <RpgWorldEntityCard
              entity={entity}
              imageAssetId={displayableAssets.get(entity.id)}
              key={entity.id}
              onOpen={() => setSelectedEntityId(entity.id)}
              topic={page.topic}
              worldId={worldId}
            />
          ))}
        </div>
        {page.topic ? <RpgWorldTopicEditor topic={page.topic} worldId={worldId} /> : null}
      </section>
    );
  }

  const blocks = presentLoreBlocks(section.id, page.body);
  const toc = documentAnchors(blocks);
  const heroStyle = bannerAssetId ? {
    backgroundImage: `url(${JSON.stringify(assetUrl(String(bannerAssetId)))})`,
  } : undefined;

  return (
    <section className="rpg-authoring-page">
      <RpgWorldLoreLayout
        blocks={blocks}
        heroStyle={heroStyle}
        sectionId={section.id}
        summary={page.summary || `${section.label} is presented as a readable, vertically scrolling lore document.`}
        title={page.title || section.label}
        toc={toc}
      >
        {documentEntities.length ? (
          <section className="rpg-authoring-related-entities">
            <div className="rpg-authoring-page-heading"><div><p className="eyebrow">Connected canon</p><h3>Related entries</h3></div><span>{documentEntities.length}</span></div>
            <div className="rpg-authoring-entity-grid">
              {documentEntities.map((entity) => (
                <RpgWorldEntityCard
                  entity={entity}
                  imageAssetId={displayableAssets.get(entity.id)}
                  key={entity.id}
                  onOpen={() => setSelectedEntityId(entity.id)}
                  topic={page.topic}
                  worldId={worldId}
                />
              ))}
            </div>
          </section>
        ) : null}
        {page.topic ? <RpgWorldTopicEditor topic={page.topic} worldId={worldId} /> : null}
      </RpgWorldLoreLayout>
    </section>
  );
}
