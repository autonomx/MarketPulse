/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldAuthoringClient,
  type RpgAuthoringEntityCard,
  type RpgAuthoringGroup,
  type RpgAuthoringPage,
  type RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import { RpgWorldAdvancedPanel } from './RpgWorldAdvancedPanel';
import { RpgWorldAuthoringPage } from './RpgWorldAuthoringPage';
import {
  completeAuthoringSections,
  parseWorldEditorRoute,
  pushWorldEditorRoute,
} from './RpgWorldCompletionModels';
import { RpgWorldGenerationDashboard } from './RpgWorldGenerationDashboard';
import { RpgWorldImagesPanel } from './RpgWorldImagesPanel';
import { RpgWorldScenarioAuthoringPanel } from './RpgWorldScenarioAuthoringPanel';
import { RpgWorldVisualMapPanel } from './RpgWorldVisualMapPanel';
import './RpgWorldFinalizedDesign.css';
import './RpgWorldCompletionDesign.css';
import './RpgWorldShellDesign.css';
import './RpgWorldShellInteractions.css';

interface RpgWorldEditorShellProps {
  backLabel?: string;
  onBack: () => void;
  onPlay: () => void;
  runtimeLoreCards?: RpgAuthoringEntityCard[];
  world: RpgWorldSummary;
  worldId: string;
}

const GROUPS: Array<{ id: RpgAuthoringGroup; label: string }> = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'world', label: 'World' },
  { id: 'lore', label: 'Lore' },
  { id: 'game-master', label: 'Game Master' },
];

const DEDICATED_SECTIONS = ['advanced', 'generation', 'images', 'map', 'scenarios'];

const SECTION_ICONS: Record<string, string> = {
  overview: '◉',
  generation: '⚙',
  images: '▧',
  map: '⌖',
  regions: '◈',
  races: '✥',
  factions: '♜',
  classes: '✣',
  spells: '✦',
  feats: '✧',
  locations: '⌂',
  points_of_interest: '◇',
  monsters: '♢',
  items: '♧',
  realm: '⌂',
  realm_overview: '⌂',
  cosmology: '◊',
  magic_technology: '⋈',
  history: '▣',
  calendar: '◷',
  cultures: '♙',
  institutions: '▤',
  pantheon: '✺',
  hero_system: '✵',
  current_conflicts: '⚔',
  npcs: '♟',
  quests: '✎',
  scenarios: '▶',
  advanced: '⋯',
};

