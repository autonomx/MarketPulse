import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldLibraryClient,
  type RpgWorldSummary,
} from './api/rpgWorldLibraryClient';
import { RpgWorldCampaignSetup } from './RpgWorldCampaignSetup';
import { RpgWorldCard } from './RpgWorldCard';
import {
  parseWorldEditorRoute,
  pushWorldEditorRoute,
} from './RpgWorldCompletionModels';
import { RpgWorldDeleteDialog } from './RpgWorldDeleteDialog';
import { RpgWorldEditorShell } from './RpgWorldEditorShell';
import './RpgWorldAuthoringWorkspace.css';

interface RpgWorldAuthoringWorkspaceProps {
  onBack: () => void;
  onSessionLaunched: (sessionId: string) => void;
}

type WorldWorkspaceView =
  | { kind: 'library' }
  | { kind: 'editor'; worldId: string }
  | { kind: 'campaignSetup'; worldId: string };

function initialView(): WorldWorkspaceView {
  const route = parseWorldEditorRoute();
  return route ? { kind: 'editor', worldId: route.worldId } : { kind: 'library' };
}

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function worldState(world: RpgWorldSummary): string {
  const generation = world.generation;
  if (world.status === 'archived') return 'Archived';
  if (generation?.status === 'running' || generation?.status === 'planned') return 'Generating';
  if (generation?.status === 'failed') return 'Generation failed';
  return world.status === 'published' ? 'Published' : 'Draft';
}

