import type { RpgCapability, RpgNewGameRequest } from './rpgSessionClient';

const GENESIS_VERSION = 'rpg_genesis_v2';

const CAPABILITY_TALENT_IDS: Partial<Record<RpgCapability, string>> = {
  combat: 'action_readiness',
  influence: 'social_leverage',
  knowledge: 'field_knowledge',
  recon: 'reconnaissance',
  support: 'party_support',
  survival: 'survival_sense',
  technical: 'technical_handling',
};

const BUILD_STARTER_TAGS: Record<string, string[]> = {
  balanced_adventurer: ['travel_supplies', 'close_weapon'],
  ranger: ['ranged_weapon', 'survival_tool', 'travel_supplies'],
  silver_tongue: ['field_notes', 'travel_supplies'],
  warrior: ['close_weapon', 'survival_tool', 'travel_supplies'],
};

interface GenesisTalentInput {
  id?: unknown;
  rank?: unknown;
}

interface LooseRequest extends RpgNewGameRequest {
  genesis?: Record<string, unknown>;
  flaw?: string | null;
  motivation?: Record<string, unknown>;
  motivation_primary?: string;
  motivation_target?: string | null;
  opening_hook?: string;
  opening_pace?: string;
  origin?: string;
  relationship_preset?: string;
  starter_gear?: string[];
  starter_gear_tags?: string[];
  story_options?: Record<string, unknown>;
  system_options?: Record<string, unknown>;
  talents?: GenesisTalentInput[];
  values?: string[];
  world_forge?: Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function asBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function asInteger(value: unknown, fallback: number): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) ? Math.round(numeric) : fallback;
}

function normalizeStats(value: unknown): Record<string, number> {
  const source = asRecord(value);
  return {
    strength: Number(source.strength ?? 10),
    agility: Number(source.agility ?? 10),
    endurance: Number(source.endurance ?? 10),
    intellect: Number(source.intellect ?? 10),
    charisma: Number(source.charisma ?? 10),
    perception: Number(source.perception ?? 10),
    archery: Number(source.archery ?? 8),
    survival: Number(source.survival ?? 8),
  };
}

function normalizeValues(value: unknown): string[] {
  const values = Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0) : [];
  return values.length > 0 ? values.map((entry) => entry.trim()) : ['agency'];
}

function normalizeProvidedTalents(value: unknown): Array<{ id: string; rank: number }> {
  if (!Array.isArray(value)) {
    return [];
  }
  const talents: Array<{ id: string; rank: number }> = [];
  const seen = new Set<string>();
  for (const entry of value) {
    const record = asRecord(entry);
    const id = asString(record.id, '').replace('-', '_');
    if (!id || seen.has(id)) {
      continue;
    }
    seen.add(id);
    talents.push({ id, rank: Math.max(1, asInteger(record.rank, 1)) });
  }
  return talents;
}

function buildTalents(request: LooseRequest): Array<{ id: string; rank: number }> {
  const provided = normalizeProvidedTalents(request.talents);
  if (provided.length > 0) {
    return provided;
  }
  const talents: Array<{ id: string; rank: number }> = [];
  const seen = new Set<string>();
  const addTalent = (capability: unknown, rank: number): void => {
    const key = asString(capability, '').replace('-', '_');
    const id = CAPABILITY_TALENT_IDS[key as RpgCapability] ?? key;
    if (!id || seen.has(id)) {
      return;
    }
    seen.add(id);
    talents.push({ id, rank });
  };
  addTalent(request.primary_capability, 2);
  for (const capability of request.secondary_capabilities ?? []) {
    addTalent(capability, 1);
  }
  return talents;
}

function addTag(tags: Set<string>, tag: string): void {
  if (tag.trim()) {
    tags.add(tag.trim());
  }
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0).map((entry) => entry.trim())
    : [];
}

function fallbackGearTags(request: LooseRequest): string[] {
  const provided = stringList(request.starter_gear_tags);
  if (provided.length > 0) {
    return provided;
  }
  const starterGear = stringList(request.starter_gear);
  if (starterGear.length > 0) {
    return starterGear;
  }
  const player = asRecord(request.player);
  const buildKey = asString(player.build, '').replace('-', '_');
  const tags = new Set<string>(BUILD_STARTER_TAGS[buildKey] ?? ['travel_supplies']);
  const capabilities = [request.primary_capability, ...(request.secondary_capabilities ?? [])];
  if (capabilities.includes('combat')) addTag(tags, 'close_weapon');
  if (capabilities.includes('recon')) addTag(tags, 'ranged_weapon');
  if (capabilities.includes('survival')) addTag(tags, 'survival_tool');
  if (capabilities.includes('knowledge') || capabilities.includes('technical')) addTag(tags, 'field_notes');
  return Array.from(tags);
}