function statusLabel(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function worldState(world: RpgWorldSummary): string {
  const generation = world.generation;
  if (world.status === 'archived') return 'Archived';
  if (generation?.status === 'running' || generation?.status === 'planned') return 'Generating';
  if (generation?.status === 'failed') return 'Generation failed';
  return world.status === 'published' ? 'Published' : 'Draft';
}

function icon(sectionId: string): string {
  return SECTION_ICONS[sectionId] ?? SECTION_ICONS[sectionId.replace(/s$/, '')] ?? '✦';
}

const RUNTIME_TOPIC_SECTION_ALIASES: Record<string, string[]> = {
  actors: ['actors', 'npcs', 'characters'],
  equipment_vehicles: ['equipment_vehicles', 'items'],
  groups: ['groups', 'factions', 'institutions'],
  places: ['places', 'locations'],
  roles_archetypes: ['roles_archetypes', 'classes'],
  technology_augmentations: ['technology_augmentations', 'items'],
  threats: ['threats', 'monsters'],
  networks: ['networks'],
  quests: ['quests'],
  regions: ['regions'],
  cultures: ['cultures'],
};

function sectionAcceptsRuntimeCard(
  section: RpgAuthoringSection,
  card: RpgAuthoringEntityCard,
): boolean {
  const topicId = String(card.metadata.lore_topic_id ?? card.card_type);
  const aliases = RUNTIME_TOPIC_SECTION_ALIASES[topicId] ?? [topicId];
  const sectionIds = new Set([section.id, ...section.topic_ids]);
  if (aliases.some((candidate) => sectionIds.has(candidate))) return true;
  const entityKind = section.entity_kind?.toLowerCase();
  return Boolean(entityKind) && [card.kind, card.card_type]
    .some((candidate) => candidate.toLowerCase() === entityKind);
}

export function mergeRuntimeLoreCards(
  page: RpgAuthoringPage | undefined,
  section: RpgAuthoringSection,
  runtimeCards: RpgAuthoringEntityCard[],
): RpgAuthoringPage | undefined {
  if (!page || page.page_kind !== 'collection') return page;
  const cards = new Map(page.entities.map((card) => [card.id.toLowerCase(), card]));
  for (const card of runtimeCards.filter((candidate) => (
    sectionAcceptsRuntimeCard(section, candidate)
  ))) {
    const key = card.id.toLowerCase();
    if (!cards.has(key)) cards.set(key, card);
  }
  return { ...page, entities: Array.from(cards.values()) };
}

export function RpgWorldEditorShell({
  backLabel = 'Back to Worlds',
  onBack,
  onPlay,
  runtimeLoreCards = [],
  world,
  worldId,
}: RpgWorldEditorShellProps) {
  const initialRoute = parseWorldEditorRoute();
  const queryClient = useQueryClient();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const notificationsRef = useRef<HTMLElement>(null);
  const [sectionId, setSectionId] = useState(
    initialRoute?.worldId === worldId ? initialRoute.sectionId : 'overview',
  );
  const [entityId, setEntityId] = useState<string | null>(
    initialRoute?.worldId === worldId ? initialRoute.entityId ?? null : null,
  );
  const manifestQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId],
    queryFn: () => rpgWorldAuthoringClient.manifest(worldId),
    refetchInterval: 5000,
  });
  const rawSections = manifestQuery.data?.sections ?? [];
  const sections = useMemo(() => completeAuthoringSections(rawSections), [rawSections]);
  const rawSectionIds = useMemo(() => new Set(rawSections.map((section) => section.id)), [rawSections]);
  const selectedSection = sections.find((section) => section.id === sectionId)
    ?? sections.find((section) => section.id === 'overview')
    ?? ({
      id: sectionId,
      label: statusLabel(sectionId),
      group: 'workspace',
      page_kind: 'document',
      topic_ids: [],
      dependencies: [],
      required_before_launch: false,
      supports_generation: false,
      supports_images: false,
      supports_entity_editing: false,
      operational_status: 'empty',
      editorial_status: 'unreviewed',
      entity_count: 0,
    } satisfies RpgAuthoringSection);
  const pageQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-authoring-section', worldId, selectedSection.id],
    queryFn: () => rpgWorldAuthoringClient.section(worldId, selectedSection.id),
    enabled: Boolean(worldId)
      && !DEDICATED_SECTIONS.includes(selectedSection.id)
      && rawSectionIds.has(selectedSection.id),
    refetchInterval: selectedSection.operational_status === 'generating' ? 3000 : false,
  });
  const page = useMemo(
    () => mergeRuntimeLoreCards(
      pageQuery.data,
      selectedSection,
      runtimeLoreCards,
    ),
    [pageQuery.data, runtimeLoreCards, selectedSection],
  );

  const navigate = (nextSectionId: string, nextEntityId: string | null = null, replace = false) => {
    setSectionId(nextSectionId);
    setEntityId(nextEntityId);
    setSearchOpen(false);
    setNotificationsOpen(false);
    pushWorldEditorRoute({ worldId, sectionId: nextSectionId, entityId: nextEntityId ?? undefined }, replace);
  };

  useEffect(() => {
    const syncRoute = () => {
      const route = parseWorldEditorRoute();
      if (!route || route.worldId !== worldId) return;
      setSectionId(route.sectionId || 'overview');
      setEntityId(route.entityId ?? null);
    };
    window.addEventListener('popstate', syncRoute);
    window.addEventListener('hashchange', syncRoute);
    return () => {
      window.removeEventListener('popstate', syncRoute);
      window.removeEventListener('hashchange', syncRoute);
    };
  }, [worldId]);

  useEffect(() => {
    if (!notificationsOpen) return undefined;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNotificationsOpen(false);
    };
    const dismissOutside = (event: PointerEvent) => {
      if (!notificationsRef.current?.contains(event.target as Node)) {
        setNotificationsOpen(false);
      }
    };
    window.addEventListener('keydown', dismiss);
    window.addEventListener('pointerdown', dismissOutside);
    return () => {
      window.removeEventListener('keydown', dismiss);
      window.removeEventListener('pointerdown', dismissOutside);
    };
  }, [notificationsOpen]);

  useEffect(() => {
    if (sections.length && !sections.some((section) => section.id === sectionId)) {
      navigate(sections[0].id, null, true);
    }
  }, [sectionId, sections]);

  useEffect(() => {
    const route = parseWorldEditorRoute();
    if (!route || route.worldId !== worldId) navigate(sectionId, entityId, true);
  }, [worldId]);

  const updateWorld = useMutation({
    mutationFn: (changes: Record<string, unknown>) => rpgWorldAuthoringClient.updateWorld(worldId, {
      expected_draft_revision: (manifestQuery.data?.world ?? world).draft_revision,
      ...changes,
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-workspace'] }),
      ]);
    },
  });

  const grouped = useMemo(() => new Map(GROUPS.map((group) => [
    group.id,
    sections.filter((section) => section.group === group.id),
  ])), [sections]);
  const currentWorld = manifestQuery.data?.world ?? world;
  const selectedGroup = GROUPS.find((group) => group.id === selectedSection.group)?.label ?? statusLabel(selectedSection.group);
  const profileInitial = currentWorld.title.trim().slice(0, 1).toUpperCase() || 'W';
  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return sections.slice(0, 8);
    return sections.filter((candidate) => [candidate.label, candidate.id, candidate.group]
      .some((value) => String(value).toLowerCase().includes(query))).slice(0, 12);
  }, [searchQuery, sections]);
  const attentionSections = sections.filter((candidate) => (
    candidate.operational_status === 'failed'
    || candidate.operational_status === 'stale'
    || candidate.editorial_status === 'needs_review'
  ));
  const generation = manifestQuery.data?.generation;
  const generationRun = generation && 'run_id' in generation ? generation : undefined;
  const notificationCount = attentionSections.length + (generationRun?.status === 'failed' ? 1 : 0);

  return (
    <section className={`rpg-authoring-editor rpg-world-product-shell${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`} aria-label="World editor">
      <div className="rpg-world-shell-layout">
        <aside className="rpg-world-sidebar">
          <div role="group" className="rpg-world-brand" aria-label="Worlds and Campaigns">
            <span className="rpg-world-brand-mark">✥</span>
            <strong><span>Worlds &amp;</span><span>Campaigns</span></strong>
          </div>
          <nav aria-label="World editor sections">
            {GROUPS.map((group) => {
              const rows = grouped.get(group.id) ?? [];
              if (!rows.length) return null;
              return (
                <section key={group.id}>
                  <h4>{group.label}</h4>
                  {rows.map((section) => (
                    <button
                      aria-current={selectedSection.id === section.id ? 'page' : undefined}
                      className={selectedSection.id === section.id ? 'is-active' : ''}
                      key={section.id}
                      title={sidebarCollapsed ? section.label : undefined}
                      type="button"
                      onClick={() => navigate(section.id)}
                    >
                      <span className="rpg-world-nav-icon" aria-hidden="true">{icon(section.id)}</span>
                      <span className="rpg-world-nav-copy"><span>{section.label}</span><small>{statusLabel(section.operational_status)}{section.entity_count ? ` · ${section.entity_count}` : ''}</small></span>
                    </button>
                  ))}
                </section>
              );
            })}
            {manifestQuery.isPending ? <p>Loading sections…</p> : null}
            {manifestQuery.isError ? <p className="rpg-world-catalog-error">Unable to load authoring manifest.</p> : null}
          </nav>
          <button
            aria-expanded={!sidebarCollapsed}
            className="rpg-world-sidebar-collapse"
            type="button"
            onClick={() => setSidebarCollapsed((value) => !value)}
          >
            <span aria-hidden="true">{sidebarCollapsed ? '›' : '‹'}</span>
            <span>{sidebarCollapsed ? 'Expand' : 'Collapse'}</span>
          </button>
        </aside>

        <div className="rpg-world-main-shell">
          <header className="rpg-authoring-editor-header rpg-world-topbar">
            <nav className="rpg-world-breadcrumbs" aria-label="Breadcrumb">
              <button type="button" onClick={onBack}>← {backLabel}</button>
              <span>›</span>
              <button type="button" onClick={() => navigate('overview')}>{currentWorld.title}</button>
              <span>›</span>
              <span>{selectedGroup}</span>
              <span>›</span>
              <strong>{selectedSection.label}</strong>
              {entityId ? <><span>›</span><strong>{statusLabel(entityId.split(':').slice(-1)[0])}</strong></> : null}
            </nav>
            <div className="rpg-world-topbar-actions">
              <button aria-expanded={searchOpen} aria-label="Search world" className="rpg-world-icon-button" type="button" onClick={() => { setSearchOpen((value) => !value); setNotificationsOpen(false); }}>⌕</button>
              <button aria-expanded={notificationsOpen} aria-label="Notifications" className="rpg-world-icon-button rpg-world-notification-button" type="button" onClick={() => { setNotificationsOpen((value) => !value); setSearchOpen(false); }}>♧{notificationCount ? <b>{notificationCount}</b> : null}</button>
              <span className="rpg-world-topbar-divider" />
              <span className="rpg-world-profile-avatar" aria-hidden="true">{profileInitial}</span>
              <span className="rpg-world-profile-copy"><strong>Lorekeeper</strong><small>Game Master</small></span>
              <span className={`rpg-world-state is-${worldState(currentWorld).toLowerCase().replace(/\s+/g, '-')}`}>{worldState(currentWorld)}</span>
              <button className="rpg-world-play-button" type="button" onClick={onPlay}>Play</button>
            </div>
          </header>

          {searchOpen ? (
            <aside className="rpg-world-command-popover is-search" aria-label="Search world sections">
              <label><span>Search this world</span><input autoFocus value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} placeholder="Regions, factions, history…" /></label>
              <div>
                {searchResults.map((candidate) => <button key={candidate.id} type="button" onClick={() => navigate(candidate.id)}><span>{icon(candidate.id)}</span><strong>{candidate.label}</strong><small>{statusLabel(candidate.group)} · {statusLabel(candidate.operational_status)}</small></button>)}
                {!searchResults.length ? <p>No matching sections.</p> : null}
              </div>
            </aside>
          ) : null}

          {notificationsOpen ? (
            <aside className="rpg-world-command-popover is-notifications" aria-label="World notifications" ref={notificationsRef}>
              <header><strong>World activity</strong><span>{notificationCount || 'All clear'}</span><button aria-label="Close world activity" className="rpg-world-popover-close" type="button" onClick={() => setNotificationsOpen(false)}>×</button></header>
              {generationRun ? <button type="button" onClick={() => navigate('generation')}><span>⚙</span><strong>Generation {statusLabel(generationRun.status)}</strong><small>Open run diagnostics and activity.</small></button> : null}
              {attentionSections.map((candidate) => <button key={candidate.id} type="button" onClick={() => navigate(candidate.id)}><span>{icon(candidate.id)}</span><strong>{candidate.label}</strong><small>{statusLabel(candidate.operational_status)} · {statusLabel(candidate.editorial_status)}</small></button>)}
              {!generationRun && !attentionSections.length ? <p>No generation failures, stale sections, or pending reviews.</p> : null}
            </aside>
          ) : null}

          <main className="rpg-world-content-canvas">
            {selectedSection.id === 'advanced' ? (
              <RpgWorldAdvancedPanel world={currentWorld} />
            ) : selectedSection.id === 'generation' ? (
              <RpgWorldGenerationDashboard
                generation={manifestQuery.data?.generation}
                onOpenImages={() => navigate('images')}
                onOpenSection={(nextSectionId) => navigate(nextSectionId)}
                sections={sections}
                tokenUsage={manifestQuery.data?.token_usage}
                worldId={worldId}
              />
            ) : selectedSection.id === 'images' ? (
              <RpgWorldImagesPanel worldId={worldId} />
            ) : selectedSection.id === 'map' ? (
              <RpgWorldVisualMapPanel worldId={worldId} />
            ) : selectedSection.id === 'scenarios' ? (
              <RpgWorldScenarioAuthoringPanel worldId={worldId} />
            ) : (
              <RpgWorldAuthoringPage
                error={pageQuery.error instanceof Error ? pageQuery.error.message : undefined}
                isLoading={pageQuery.isPending && rawSectionIds.has(selectedSection.id)}
                isSaving={updateWorld.isPending}
                onOpenEntity={(nextSectionId, nextEntityId) => navigate(nextSectionId, nextEntityId)}
                onOpenSection={(nextSectionId) => navigate(nextSectionId)}
                onSaveWorld={(changes) => updateWorld.mutate(changes)}
                onSelectEntity={(nextEntityId) => navigate(selectedSection.id, nextEntityId)}
                page={page}
                section={selectedSection}
                sections={sections}
                selectedEntityId={entityId}
                world={currentWorld}
                worldId={worldId}
              />
            )}
          </main>
        </div>
      </div>
    </section>
  );
}
