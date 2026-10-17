import type { RpgMapDefinition } from './api/rpgMapClient';
import './RpgMapHierarchyNav.css';

export function RpgMapHierarchyNav({
  definition,
  onNavigate,
}: {
  definition: RpgMapDefinition;
  onNavigate: (mapId: string) => void;
}) {
  return (
    <nav aria-label="Map hierarchy" className="rpg-map-breadcrumbs">
      {definition.parent_map_id ? (
        <>
          <button onClick={() => onNavigate(definition.parent_map_id!)} type="button">
            ← Back to {humanizeMapId(definition.parent_map_id)}
          </button>
          <span aria-hidden="true">/</span>
        </>
      ) : null}
      <span aria-current="page">{humanizeMapId(definition.map_id)}</span>
    </nav>
  );
}

export function RpgMapChildControls({
  childMapId,
  canEnter,
  isApplying,
  onEnter,
  onPeek,
}: {
  childMapId: string;
  canEnter: boolean;
  isApplying: boolean;
  onEnter: () => void;
  onPeek: () => void;
}) {
  return (
    <div role="group" className="rpg-map-child-controls" aria-label="Map hierarchy actions">
      <button className="rpg-secondary-button" onClick={onPeek} type="button">
        Peek inside
      </button>
      <button className="rpg-primary-button" disabled={!canEnter || isApplying} onClick={onEnter} type="button">
        {isApplying ? 'Entering…' : `Enter ${humanizeMapId(childMapId)}`}
      </button>
    </div>
  );
}

export function humanizeMapId(mapId: string): string {
  const value = mapId.includes(':') ? mapId.split(':').slice(1).join(' ') : mapId;
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}
