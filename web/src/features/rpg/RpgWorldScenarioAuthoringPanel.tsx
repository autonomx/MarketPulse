/* eslint-disable @typescript-eslint/no-unused-expressions -- baseline WP-9.x */
/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldLibraryClient,
  type RpgScenarioSummary,
} from './api/rpgWorldLibraryClient';
import {
  certification,
  matchingRelease,
  number,
  pretty,
  record,
  text,
  worldLocationOptions,
} from './rpgWorldAuthoringData';
import './RpgWorldSpatialAuthoring.css';

interface RpgWorldScenarioAuthoringPanelProps {
  worldId: string;
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function parseArray(value: string, label: string): Record<string, unknown>[] {
  const parsed = JSON.parse(value) as unknown;
  if (!Array.isArray(parsed) || parsed.some((row) => !row || typeof row !== 'object' || Array.isArray(row))) {
    throw new Error(`${label} must be a JSON array of objects.`);
  }
  return parsed as Record<string, unknown>[];
}

export function RpgWorldScenarioAuthoringPanel({ worldId }: RpgWorldScenarioAuthoringPanelProps) {
  const queryClient = useQueryClient();
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [title, setTitle] = useState('Opening Scenario');
  const [description, setDescription] = useState('');
  const [locationId, setLocationId] = useState('');
  const [startingEpoch, setStartingEpoch] = useState('Day 1');
  const [protagonistOptionsJson, setProtagonistOptionsJson] = useState('[]');
  const [startingResourcesJson, setStartingResourcesJson] = useState('{}');
  const [feedback, setFeedback] = useState('');
  const detailQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'scenario-authoring', worldId],
    queryFn: () => rpgWorldLibraryClient.detail(worldId),
    refetchInterval: 5000,
  });
  const detail = detailQuery.data;
  const locations = useMemo(() => worldLocationOptions(detail), [detail]);
  const scenarios = detail?.scenarios ?? [];
  const selectedScenario = scenarios.find((scenario) => scenario.id === selectedScenarioId);
  const selectedRevisions = detail?.scenario_revisions[selectedScenarioId] ?? [];
  const latestScenarioRevision = [...selectedRevisions]
    .sort((left, right) => right.revision - left.revision)[0];
  const latestWorldRevision = detail?.revisions[0];
  const latestRelease = detail?.releases.find((release) => (
    release.world_revision === latestWorldRevision?.revision
  ));

  useEffect(() => {
    if (!selectedScenarioId && scenarios[0]?.id) setSelectedScenarioId(scenarios[0].id);
  }, [scenarios, selectedScenarioId]);

  useEffect(() => {
    if (!locationId && locations[0]?.id) setLocationId(locations[0].id);
  }, [locationId, locations]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-library', 'scenario-authoring', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-workspace'] }),
    ]);
  };

  const createScenario = useMutation({
    mutationFn: () => rpgWorldLibraryClient.createScenario({
      world_id: worldId,
      title: title.trim(),
      description: description.trim() || `Opening at ${locationId}`,
      metadata: { starting_location: locationId },
    }),
    onSuccess: async (result) => {
      setSelectedScenarioId(result.scenario.id);
      setFeedback(`Scenario created: ${result.scenario.title}. Publish its first revision when ready.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Scenario could not be created.'),
  });

  const publishScenario = useMutation({
    mutationFn: async () => {
      if (!selectedScenario || !latestWorldRevision || !latestRelease || !locationId) {
        throw new Error('Select a scenario, location, published world revision, and release.');
      }
      const revision = Math.max(0, ...selectedRevisions.map((row) => row.revision)) + 1;
      const protagonistOptions = parseArray(protagonistOptionsJson, 'Protagonist options');
      const startingResources = parseObject(startingResourcesJson, 'Starting resources');
      const publish = (
        worldRevision: number,
        worldRevisionHash: string,
        compatibleRelease: number,
      ) => rpgWorldLibraryClient.publishScenario(selectedScenario.id, {
        revision,
        world_id: worldId,
        world_revision: worldRevision,
        world_revision_hash: worldRevisionHash,
        compatible_release: compatibleRelease,
        starting_epoch: startingEpoch,
        starting_location_id: locationId,
        activated_conflict_ids: [],
        initial_npc_ids: [],
        protagonist_options: protagonistOptions,
        starting_resources: startingResources,
        opening_seed_ids: [],
        map_initialization: [],
        content_hash: '',
      });

      try {
        return {
          result: await publish(
            latestWorldRevision.revision,
            latestWorldRevision.content_hash,
            latestRelease.release,
          ),
          promotedRevision: 0,
        };
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause);
        if (!message.includes('scenario_starting_map_missing')) throw cause;
        const promoted = await rpgWorldLibraryClient.promoteStarterBubble(worldId, {
          source_world_revision: latestWorldRevision.revision,
          starting_location_id: locationId,
        });
        const promotion = record(promoted.promotion);
        const promotedRevision = number(promotion.world_revision);
        const promotedRelease = number(promotion.world_release);
        const promotedHash = text(promotion.world_revision_hash);
        if (!promotedRevision || !promotedRelease || !promotedHash) {
          throw new Error('Starter-map promotion did not return a publishable release.');
        }
        return {
          result: await publish(promotedRevision, promotedHash, promotedRelease),
          promotedRevision,
        };
      }
    },
    onSuccess: async ({ result, promotedRevision }) => {
      setFeedback(
        `Published scenario revision ${result.scenario_revision.revision}${promotedRevision ? ` after preparing starter maps in world revision ${promotedRevision}` : ''}.`,
      );
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Scenario revision could not be published.'),
  });

  const lifecycle = useMutation({
    mutationFn: (scenario: RpgScenarioSummary) => scenario.status === 'archived'
      ? rpgWorldLibraryClient.restoreScenario(scenario.id)
      : rpgWorldLibraryClient.archiveScenario(scenario.id),
    onSuccess: async (result) => {
      setFeedback(`Scenario ${result.scenario.status}: ${result.scenario.title}`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Scenario status could not be changed.'),
  });

  const loadScenario = (scenario: RpgScenarioSummary) => {
    const revisions = detail?.scenario_revisions[scenario.id] ?? [];
    const latest = [...revisions].sort((left, right) => right.revision - left.revision)[0];
    const document = record(latest?.document);
    setSelectedScenarioId(scenario.id);
    setTitle(scenario.title);
    setDescription(scenario.description);
    setLocationId(text(document.starting_location_id, text(scenario.metadata.starting_location, locations[0]?.id)));
    setStartingEpoch(text(document.starting_epoch, 'Day 1'));
    setProtagonistOptionsJson(pretty(document.protagonist_options ?? []));
    setStartingResourcesJson(pretty(document.starting_resources ?? {}));
    setFeedback(`Loaded ${scenario.title}. Publishing creates revision ${revisions.length + 1}.`);
  };

  return (
    <section className="rpg-authoring-page rpg-spatial-authoring" aria-label="Scenario authoring">
      <div className="rpg-authoring-page-heading">
        <div><p className="eyebrow">Game Master</p><h2>Scenarios</h2><p>Create and publish immutable campaign openings without leaving the world editor.</p></div>
        <span>{scenarios.length} scenario{scenarios.length === 1 ? '' : 's'}</span>
      </div>
      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
      {detailQuery.isPending ? <p>Loading scenarios…</p> : null}
      {detailQuery.isError ? <p className="rpg-world-catalog-error">Unable to load scenario authoring.</p> : null}

      <div className="rpg-spatial-layout">
        <form onSubmit={(event) => { event.preventDefault(); selectedScenario ? publishScenario.mutate() : createScenario.mutate(); }}>
          <h3>{selectedScenario ? `Edit ${selectedScenario.title}` : 'Create opening scenario'}</h3>
          <p>Scenario IDs are assigned automatically when a project is created.</p>
          <label><span>Title</span><input required value={title} onChange={(event) => setTitle(event.currentTarget.value)} /></label>
          <label><span>Description</span><textarea rows={4} value={description} onChange={(event) => setDescription(event.currentTarget.value)} /></label>
          <label><span>Starting location</span><select disabled={!locations.length} value={locationId} onChange={(event) => setLocationId(event.currentTarget.value)}>{!locations.length ? <option value="">Generate Areas first</option> : null}{locations.map((location) => <option key={location.id} value={location.id}>{location.label}</option>)}</select></label>
          <label><span>Starting epoch</span><input value={startingEpoch} onChange={(event) => setStartingEpoch(event.currentTarget.value)} /></label>
          <label><span>Protagonist options JSON</span><textarea aria-label="Protagonist options JSON" rows={8} value={protagonistOptionsJson} onChange={(event) => setProtagonistOptionsJson(event.currentTarget.value)} /></label>
          <label><span>Starting resources JSON</span><textarea aria-label="Starting resources JSON" rows={6} value={startingResourcesJson} onChange={(event) => setStartingResourcesJson(event.currentTarget.value)} /></label>
          <div className="rpg-spatial-actions">
            {selectedScenario ? <button className="rpg-secondary-button" type="button" onClick={() => { setSelectedScenarioId(''); setTitle('Opening Scenario'); setDescription(''); }}>New Scenario</button> : null}
            <button type="submit" disabled={!locationId || createScenario.isPending || publishScenario.isPending || (Boolean(selectedScenario) && (!latestWorldRevision || !latestRelease))}>{selectedScenario ? (publishScenario.isPending ? 'Publishing…' : `Publish Revision ${selectedRevisions.length + 1}`) : (createScenario.isPending ? 'Creating…' : 'Create Scenario')}</button>
          </div>
          {latestScenarioRevision ? <small>Latest revision {latestScenarioRevision.revision} · world r{latestScenarioRevision.world_revision}</small> : null}
        </form>

        <section>
          <h3>Scenario projects</h3>
          <div className="rpg-spatial-card-list">
            {scenarios.map((scenario) => {
              const revisions = detail?.scenario_revisions[scenario.id] ?? [];
              const latest = [...revisions].sort((left, right) => right.revision - left.revision)[0];
              const release = latest && detail ? matchingRelease(detail, latest) : undefined;
              const launchReady = Boolean(certification(release).launch_ready);
              return (
                <article key={scenario.id}>
                  <div><strong>{scenario.title}</strong><p>{scenario.status} · {revisions.length} revision{revisions.length === 1 ? '' : 's'}</p><small>{latest ? `World r${latest.world_revision} · ${launchReady ? 'launch ready' : 'review required'}` : 'Not published'}</small></div>
                  <div className="rpg-spatial-actions"><button className="rpg-secondary-button" type="button" onClick={() => loadScenario(scenario)}>Edit Next Revision</button><button className="rpg-secondary-button" type="button" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate(scenario)}>{scenario.status === 'archived' ? 'Restore' : 'Archive'}</button></div>
                </article>
              );
            })}
            {!scenarios.length ? <p>No scenario projects have been created.</p> : null}
          </div>
        </section>
      </div>
    </section>
  );
}
