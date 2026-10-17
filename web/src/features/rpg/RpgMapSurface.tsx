import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { applyRpgMapAction } from './api/rpgMapActionClient';
import {
  getRpgMapDefinition,
  getRpgMapOverlay,
  type RpgMapActionCapability,
  type RpgMapDefinition,
  type RpgMapObjectDefinition,
  type RpgMapObjectDynamicState,
  type RpgMapOverlay,
  type RpgMapOverlayResponse,
} from './api/rpgMapClient';
import { RpgMapChildControls, RpgMapHierarchyNav } from './RpgMapHierarchyNav';
import { RpgMapViewportSurface } from './RpgMapViewportSurface';
import './RpgMapSurface.css';

interface RpgMapSurfaceProps {
  mapId: string;
  sessionId: string;
}

export function RpgMapSurface({ mapId, sessionId }: RpgMapSurfaceProps) {
  const queryClient = useQueryClient();
  const [viewMapId, setViewMapId] = useState(mapId);
  const [activeObjectId, setActiveObjectId] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const definitionQuery = useQuery({
    queryKey: ['feature', 'rpg', 'map-definition', sessionId, viewMapId],
    queryFn: () => getRpgMapDefinition(viewMapId, undefined, sessionId),
    enabled: Boolean(sessionId && viewMapId),
    staleTime: Number.POSITIVE_INFINITY,
  });
  const overlayQuery = useQuery({
    queryKey: ['feature', 'rpg', 'map-overlay', sessionId, viewMapId],
    queryFn: () => getRpgMapOverlay(sessionId, viewMapId),
    enabled: Boolean(sessionId && viewMapId),
    refetchInterval: 2500,
    refetchIntervalInBackground: false,
  });
  const actionMutation = useMutation({
    mutationFn: (capability: RpgMapActionCapability) => {
      const definitionRevision = definitionQuery.data?.definition_revision ?? '';
      const overlayRevision = overlayQuery.data?.overlay_revision ?? -1;
      return applyRpgMapAction(sessionId, viewMapId, {
        action: capability.type,
        client_action_id: actionId(),
        definition_revision: definitionRevision,
        overlay_revision: overlayRevision,
        route_id: capability.route_id,
        target_object_id: capability.target_object_id,
      });
    },
    onSuccess: (response) => {
      const overlayResponse: RpgMapOverlayResponse = {
        ok: true,
        map_id: response.map_id,
        definition_revision: response.definition_revision,
        overlay_revision: response.overlay_revision,
        session_turn_index: response.session_turn_index,
        overlay: response.overlay,
      };
      queryClient.setQueryData(['feature', 'rpg', 'map-overlay', sessionId, response.map_id], overlayResponse);
      queryClient.setQueryData(['feature', 'rpg', 'session', sessionId], {
        ok: true,
        session_id: sessionId,
        session: response.session,
        game: response.game,
      });
      setViewMapId(response.map_id);
      setActiveObjectId(null);
      setSelectedObjectId(null);
      void queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'replay-inventory'] });
    },
  });

  useEffect(() => {
    setViewMapId(mapId);
  }, [mapId, sessionId]);

  useEffect(() => {
    setActiveObjectId(null);
    setSelectedObjectId(null);
  }, [viewMapId]);

  if (definitionQuery.isPending || overlayQuery.isPending) {
    return <MapStateMessage title="Loading map" detail="Reading the map definition and current session overlay…" />;
  }
  if (definitionQuery.isError) {
    return <MapStateMessage title="Map definition unavailable" detail={errorMessage(definitionQuery.error)} tone="error" />;
  }
  if (overlayQuery.isError) {
    return <MapStateMessage title="Live map state unavailable" detail={errorMessage(overlayQuery.error)} tone="error" />;
  }

  const definition = definitionQuery.data?.definition;
  const overlay = overlayQuery.data?.overlay;
  if (!definition) {
    return <MapStateMessage title="Empty map definition" detail="The server returned a matching revision without map geometry." />;
  }
  if (!overlay) {
    return <MapStateMessage title="Empty map overlay" detail="The selected session did not return live map state." />;
  }

  const revisionMismatch = definition.definition_revision !== overlay.definition_revision;
  const visible = visibleObjects(definition, overlay);
  const selectedObject = visible.find((item) => item.id === selectedObjectId) ?? null;
  const objectState = selectedObject
    ? overlay.object_states?.find((state) => state.object_id === selectedObject.id)
    : undefined;
  const selectedCapabilities = selectedObject
    ? overlay.capabilities.filter((capability) => capability.target_object_id === selectedObject.id)
    : [];
  const selectObject = (objectId: string) => {
    setSelectedObjectId(objectId);
    setActiveObjectId(objectId);
  };
  const enterCapability: RpgMapActionCapability | null = selectedObject?.child_map_id ? {
    type: 'enter',
    enabled: overlay.availability === 'ready' && !revisionMismatch,
    target_object_id: selectedObject.id,
    target_location_id: selectedObject.location_id,
    route_id: null,
    disabled_reason: overlay.availability === 'ready' ? '' : 'map_not_active',
  } : null;

  return (
    <div className="rpg-map-surface">
      <RpgMapHierarchyNav definition={definition} onNavigate={setViewMapId} />
      {revisionMismatch ? (
        <MapStateMessage compact title="Map definition changed" detail="The live overlay references a newer map definition. Refreshing the map before actions are enabled." tone="warning" />
      ) : null}
      {overlay.availability === 'ready' ? null : (
        <MapStateMessage compact title="Live position unavailable" detail={humanizeReason(overlay.unavailable_reason)} tone="warning" />
      )}
      {actionMutation.isError ? (
        <MapStateMessage compact title="Map action rejected" detail={errorMessage(actionMutation.error)} tone="error" />
      ) : null}
      <RpgMapViewportSurface
        activeObjectId={activeObjectId}
        definition={definition}
        onActiveObjectChange={setActiveObjectId}
        onSelectObject={selectObject}
        overlay={revisionMismatch ? { ...overlay, availability: 'stale', capabilities: [] } : overlay}
        selectedObjectId={selectedObjectId}
      />
      <div role="group" className="rpg-map-surface-meta" aria-label="Map revision information">
        <span>{definition.level}</span>
        <span>Definition {shortRevision(definition.definition_revision)}</span>
        <span>Overlay {overlay.overlay_revision}</span>
        <span>Turn {overlay.session_turn_index}</span>
        {overlayQuery.isFetching ? <span>Refreshing live overlay…</span> : null}
        {actionMutation.isPending ? <span>Applying authoritative action…</span> : null}
      </div>
      <SelectedObjectPanel
        capabilities={revisionMismatch ? [] : selectedCapabilities}
        enterCapability={enterCapability}
        isApplyingAction={actionMutation.isPending}
        item={selectedObject}
        objectState={objectState}
        onAction={(capability) => actionMutation.mutate(capability)}
        onClose={() => setSelectedObjectId(null)}
        onPeek={(childMapId) => setViewMapId(childMapId)}
      />
      <AccessibleObjectList
        activeObjectId={activeObjectId}
        definition={definition}
        onActiveObjectChange={setActiveObjectId}
        onSelectObject={selectObject}
        overlay={overlay}
        selectedObjectId={selectedObjectId}
      />
    </div>
  );
}

