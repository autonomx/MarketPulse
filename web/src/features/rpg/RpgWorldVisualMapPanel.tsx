import { useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rpgWorldLibraryClient } from './api/rpgWorldLibraryClient';
import { rpgWorldImageClient } from './api/rpgWorldImageClient';
import { RpgWorldMapAuthoringPanel } from './RpgWorldMapAuthoringPanel';
import { array, record, text, worldLocationOptions } from './rpgWorldAuthoringData';

interface RpgWorldVisualMapPanelProps {
  worldId: string;
}

function assetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}

const ATLAS_WIDTH = 1600;
const ATLAS_HEIGHT = 1040;
const MIN_ZOOM = 0.55;
const MAX_ZOOM = 2.8;
const LOCATION_MAP_ZOOM = 1.7;

interface AtlasPoint { x: number; y: number; }
interface LocalMapOverlay { id: string; label: string; kind: string; x: number; y: number; }

function mapLabel(location: { id: string; label: string }): string {
  const identifierSuffix = ` (${location.id})`;
  return location.label.endsWith(identifierSuffix)
    ? location.label.slice(0, -identifierSuffix.length)
    : location.label;
}

function numeric(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function mapPosition(entity: Record<string, unknown>, locationId: string, index: number, total: number): AtlasPoint {
  const candidates = [
    record(entity.map_position),
    record(entity.coordinates),
    record(record(entity.metadata).map_position),
  ];
  for (const candidate of candidates) {
    const x = numeric(candidate.x ?? candidate.column);
    const y = numeric(candidate.y ?? candidate.row);
    if (x !== undefined && y !== undefined) {
      return {
        x: Math.max(80, Math.min(ATLAS_WIDTH - 80, x <= 100 ? 80 + (x * 14.4) : x)),
        y: Math.max(80, Math.min(ATLAS_HEIGHT - 80, y <= 100 ? 80 + (y * 8.8) : y)),
      };
    }
  }
  const hash = stableHash(locationId);
  const columns = Math.max(3, Math.ceil(Math.sqrt(Math.max(total, 1) * 1.8)));
  const column = index % columns;
  const row = Math.floor(index / columns);
  return {
    x: 150 + (column * ((ATLAS_WIDTH - 300) / Math.max(columns - 1, 1))) + ((hash >>> 8) % 90) - 45,
    y: 160 + (row * 230) + ((hash >>> 16) % 100) - 50,
  };
}

function locationEntities(detail: unknown): Map<string, Record<string, unknown>> {
  const response = record(detail);
  const entities = new Map<string, Record<string, unknown>>();
  const merge = (id: unknown, value: unknown) => {
    const entityId = text(id);
    if (!entityId) return;
    entities.set(entityId, { ...entities.get(entityId), ...record(value) });
  };
  const topics = Array.isArray(response.topics) ? response.topics : [];
  for (const rawTopic of topics) {
    const topic = record(rawTopic);
    if (!['locations', 'places', 'regions'].includes(text(topic.topic_id))) continue;
    const values = record(topic.content).entities;
    if (!Array.isArray(values)) continue;
    for (const rawEntity of values) {
      const entity = record(rawEntity);
      merge(entity.location_id ?? entity.id ?? entity.entity_id, entity);
    }
  }

  // Topic rows are often deliberately lightweight. Layer the generated topic
  // data beneath the canonical record, which carries the authored location
  // description and other durable entity fields.
  const revision = record(array(response.revisions)[0]);
  const document = record(revision.document);
  for (const source of [
    record(record(document.entity_manifest).entities),
    record(record(document.canon).entities),
  ]) {
    for (const [entityId, entity] of Object.entries(source)) merge(entityId, entity);
  }
  return entities;
}

function locationDescription(entity: Record<string, unknown> | undefined): string {
  const row = entity ?? {};
  const dossier = record(row.dossier);
  const metadata = record(row.metadata);
  const metadataDossier = record(metadata.dossier);
  const values = [
    row.description,
    row.short_summary,
    row.summary,
    row.sensory_profile,
    dossier.description,
    dossier.sensory_profile,
    metadata.description,
    metadata.short_summary,
    metadata.summary,
    metadata.sensory_profile,
    metadataDossier.description,
    metadataDossier.sensory_profile,
  ];
  return values.map((value) => text(value)).find(Boolean)
    ?? 'This Area has a generated semantic baseline and is ready for further authoring.';
}

function localMapOverlays(blueprint: unknown): LocalMapOverlay[] {
  const document = record(record(blueprint).document);
  const groups: Array<[string, unknown]> = [
    ['Zone', document.required_zone_ids],
    ['Portal', document.required_portal_ids],
    ['Route', document.required_route_ids],
    ['Spawn', document.required_spawn_point_ids],
  ];
  return groups.flatMap(([kind, values]) => array(values).map((value) => ({ kind, id: text(value) })))
    .filter((overlay) => overlay.id)
    .slice(0, 12)
    .map((overlay) => {
      const hash = stableHash(overlay.id);
      const suffix = overlay.id.split(':').pop()?.replace(/[_-]+/g, ' ') ?? overlay.id;
      return {
        ...overlay,
        label: `${overlay.kind}: ${suffix}`,
        x: 14 + (hash % 72),
        y: 18 + ((hash >>> 8) % 64),
      };
    });
}

export function RpgWorldVisualMapPanel({ worldId }: RpgWorldVisualMapPanelProps) {
  const queryClient = useQueryClient();
  const [selectedLocationId, setSelectedLocationId] = useState('');
  const [activeLocationMapId, setActiveLocationMapId] = useState('');
  const [feedback, setFeedback] = useState('');
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; panX: number; panY: number } | null>(null);
  useEffect(() => {
    const syncFullscreenState = () => setIsFullscreen(document.fullscreenElement === viewport.current);
    document.addEventListener('fullscreenchange', syncFullscreenState);
    return () => document.removeEventListener('fullscreenchange', syncFullscreenState);
  }, []);
  const detailQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'visual-map', worldId],
    queryFn: () => rpgWorldLibraryClient.detail(worldId),
    refetchInterval: 5000,
  });
  const detail = detailQuery.data;
  const imagesQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-image-targets', worldId],
    queryFn: () => rpgWorldImageClient.list(worldId),
    refetchInterval: (state) => (
      state.state.data?.targets.some((target) => ['queued', 'generating'].includes(target.status))
        ? 3000
        : false
    ),
  });
  const locations = useMemo(() => worldLocationOptions(detail), [detail]);
  const entitiesByLocation = useMemo(() => locationEntities(detail), [detail]);
  const atlasLocations = useMemo(() => locations.map((location, index) => ({
    ...location,
    entity: entitiesByLocation.get(location.id) ?? {},
    point: mapPosition(entitiesByLocation.get(location.id) ?? {}, location.id, index, locations.length),
  })), [entitiesByLocation, locations]);
  const selected = atlasLocations.find((location) => location.id === selectedLocationId) ?? atlasLocations[0];
  const selectedLabel = selected ? mapLabel(selected) : '';
  const blueprints = detail?.map_blueprints ?? [];
  const selectedBlueprint = blueprints.find((blueprint) => text(record(blueprint.document).location_id) === selected?.id);
  const mapTarget = imagesQuery.data?.targets.find((target) => target.target_id === 'world:map');
  // A blueprint change makes the map target stale while the regenerated image
  // is queued. Keep the last approved artwork on the atlas during that gap.
  const mapAssetId = mapTarget?.review_state !== 'rejected' ? mapTarget?.active_asset_id : undefined;
  const isLocationArtworkTarget = (target: { metadata: Record<string, unknown>; role: string }) => (
    ['locations', 'places', 'regions'].includes(text(target.metadata.topic_id))
    && target.role !== 'map'
  );
  const locationArtwork = useMemo(() => new Map(
    (imagesQuery.data?.targets ?? [])
      .filter((target) => isLocationArtworkTarget(target) && target.review_state === 'approved' && target.active_asset_id)
      .map((target) => [target.entity_id, String(target.active_asset_id)]),
  ), [imagesQuery.data?.targets]);
  const locationMapArtwork = useMemo(() => new Map(
    (imagesQuery.data?.targets ?? [])
      .filter((target) => target.metadata.map_level === 'location' && target.review_state === 'approved' && target.active_asset_id)
      .map((target) => [target.entity_id, String(target.active_asset_id)]),
  ), [imagesQuery.data?.targets]);
  const missingLocationArtwork = (imagesQuery.data?.targets ?? [])
    .filter((target) => isLocationArtworkTarget(target) && !(
      target.review_state === 'approved' && target.active_asset_id
    ));
  const missingLocationMaps = (imagesQuery.data?.targets ?? [])
    .filter((target) => target.metadata.map_level === 'location' && !(
      target.review_state === 'approved' && target.active_asset_id
    ));
  const locationMapTargets = (imagesQuery.data?.targets ?? [])
    .filter((target) => target.metadata.map_level === 'location');
  const areaArtworkTotal = locationArtwork.size + missingLocationArtwork.length;
  const queuedAreaArtwork = missingLocationArtwork.filter((target) => ['queued', 'generating'].includes(target.status)).length;
  const areaArtworkPercent = areaArtworkTotal ? Math.round((locationArtwork.size / areaArtworkTotal) * 100) : 0;
  const missingBlueprints = locations.filter((location) => !blueprints.some(
    (blueprint) => text(record(blueprint.document).location_id) === location.id,
  ));
  const regenerateMap = useMutation({
    mutationFn: () => rpgWorldImageClient.regenerate(worldId, 'world:map', {
      width: 1024,
      height: 768,
      style: 'illustrated regional map',
      no_cache: true,
    }),
    onSuccess: async () => {
      setFeedback('Map artwork regeneration was queued. The atlas updates when the image is ready.');
      await queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] });
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Map artwork could not be regenerated.'),
  });
  const generateMissingLocationArtwork = useMutation({
    mutationFn: () => rpgWorldImageClient.generate(worldId, {
      target_ids: missingLocationArtwork.map((target) => target.target_id),
      width: 768,
      height: 768,
      style: 'illustrated location scene',
      no_cache: false,
    }),
    onSuccess: async (result) => {
      setFeedback(`Queued ${result.jobs.length} missing area image${result.jobs.length === 1 ? '' : 's'}.`);
      await queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] });
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Area artwork could not be generated.'),
  });
  const generateMissingLocationMaps = useMutation({
    mutationFn: () => rpgWorldImageClient.generate(worldId, {
      target_ids: missingLocationMaps.map((target) => target.target_id),
      style: 'detailed illustrated local RPG map',
      no_cache: false,
    }),
    onSuccess: async (result) => {
      setFeedback(`Queued ${result.jobs.length} detailed local map${result.jobs.length === 1 ? '' : 's'}.`);
      await queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] });
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Detailed local maps could not be generated.'),
  });
  const regenerateAllLocationMaps = useMutation({
    mutationFn: () => rpgWorldImageClient.generate(worldId, {
      target_ids: locationMapTargets.map((target) => target.target_id),
      style: 'detailed illustrated local RPG map',
      no_cache: true,
    }),
    onSuccess: async (result) => {
      setFeedback(`Queued ${result.jobs.length} detailed local map${result.jobs.length === 1 ? '' : 's'} for regeneration.`);
      await queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] });
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Detailed local maps could not be regenerated.'),
  });
  const generateMissingBlueprints = useMutation({
    mutationFn: () => rpgWorldLibraryClient.materializeMapBlueprints(worldId),
    onSuccess: async (result) => {
      setFeedback(result.created_count
        ? `Generated ${result.created_count} area blueprint${result.created_count === 1 ? '' : 's'}.`
        : 'All generated areas already have blueprints.');
      await queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-library', 'visual-map', worldId] });
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Area blueprints could not be generated.'),
  });
  const detailMapAssetId = activeLocationMapId && selected?.id === activeLocationMapId
    ? locationMapArtwork.get(activeLocationMapId)
    : undefined;
  const isLocationMap = Boolean(detailMapAssetId);
  const activeMapAssetId = detailMapAssetId ?? mapAssetId;
  const displayZoom = zoom;
  const canvasStyle = activeMapAssetId ? {
    backgroundImage: `linear-gradient(color-mix(in srgb, var(--c-blue-130) 32%, transparent), color-mix(in srgb, var(--c-blue-130) 68%, transparent)), url(${JSON.stringify(assetUrl(activeMapAssetId))})`,
  } : undefined;
  const selectedDescription = locationDescription(selected?.entity);
  const handleZoom = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setZoom((current) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, current - (event.deltaY * 0.0015))));
  };
  const startPan = (event: PointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest('button')) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
  };
  const movePan = (event: PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    setPan({ x: active.panX + event.clientX - active.x, y: active.panY + event.clientY - active.y });
  };
  const endPan = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  };
  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };
  const enterLocationMap = () => {
    if (!selected || !locationMapArtwork.has(selected.id)) {
      setFeedback('This location does not have an approved detailed map yet.');
      return;
    }
    setActiveLocationMapId(selected.id);
    setZoom(MIN_ZOOM);
    setPan({ x: 0, y: 0 });
  };
  const returnToWorldMap = () => {
    setActiveLocationMapId('');
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await viewport.current?.requestFullscreen();
    } catch (cause) {
      setFeedback(cause instanceof Error ? cause.message : 'Full-screen map mode is unavailable in this browser.');
    }
  };

  return (
    <div className="rpg-visual-map-page">
      <section className="rpg-authoring-page rpg-visual-map-browser" aria-label="Visual world map">
        <div className="rpg-authoring-page-heading">
          <div><p className="eyebrow">World atlas</p><h2>Map</h2><p>Generated Areas receive baseline semantic blueprints automatically; open the editor only when you need to refine one.</p></div>
          <div className="rpg-visual-map-actions">
            {missingLocationMaps.length ? <button className="rpg-secondary-button" type="button" disabled={generateMissingLocationMaps.isPending || imagesQuery.isPending} onClick={() => generateMissingLocationMaps.mutate()}>{generateMissingLocationMaps.isPending ? 'Queuing detailed maps' : `Generate Detail Maps (${missingLocationMaps.length})`}</button> : null}
            {locationMapTargets.length ? <button className="rpg-secondary-button" type="button" disabled={regenerateAllLocationMaps.isPending || imagesQuery.isPending} onClick={() => regenerateAllLocationMaps.mutate()}>{regenerateAllLocationMaps.isPending ? 'Queuing detailed maps' : `Regenerate All Detailed Maps (${locationMapTargets.length})`}</button> : null}
            {!missingLocationMaps.length && locationMapTargets.length ? <span className="rpg-visual-map-artwork-ready">Detailed maps ready ({locationMapArtwork.size})</span> : null}
            <button type="button" disabled={regenerateMap.isPending || imagesQuery.isPending} onClick={() => regenerateMap.mutate()}>{regenerateMap.isPending ? 'Queuing map artwork…' : mapAssetId ? 'Regenerate Map Artwork' : 'Generate Map Artwork'}</button>
            {missingLocationArtwork.length ? <button className="rpg-secondary-button" type="button" disabled={generateMissingLocationArtwork.isPending || imagesQuery.isPending} onClick={() => generateMissingLocationArtwork.mutate()}>{generateMissingLocationArtwork.isPending ? 'Queuing area artwork…' : `Generate Missing Area Artwork (${missingLocationArtwork.length})`}</button> : <span className="rpg-visual-map-artwork-ready">All area artwork ready ({locationArtwork.size})</span>}
            {missingBlueprints.length ? <button className="rpg-secondary-button" type="button" disabled={generateMissingBlueprints.isPending} onClick={() => generateMissingBlueprints.mutate()}>{generateMissingBlueprints.isPending ? 'Generating area blueprints…' : `Generate Area Blueprints (${missingBlueprints.length})`}</button> : null}
            <span>{locations.length} areas · {blueprints.filter((row) => row.status === 'ready').length} ready blueprints</span>
            {areaArtworkTotal ? <div className="rpg-visual-map-progress">
              <div><strong>Area artwork</strong><span>{locationArtwork.size} / {areaArtworkTotal} ready{queuedAreaArtwork ? ` · ${queuedAreaArtwork} generating` : ''}</span></div>
              <div aria-label="Area artwork generation progress" aria-valuemax={areaArtworkTotal} aria-valuemin={0} aria-valuenow={locationArtwork.size} className="rpg-visual-map-progress-track" role="progressbar"><span style={{ width: `${areaArtworkPercent}%` }} /></div>
            </div> : null}
          </div>
        </div>
        {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
        {detailQuery.isPending ? <p>Loading world atlas…</p> : null}
        {detailQuery.isError ? <p className="rpg-world-catalog-error">Unable to load the world atlas.</p> : null}
        {!detailQuery.isPending && !locations.length ? <div className="rpg-authoring-empty"><h3>No mapped areas yet</h3><p>Generate Areas before building the visual atlas.</p></div> : null}
        {locations.length ? (
          <div className="rpg-visual-map-layout">
            <div
              className="rpg-atlas-viewport"
              ref={viewport}
              aria-label="Interactive world atlas. Drag to pan and use the mouse wheel to zoom."
              onPointerDown={startPan}
              onPointerMove={movePan}
              onPointerUp={endPan}
              onPointerCancel={endPan}
              onWheel={handleZoom}
              role="application"
            >
              <div className="rpg-atlas-coordinate-readout">x {Math.round(-pan.x / zoom)} · y {Math.round(-pan.y / zoom)} · {Math.round(zoom * 100)}%</div>
              <div role="group" className="rpg-atlas-toolbar" aria-label="Map controls">
                <button type="button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + 0.2))}>+</button>
                <button type="button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - 0.2))}>−</button>
                <button type="button" aria-label="Reset map view" onClick={resetView}>⌖</button>
                {isLocationMap ? <button type="button" aria-label="Return to world map" onClick={returnToWorldMap}>World</button> : null}
                <button type="button" aria-label={isFullscreen ? 'Exit full screen map' : 'Enter full screen map'} onClick={() => void toggleFullscreen()}>{isFullscreen ? 'Exit' : 'Full'}</button>
              </div>
              <div
                className={`rpg-atlas-world${activeMapAssetId ? ' has-image' : ''}${isLocationMap ? ' is-location-map' : ''}`}
                data-map-level={isLocationMap ? 'location' : 'world'}
                style={{ ...canvasStyle, transform: `translate(${pan.x}px, ${pan.y}px) scale(${displayZoom})` }}
              >
                <div className="rpg-atlas-world-title"><strong>{isLocationMap ? selectedLabel : detail?.world.title}</strong><small>{isLocationMap ? 'Local detail map' : 'World atlas'}</small></div>
                {!isLocationMap && atlasLocations.map((location) => {
                  const blueprint = blueprints.find((row) => text(record(row.document).location_id) === location.id);
                  const locationAssetId = locationArtwork.get(location.id);
                  return (
                    <button
                      aria-label={`Open ${mapLabel(location)}`}
                      aria-pressed={selected?.id === location.id}
                      className={`rpg-atlas-marker${selected?.id === location.id ? ' is-active' : ''}${locationAssetId ? ' has-artwork' : ''}${zoom >= 0.85 ? ' show-label' : ''}`}
                      key={location.id}
                      style={{
                        left: location.point.x,
                        top: location.point.y,
                        ...(locationAssetId ? { backgroundImage: `url(${JSON.stringify(assetUrl(locationAssetId))})` } : {}),
                      }}
                      type="button"
                      onClick={() => setSelectedLocationId(location.id)}
                    >
                      <span aria-hidden="true">{mapLabel(location).slice(0, 1).toUpperCase()}</span>
                      <strong>{mapLabel(location)}</strong>
                      <small>{blueprint?.status ?? 'No blueprint'}</small>
                    </button>
                  );
                })}
                {isLocationMap && localMapOverlays(selectedBlueprint).map((overlay) => (
                  <button
                    aria-label={`Open ${overlay.label}`}
                    className="rpg-atlas-local-overlay"
                    key={overlay.id}
                    style={{ left: `${overlay.x}%`, top: `${overlay.y}%` }}
                    type="button"
                    onClick={() => setFeedback(`${overlay.label} selected on ${selectedLabel || 'the local map'}.`)}
                  >
                    <span>{overlay.kind}</span><strong>{overlay.label.split(': ')[1]}</strong>
                  </button>
                ))}
              </div>
            </div>
            <aside className="rpg-visual-map-inspector">
              <p className="eyebrow">Selected area</p>
              <h3>{selectedLabel}</h3>
              {selected && locationArtwork.get(selected.id) ? <img alt="" src={assetUrl(locationArtwork.get(selected.id) ?? '')} /> : null}
              <p className="rpg-atlas-location-description">{selectedDescription}</p>
              {selected ? <div className="rpg-atlas-map-entry">{isLocationMap ? <button type="button" onClick={returnToWorldMap}>Return to World Map</button> : locationMapArtwork.has(selected.id) ? <button type="button" onClick={enterLocationMap}>Enter {selectedLabel} Map</button> : <p>Generate this location's detailed map to open its local view.</p>}</div> : null}
              {selected ? <p className="rpg-atlas-zoom-hint">{locationMapArtwork.has(selected.id) ? `Zoom past ${Math.round(LOCATION_MAP_ZOOM * 100)}% to inspect this location in detail.` : 'Generate this location’s detailed map to enable deep zoom.'}</p> : null}
              <dl>
                <div><dt>Area ID</dt><dd>{selected?.id}</dd></div>
                <div><dt>Blueprint</dt><dd>{selectedBlueprint?.map_id ?? 'Not authored'}</dd></div>
                <div><dt>Status</dt><dd>{selectedBlueprint?.status ?? 'Needs blueprint'}</dd></div>
                <div><dt>Revision</dt><dd>{selectedBlueprint?.blueprint_revision ?? '—'}</dd></div>
              </dl>
              {selectedBlueprint?.findings.length ? <p className="rpg-world-catalog-error">{selectedBlueprint.findings.length} reconciliation finding(s) need review.</p> : <p>{selectedBlueprint ? 'All active references reconcile.' : 'Open authoring tools to create the first semantic blueprint.'}</p>}
            </aside>
          </div>
        ) : null}
      </section>
      <details className="rpg-visual-map-authoring">
        <summary>Open semantic blueprint authoring</summary>
        <RpgWorldMapAuthoringPanel worldId={worldId} />
      </details>
    </div>
  );
}