export function RpgWorldAuthoringWorkspace({ onBack, onSessionLaunched }: RpgWorldAuthoringWorkspaceProps) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<WorldWorkspaceView>(initialView);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [showCreate, setShowCreate] = useState(false);
  const [pendingDeleteWorld, setPendingDeleteWorld] = useState<RpgWorldSummary | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [genre, setGenre] = useState('classic_fantasy');
  const [tone, setTone] = useState('heroic adventure');
  const [feedback, setFeedback] = useState('');

  const libraryQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-authoring-workspace'],
    queryFn: () => rpgWorldLibraryClient.list(),
    refetchInterval: 5000,
  });

  const openEditor = (worldId: string, replace = false, sectionId = 'overview') => {
    setView({ kind: 'editor', worldId });
    pushWorldEditorRoute({ worldId, sectionId }, replace);
  };
  const openLibrary = (replace = false) => {
    setView({ kind: 'library' });
    pushWorldEditorRoute(null, replace);
  };

  useEffect(() => {
    const syncRoute = () => {
      const route = parseWorldEditorRoute();
      setView(route ? { kind: 'editor', worldId: route.worldId } : { kind: 'library' });
    };
    window.addEventListener('popstate', syncRoute);
    window.addEventListener('hashchange', syncRoute);
    return () => {
      window.removeEventListener('popstate', syncRoute);
      window.removeEventListener('hashchange', syncRoute);
    };
  }, []);

  const refresh = async () => queryClient.invalidateQueries({ queryKey: ['feature', 'rpg'] });
  const createWorld = useMutation({
    mutationFn: () => rpgWorldLibraryClient.createWorld({
      title: title.trim(),
      description: description.trim(),
      source_mode: 'hybrid',
      genre,
      tone,
      seed: 0,
      metadata: { campaign_template: genre },
    }),
    onSuccess: async (result) => {
      setShowCreate(false);
      setTitle('');
      setDescription('');
      setFeedback(`Created ${result.world.title}.`);
      await refresh();
      openEditor(result.world.id);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'World could not be created.'),
  });
  const lifecycle = useMutation({
    mutationFn: (world: RpgWorldSummary) => world.status === 'archived'
      ? rpgWorldLibraryClient.restoreWorld(world.id)
      : rpgWorldLibraryClient.archiveWorld(world.id),
    onSuccess: async () => refresh(),
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'World status could not be changed.'),
  });

  const visibleWorlds = useMemo(() => {
    const query = search.trim().toLowerCase();
    return [...(libraryQuery.data?.worlds ?? [])]
      .filter((world) => statusFilter === 'all' || worldState(world).toLowerCase().replace(/\s+/g, '-') === statusFilter)
      .filter((world) => !query || [world.title, world.description, world.genre, world.tone]
        .some((value) => value.toLowerCase().includes(query)))
      .sort((left, right) => timestamp(right.updated_at) - timestamp(left.updated_at));
  }, [libraryQuery.data?.worlds, search, statusFilter]);

  if (view.kind === 'library') {
    return (
      <section className="rpg-authoring-library" aria-label="World library">
        <header className="rpg-authoring-heading">
          <div><p className="eyebrow">RPG authoring</p><h2>Worlds</h2><p>Create reusable worlds, then edit or play them.</p></div>
          <div><button className="rpg-secondary-button" type="button" onClick={onBack}>Back to Play</button><button type="button" onClick={() => setShowCreate(true)}>Create New World</button></div>
        </header>
        <div className="rpg-authoring-toolbar">
          <input aria-label="Search world library" placeholder="Search by title, genre, or tone…" value={search} onChange={(event) => setSearch(event.currentTarget.value)} />
          <select aria-label="Filter worlds" value={statusFilter} onChange={(event) => setStatusFilter(event.currentTarget.value)}>
            <option value="all">All worlds</option><option value="draft">Draft</option><option value="published">Published</option><option value="archived">Archived</option><option value="generating">Generating</option><option value="generation-failed">Generation failed</option>
          </select>
        </div>
        {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
        {libraryQuery.isPending ? <p>Loading worlds…</p> : null}
        {libraryQuery.isError ? <p className="rpg-world-catalog-error">Unable to load worlds.</p> : null}
        <div className="rpg-world-card-grid">
          {visibleWorlds.map((world) => {
            const scenarioCount = (libraryQuery.data?.scenarios ?? []).filter((scenario) => scenario.world_id === world.id && scenario.status === 'published').length;
            const campaignCount = (libraryQuery.data?.campaigns ?? []).filter((campaign) => campaign.world_id === world.id && campaign.status !== 'archived').length;
            return (
              <RpgWorldCard
                key={world.id}
                world={world}
                facts={<><span>{worldState(world)}</span><span>{scenarioCount} opening{scenarioCount === 1 ? '' : 's'}</span><span>{campaignCount} campaign{campaignCount === 1 ? '' : 's'}</span></>}
                actions={<><button className="rpg-secondary-button" type="button" onClick={() => openEditor(world.id)}>Edit</button><button type="button" onClick={() => { pushWorldEditorRoute(null); setView({ kind: 'campaignSetup', worldId: world.id }); }}>Play</button></>}
                footer={(
                  <details className="rpg-world-card-more">
                    <summary>More</summary>
                    <button className="rpg-secondary-button" type="button" onClick={() => lifecycle.mutate(world)}>{world.status === 'archived' ? 'Restore' : 'Archive'}</button>
                    <button className="rpg-danger-button" type="button" onClick={() => setPendingDeleteWorld(world)}>Delete world</button>
                  </details>
                )}
              />
            );
          })}
        </div>
        {!libraryQuery.isPending && !visibleWorlds.length ? <p>No worlds match this view.</p> : null}
        {showCreate ? (
          <div className="rpg-authoring-modal" role="dialog" aria-modal="true" aria-label="Create new world">
            <form onSubmit={(event) => { event.preventDefault(); createWorld.mutate(); }}>
              <h3>Create New World</h3>
              <p>A stable technical identifier will be assigned automatically.</p>
              <label><span>Title</span><input required value={title} onChange={(event) => setTitle(event.currentTarget.value)} /></label>
              <label><span>Description</span><textarea value={description} onChange={(event) => setDescription(event.currentTarget.value)} /></label>
              <label><span>Genre</span><input value={genre} onChange={(event) => setGenre(event.currentTarget.value)} /></label>
              <label><span>Tone</span><input value={tone} onChange={(event) => setTone(event.currentTarget.value)} /></label>
              <div><button className="rpg-secondary-button" type="button" onClick={() => setShowCreate(false)}>Cancel</button><button type="submit" disabled={createWorld.isPending}>Create World</button></div>
            </form>
          </div>
        ) : null}
        {pendingDeleteWorld ? (
          <RpgWorldDeleteDialog
            world={pendingDeleteWorld}
            onCancel={() => setPendingDeleteWorld(null)}
            onDeleted={(result) => {
              setPendingDeleteWorld(null);
              setFeedback(`Deleted ${result.world_title}.`);
              void refresh();
            }}
          />
        ) : null}
      </section>
    );
  }

  if (view.kind === 'campaignSetup') {
    return (
      <RpgWorldCampaignSetup
        onBack={() => openLibrary()}
        onEditWorld={() => openEditor(view.worldId)}
        onReviewGeneration={() => openEditor(view.worldId, false, 'generation')}
        onSessionLaunched={onSessionLaunched}
        worldId={view.worldId}
      />
    );
  }

  const world = libraryQuery.data?.worlds.find((candidate) => candidate.id === view.worldId);
  if (!world) return <p>Loading world editor…</p>;
  return (
    <RpgWorldEditorShell
      onBack={() => openLibrary()}
      onPlay={() => { pushWorldEditorRoute(null); setView({ kind: 'campaignSetup', worldId: view.worldId }); }}
      world={world}
      worldId={view.worldId}
    />
  );
}