function SelectedObjectPanel({
  capabilities,
  enterCapability,
  isApplyingAction,
  item,
  objectState,
  onAction,
  onClose,
  onPeek,
}: {
  capabilities: RpgMapActionCapability[];
  enterCapability: RpgMapActionCapability | null;
  isApplyingAction: boolean;
  item: RpgMapObjectDefinition | null;
  objectState?: RpgMapObjectDynamicState;
  onAction: (capability: RpgMapActionCapability) => void;
  onClose: () => void;
  onPeek: (childMapId: string) => void;
}) {
  if (!item) return null;
  return (
    <section aria-label="Selected map object" className="rpg-map-selected-panel">
      <div>
        <p className="eyebrow">Selected location</p>
        <h3>{item.label || item.location_id || item.id}</h3>
        <p>{objectState?.presentation_hint || item.description || `A ${humanizeReason(item.kind)} on the current map.`}</p>
        <small>{item.location_id ?? item.id} • {humanizeReason(objectState?.status ?? 'normal')}</small>
      </div>
      {item.tags.length ? <div role="group" className="rpg-map-object-tags" aria-label="Object tags">{item.tags.map((tag) => <span key={tag}>{humanizeReason(tag)}</span>)}</div> : null}
      {item.child_map_id && enterCapability ? (
        <RpgMapChildControls
          canEnter={enterCapability.enabled}
          childMapId={item.child_map_id}
          isApplying={isApplyingAction}
          onEnter={() => onAction(enterCapability)}
          onPeek={() => onPeek(item.child_map_id!)}
        />
      ) : null}
      <div role="group" className="rpg-map-capability-list" aria-label="Projected map capabilities">
        {capabilities.length ? capabilities.map((capability) => (
          <button
            className={capability.enabled ? 'rpg-map-capability-enabled' : 'rpg-map-capability-disabled'}
            disabled={!capability.enabled || isApplyingAction}
            key={`${capability.type}:${capability.route_id ?? capability.target_object_id}`}
            onClick={() => onAction(capability)}
            title={capability.enabled ? '' : humanizeReason(capability.disabled_reason)}
            type="button"
          >
            {humanizeReason(capability.type)}{capability.enabled ? '' : ` — ${humanizeReason(capability.disabled_reason)}`}
          </button>
        )) : <span>No live actions are projected for this object.</span>}
      </div>
      <button className="rpg-secondary-button" onClick={onClose} type="button">Close selection</button>
    </section>
  );
}

