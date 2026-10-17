import type { RpgNewGameRequest } from './rpgSessionClient';
import { withRpgGenesisContract as withBaseRpgGenesisContract } from './rpgGenesis';

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function titleize(value: unknown, fallback: string): string {
  const text = asString(value, fallback).replace(/[_-]/g, ' ');
  return text.replace(/\b\w/g, (char) => char.toUpperCase());
}

function hasLegacySummaryMarkers(value: unknown): boolean {
  if (typeof value !== 'string') {
    return false;
  }
  const starterMarker = ['Starter', 'gear:'].join(' ');
  const statsMarker = ['Sta', 'ts:'].join('');
  return value.includes(starterMarker) || value.includes(statsMarker);
}

function renderSummary(genesis: Record<string, unknown>): string {
  const identity = asRecord(genesis.identity);
  const drivers = asRecord(genesis.drivers);
  const motivation = asRecord(drivers.motivation);
  const story = asRecord(genesis.story_options);
  const world = asRecord(genesis.world_options);
  const name = asString(identity.name, 'Alyndra');
  const archetype = titleize(drivers.archetype, 'Adventurer');
  const origin = titleize(identity.origin, 'Unknown Origin');
  const primary = titleize(motivation.primary, 'Survival').toLowerCase();
  const hook = titleize(story.opening_hook, 'Tavern Rumor').toLowerCase();
  const location = titleize(world.starting_location, 'Rusty Flagon Tavern');
  return `${name} begins as a ${archetype} from ${origin}, driven by ${primary}, opening with ${hook} near ${location}.`;
}

export function withRpgGenesisContract(request: RpgNewGameRequest = {}): RpgNewGameRequest {
  const promoted = withBaseRpgGenesisContract(request) as RpgNewGameRequest & { genesis?: Record<string, unknown> };
  if (!promoted.genesis || !hasLegacySummaryMarkers(promoted.generated_class_summary)) {
    return promoted;
  }
  return {
    ...promoted,
    generated_class_summary: renderSummary(promoted.genesis),
  };
}
