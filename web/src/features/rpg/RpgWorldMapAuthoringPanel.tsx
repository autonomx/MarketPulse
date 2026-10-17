import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldLibraryClient,
  type RpgMapBlueprintRevision,
} from './api/rpgWorldLibraryClient';
import {
  array,
  defaultMapBlueprint,
  pretty,
  record,
  text,
  worldLocationOptions,
} from './rpgWorldAuthoringData';
import './RpgWorldSpatialAuthoring.css';

interface RpgWorldMapAuthoringPanelProps {
  worldId: string;
}

function findingLabel(value: Record<string, unknown>): string {
  return [text(value.code, 'semantic mismatch'), text(value.target_id), text(value.scenario_id)]
    .filter(Boolean)
    .join(' · ');
}

export function RpgWorldMapAuthoringPanel({ worldId }: RpgWorldMapAuthoringPanelProps) {
  const queryClient = useQueryClient();
  const [locationId, setLocationId] = useState('');
  const [mapId, setMapId] = useState('');
  const [expectedRevision, setExpectedRevision] = useState(0);
  const [blueprintJson, setBlueprintJson] = useState('{}');
  const [feedback, setFeedback] = useState('');
  const detailQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'map-authoring', worldId],
    queryFn: () => rpgWorldLibraryClient.detail(worldId),
    refetchInterval: 5000,
  });
  const detail = detailQuery.data;
  const locations = useMemo(() => worldLocationOptions(detail), [detail]);
  const latestRevision = detail?.revisions[0];
  const requirements = array(record(latestRevision?.document).blueprint_requirements);
  const readyCount = (detail?.map_blueprints ?? []).filter((row) => row.status === 'ready').length;
  const invalidCount = (detail?.map_blueprints ?? []).filter((row) => row.status === 'invalid').length;

  const resetBlueprint = (nextLocation: string) => {
    const document = defaultMapBlueprint(nextLocation);
    setLocationId(nextLocation);
    setMapId(text(document.map_id));
    setExpectedRevision(0);
    setBlueprintJson(pretty(document));
  };

  useEffect(() => {
    if (!locationId && locations[0]?.id) resetBlueprint(locations[0].id);
  }, [locationId, locations]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-library', 'map-authoring', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
    ]);
  };

  const save = useMutation({
    mutationFn: () => {
      const parsed = JSON.parse(blueprintJson) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Blueprint JSON must be an object.');
      }
      const document = {
        ...(parsed as Record<string, unknown>),
        map_id: mapId.trim(),
        location_id: locationId,
      };
      return rpgWorldLibraryClient.saveMapBlueprint(
        worldId,
        mapId.trim(),
        { expected_revision: expectedRevision, document },
      );
    },
    onSuccess: async (result) => {
      setExpectedRevision(result.map_blueprint.blueprint_revision);
      setBlueprintJson(pretty(result.map_blueprint.document));
      setFeedback(
        result.map_blueprint.status === 'ready'
          ? `Blueprint ${result.map_blueprint.map_id} r${result.map_blueprint.blueprint_revision} is ready.`
          : `Blueprint saved with ${result.map_blueprint.findings.length} reconciliation finding(s).`,
      );
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Blueprint could not be saved.'),
  });

  const loadBlueprint = (blueprint: RpgMapBlueprintRevision) => {
    setMapId(blueprint.map_id);
    setLocationId(text(record(blueprint.document).location_id));
    setExpectedRevision(blueprint.blueprint_revision);
    setBlueprintJson(pretty(blueprint.document));
    setFeedback(`Loaded ${blueprint.map_id} r${blueprint.blueprint_revision}. Saving creates the next revision.`);
  };

  return (
    <section className="rpg-authoring-page rpg-spatial-authoring" aria-label="Map authoring">
      <div className="rpg-authoring-page-heading">
        <div><p className="eyebrow">Spatial authoring</p><h2>Map</h2><p>Author semantic map requirements separately from visual map assets.</p></div>
        <div className="rpg-spatial-status"><span>{readyCount} ready</span><span>{invalidCount} need review</span></div>
      </div>
      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
      {detailQuery.isPending ? <p>Loading map blueprints…</p> : null}
      {detailQuery.isError ? <p className="rpg-world-catalog-error">Unable to load map authoring.</p> : null}
      {!detailQuery.isPending && !locations.length ? (
        <div className="rpg-authoring-empty"><h3>No world locations yet</h3><p>Generate or author Areas before creating spatial blueprints.</p></div>
      ) : null}

      {locations.length ? (
        <div className="rpg-spatial-layout">
          <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
            <h3>Blueprint editor</h3>
            <label><span>World location</span><select value={locationId} onChange={(event) => resetBlueprint(event.currentTarget.value)}>{locations.map((location) => <option key={location.id} value={location.id}>{location.label}</option>)}</select></label>
            <label><span>Map ID</span><input required value={mapId} onChange={(event) => setMapId(event.currentTarget.value)} /></label>
            <label><span>Current blueprint revision</span><input readOnly type="number" value={expectedRevision} /></label>
            <label><span>Structured blueprint JSON</span><textarea aria-label="Structured blueprint JSON" rows={20} value={blueprintJson} onChange={(event) => setBlueprintJson(event.currentTarget.value)} /></label>
            <div className="rpg-spatial-actions"><button className="rpg-secondary-button" type="button" onClick={() => resetBlueprint(locationId)}>New Blueprint</button><button type="submit" disabled={save.isPending || !mapId.trim()}>{save.isPending ? 'Saving…' : expectedRevision ? 'Save Next Revision' : 'Save Blueprint'}</button></div>
            {requirements.length ? <details><summary>Published blueprint requirements ({requirements.length})</summary><pre>{pretty(requirements)}</pre></details> : null}
          </form>

          <section>
            <h3>Blueprint revisions</h3>
            <div className="rpg-spatial-card-list">
              {(detail?.map_blueprints ?? []).map((blueprint) => (
                <article key={`${blueprint.map_id}:${blueprint.blueprint_revision}`}>
                  <div><strong>{blueprint.map_id}</strong><p>Revision {blueprint.blueprint_revision} · {blueprint.status}</p><small>{blueprint.semantic_interface_hash}</small></div>
                  {blueprint.findings.length ? <ul>{blueprint.findings.map((finding, index) => <li key={`${blueprint.map_id}:${index}`}>{findingLabel(finding)}</li>)}</ul> : <p>All active scenario references reconcile.</p>}
                  <button className="rpg-secondary-button" type="button" onClick={() => loadBlueprint(blueprint)}>Edit Next Revision</button>
                </article>
              ))}
              {!detail?.map_blueprints.length ? <p>No blueprint revisions have been authored.</p> : null}
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