function AccessibleObjectList({ activeObjectId, definition, onActiveObjectChange, onSelectObject, overlay, selectedObjectId }: {
  activeObjectId: string | null;
  definition: RpgMapDefinition;
  onActiveObjectChange: (objectId: string | null) => void;
  onSelectObject: (objectId: string) => void;
  overlay: RpgMapOverlay;
  selectedObjectId: string | null;
}) {
  return (
    <div role="group" className="rpg-map-accessible-list" aria-label="Visible map locations">
      <p className="eyebrow">Visible map objects</p>
      <ul>{visibleObjects(definition, overlay).map((item) => (
        <li key={item.id}><button aria-pressed={selectedObjectId === item.id} className={activeObjectId === item.id ? 'rpg-map-object-list-active' : undefined} onBlur={() => onActiveObjectChange(null)} onClick={() => onSelectObject(item.id)} onFocus={() => onActiveObjectChange(item.id)} onMouseEnter={() => onActiveObjectChange(item.id)} onMouseLeave={() => onActiveObjectChange(null)} type="button">{item.label || item.location_id || item.id}</button></li>
      ))}</ul>
    </div>
  );
}

function MapStateMessage({ compact = false, detail, title, tone = 'neutral' }: { compact?: boolean; detail: string; title: string; tone?: 'neutral' | 'warning' | 'error' }) {
  return <div className={`rpg-map-state-message rpg-map-state-${tone}${compact ? ' rpg-map-state-compact' : ''}`} role="status"><strong>{title}</strong><span>{detail}</span></div>;
}

function visibleObjects(definition: RpgMapDefinition, overlay: RpgMapOverlay): RpgMapObjectDefinition[] {
  if (overlay.availability !== 'ready') return definition.objects;
  const visibleIds = new Set(overlay.visible_object_ids);
  return definition.objects.filter((item) => visibleIds.has(item.id));
}

function actionId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `map-action:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function shortRevision(value: string): string {
  return value.replace(/^sha256:/, '').slice(0, 8) || 'unknown';
}

function humanizeReason(value: string): string {
  return value ? value.replaceAll('_', ' ') : 'The selected session does not expose a valid current map location.';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The map request failed.';
}