function normalizeMotivation(request: LooseRequest, storyOptions: Record<string, unknown>): Record<string, unknown> {
  const provided = asRecord(request.motivation);
  const primary = asString(
    provided.primary ?? request.motivation_primary ?? storyOptions.opening_hook ?? request.opening_hook,
    'survival',
  );
  const target = asString(
    provided.target ?? request.motivation_target ?? storyOptions.relationship_preset ?? request.relationship_preset,
    '',
  );
  return {
    primary,
    target: target || null,
    intensity: Math.max(1, Math.min(100, asInteger(provided.intensity, 100))),
    fulfilled: asBoolean(provided.fulfilled, false),
  };
}

function normalizeWorldForge(request: LooseRequest): Record<string, unknown> {
  const provided = asRecord(request.world_forge);
  const depth = asString(provided.depth, 'standard');
  return {
    enabled: asBoolean(provided.enabled, true),
    depth: ['quick', 'standard', 'epic'].includes(depth) ? depth : 'standard',
    background_expansion: asBoolean(provided.background_expansion, false),
    use_hermes: asBoolean(provided.use_hermes, true),
    require_consistency_audit: asBoolean(provided.require_consistency_audit, true),
    require_opening_dossiers: asBoolean(provided.require_opening_dossiers, true),
    max_parallel_jobs:
      provided.max_parallel_jobs == null
        ? null
        : Math.max(1, Math.min(4, asInteger(provided.max_parallel_jobs, 4))),
    custom_directives: stringList(provided.custom_directives),
  };
}

export function withRpgGenesisContract(request: RpgNewGameRequest = {}): RpgNewGameRequest {
  const loose = request as LooseRequest;
  if (loose.genesis) {
    return request;
  }
  const player = asRecord(request.player);
  const storyOptions = asRecord(loose.story_options);
  const systemOptions = asRecord(loose.system_options);
  const features = asRecord(request.features);
  const seed = typeof request.seed === 'number' ? request.seed : null;
  const genesis = {
    contract_version: GENESIS_VERSION,
    campaign_template: asString(request.campaign_template, 'deterministic_rpg_campaign'),
    genre: request.genre ?? null,
    tone: asString(request.tone, 'heroic adventure'),
    identity: {
      name: asString(player.name, 'Alyndra'),
      pronouns: asString(player.pronouns, 'she/her'),
      background: asString(player.background ?? request.background, 'wanderer'),
      origin: asString(loose.origin ?? player.origin ?? player.background ?? request.background, 'wanderer'),
      power_source: request.power_source ?? null,
    },
    drivers: {
      archetype: asString(player.build, asString(request.generated_class_name, 'balanced_adventurer')),
      motivation: normalizeMotivation(loose, storyOptions),
      flaw: typeof loose.flaw === 'string' && loose.flaw.trim() ? loose.flaw.trim() : null,
      talents: buildTalents(loose),
      values: normalizeValues(loose.values),
    },
    initial_stats: normalizeStats(loose.initial_stats),
    starter_gear_tags: fallbackGearTags(loose),
    story_options: {
      opening_hook: asString(storyOptions.opening_hook ?? loose.opening_hook, 'tavern_rumor'),
      opening_pace: asString(storyOptions.opening_pace ?? loose.opening_pace, 'balanced'),
      relationship_preset: asString(storyOptions.relationship_preset ?? loose.relationship_preset, 'unknown_outsider'),
    },
    world_options: {
      world_profile: null,
      starting_location: asString(request.starting_location, 'rusty_flagon_tavern'),
      difficulty: request.difficulty ?? 'normal',
      world_activity: request.world_activity ?? 'standard',
      economy_pressure: request.economy_pressure ?? 'normal',
      combat_lethality: request.combat_lethality ?? 'normal',
      seed,
    },
    world_forge: normalizeWorldForge(loose),
    system_options: {
      autosave: asBoolean(systemOptions.autosave ?? features.autosave, true),
      companions: asBoolean(systemOptions.companions ?? request.companions_enabled, true),
      permadeath: asBoolean(systemOptions.permadeath ?? request.permadeath, false),
      validator: asBoolean(systemOptions.grounding ?? features.validator, true),
      background_soft_audit: asBoolean(systemOptions.softAudit ?? features.background_soft_audit, true),
      llm_narration: asBoolean(systemOptions.narration ?? features.llm_narration, true),
      image_generation: asBoolean(systemOptions.images ?? features.image_generation, false),
      tts: asBoolean(systemOptions.tts ?? features.tts, false),
      stt: asBoolean(systemOptions.stt ?? features.stt, false),
    },
  };
  return { ...request, genesis } as RpgNewGameRequest;
}
