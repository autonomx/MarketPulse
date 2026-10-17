import type {
  RpgScenarioRevision,
  RpgWorldDetailResponse,
  RpgWorldRelease,
} from './api/rpgWorldLibraryClient';

export interface WorldLocationOption {
  id: string;
  label: string;
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

export function number(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function certification(release: RpgWorldRelease | undefined): Record<string, unknown> {
  return record(record(release?.document).certification);
}

export function matchingRelease(
  detail: RpgWorldDetailResponse,
  scenario: RpgScenarioRevision,
): RpgWorldRelease | undefined {
  const compatibleRelease = number(record(scenario.document).compatible_release);
  return detail.releases.find((release) => (
    release.world_revision === scenario.world_revision
    && (!compatibleRelease || release.release === compatibleRelease)
  ));
}

export function worldLocationOptions(
  detail: RpgWorldDetailResponse | undefined,
): WorldLocationOption[] {
  if (!detail) return [];
  const revision = record(detail.revisions[0]?.document);
  const canonEntities = record(record(revision.canon).entities);
  const manifestEntities = record(record(revision.entity_manifest).entities);
  const entities = { ...manifestEntities, ...canonEntities };
  const ids: string[] = [];
  const seen = new Set<string>();
  const labels = new Map<string, string>();
  const add = (value: unknown, label?: unknown) => {
    const id = text(value);
    if (id && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
    const resolvedLabel = text(label);
    if (id && resolvedLabel && !labels.has(id)) labels.set(id, resolvedLabel);
  };

  for (const value of array(record(revision.topology).locations)) add(value);
  for (const [entityId, entity] of Object.entries(entities)) {
    const row = record(entity);
    if (text(row.kind).toLowerCase() === 'location') {
      add(entityId, text(row.name, text(row.title)));
    }
  }
  for (const requirement of array(revision.blueprint_requirements)) {
    add(record(requirement).location_id);
  }
  for (const blueprint of detail.map_blueprints) {
    add(record(blueprint.document).location_id);
  }
  for (const topicId of ['locations', 'places', 'regions']) {
    for (const topic of detail.topics.filter((candidate) => candidate.topic_id === topicId)) {
      const content = record(topic.content);
      for (const value of [...array(content.entities), ...array(content.locations)]) {
        const row = record(value);
        const locationId = text(
          row.location_id,
          text(row.id, text(row.entity_id, typeof value === 'string' ? value : '')),
        );
        add(locationId, text(row.name, text(row.title)));
      }
    }
  }

  return ids.map((id) => {
    const entity = record(entities[id]);
    const name = labels.get(id) ?? text(entity.name, text(entity.title));
    return { id, label: name ? `${name} (${id})` : id };
  });
}

function mapSlug(locationId: string): string {
  return locationId
    .split(':')
    .pop()
    ?.replace(/[^a-zA-Z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    || 'location';
}

export function defaultMapBlueprint(locationId: string): Record<string, unknown> {
  return {
    schema_version: 'rpg_map_blueprint_v1',
    map_id: `map:${mapSlug(locationId)}:ground_floor`,
    location_id: locationId,
    level: 'interior',
    navigation_kind: 'square_grid',
    required_portal_ids: ['portal:front_door'],
    required_route_ids: [],
    required_spawn_point_ids: ['spawn:arrival'],
    required_zone_ids: ['zone:main'],
    required_object_ids: [],
    required_hazard_ids: [],
    size_profile: 'medium',
    directives: {},
    metadata: {},
  };
}
