import type { AssetListResponse, JobListResponse, ReportListResponse } from '../../api/client';
import { jobProgressPercent } from '../../api/jobProgress';

export interface RpgHeroSummaryPreview {
  avatar: string;
  name: string;
  subtitle: string;
  origin: string;
  xpLabel: string;
  xpPercent: number;
  gold: string;
  renown: string;
  source: 'live' | 'preview';
}

export interface RpgStatPreview {
  label: string;
  value: string;
  percent: number;
  tone: 'danger' | 'success' | 'mana';
}

export interface RpgSurvivalNeedPreview {
  id: 'hunger' | 'thirst' | 'fatigue';
  label: string;
  percent: number;
  severity: 'stable' | 'warning' | 'critical';
  value: string;
}

export interface RpgSurvivalPreview {
  actions: RpgQuickActionPreview[];
  detail: string;
  needs: RpgSurvivalNeedPreview[];
  source: 'live' | 'preview';
  status: 'Stable' | 'Warning' | 'Critical';
  warnings: string[];
}

export interface RpgGearPreview {
  icon: string;
  name: string;
  slot: string;
}

export interface RpgPartyMemberPreview {
  avatar: string;
  name: string;
  role: string;
  hp: string;
  percent: number;
}

export interface RpgQuestPreview {
  icon: string;
  title: string;
  detail: string;
}

export interface RpgQuickActionPreview {
  label: string;
  icon: string;
  command: string;
}

export interface RpgJournalEntryPreview {
  time: string;
  title: string;
  detail: string;
}

export interface RpgJournalDetailPreview {
  title: string;
  detail: string;
  bullets: string[];
  tags: string[];
}

export interface RpgInventoryItemPreview {
  icon: string;
  count: string;
  label: string;
}

export interface RpgHotbarAbilityPreview {
  key: string;
  icon: string;
  label: string;
  abilityId?: string;
  description?: string;
}

export interface RpgWorldStateRowPreview {
  icon: string;
  label: string;
  value: string;
}

export interface RpgNpcRelationshipPreview {
  name: string;
  stance: string;
  score: number;
}

export interface RpgEncounterPreview {
  icon: string;
  title: string;
  detail: string;
  source: 'live' | 'preview';
}

export interface RpgJobCardPreview {
  id: string;
  title: string;
  status: string;
  progress: number;
  detail: string;
  errorDetail?: string;
  source: 'live' | 'preview';
}

export interface RpgSessionSummaryPreview {
  id: string;
  worldId?: string;
  title: string;
  location: string;
  summary: string;
  updatedAt: string;
  turnLabel: string;
  checkpointLabel: string;
  sortRank: number;
  source: 'live' | 'preview';
}

export interface RpgCheckpointSummaryPreview {
  label: string;
  detail: string;
  source: 'live' | 'preview';
}

export interface RpgStoryMessagePreview {
  id?: string;
  interactionId?: string;
  messageKind?: string;
  messageIndex?: number;
  avatar: string;
  speaker: string;
  text: string;
  tone: 'player' | 'npc' | 'narrator';
}

type RpgSession = Record<string, unknown>;
type RpgJob = JobListResponse['jobs'][number];
type RpgAsset = AssetListResponse['assets'][number];
type RpgReport = NonNullable<ReportListResponse['reports']>[number];

export interface RpgWorkspaceState {
  heroSummary: RpgHeroSummaryPreview;
  heroStats: RpgStatPreview[];
  survival: RpgSurvivalPreview;
  equippedGear: RpgGearPreview[];
  partyMembers: RpgPartyMemberPreview[];
  activeQuests: RpgQuestPreview[];
  quickActions: RpgQuickActionPreview[];
  storyMessages: RpgStoryMessagePreview[];
  recentEvents: string[];
  journalEntries: RpgJournalEntryPreview[];
  narrativeLogEntries: RpgJournalEntryPreview[];
  journalDetail: RpgJournalDetailPreview;
  inventoryItems: RpgInventoryItemPreview[];
  hotbarAbilities: RpgHotbarAbilityPreview[];
  worldStateRows: RpgWorldStateRowPreview[];
  npcRelationships: RpgNpcRelationshipPreview[];
  encounter: RpgEncounterPreview;
  sessionSummaries: RpgSessionSummaryPreview[];
  selectedSessionSummary: RpgSessionSummaryPreview;
  checkpointSummary: RpgCheckpointSummaryPreview;
  sessions: RpgSession[];
  rpgJobs: RpgJob[];
  rpgAssets: RpgAsset[];
  rpgReports: RpgReport[];
  jobCards: RpgJobCardPreview[];
}

export interface RpgWorkspaceStateSources {
  inventory?: { sessions?: RpgSession[] };
  jobs?: JobListResponse;
  assets?: AssetListResponse;
  reports?: ReportListResponse;
  selectedSessionId?: string;
  selectedSession?: RpgSession;
}

interface TimelineEvent {
  time: string;
  title: string;
  detail: string;
  actor?: string;
  kind?: string;
}

const NOT_TRACKED = 'Not tracked yet';
const EMPTY_VISIBLE_RESPONSE_TEXT = new Set(['', '[]', '{}', '[ ]', '{ }', 'null', 'none', 'false', 'true']);

export const previewHeroSummary: RpgHeroSummaryPreview = {
  avatar: 'A',
  name: 'Alyndra',
  subtitle: 'Level 14 • Ranger',
  origin: 'Wanderer of the North',
  xpLabel: '7,450 / 12,000',
  xpPercent: 62,
  gold: '1,248',
  renown: 'Honored (35)',
  source: 'preview',
};

export const heroStats: RpgStatPreview[] = [
  { label: 'HP', value: '86 / 110', percent: 78, tone: 'danger' },
  { label: 'Stamina', value: '72 / 100', percent: 72, tone: 'success' },
  { label: 'Mana', value: '64 / 120', percent: 53, tone: 'mana' },
];

export const equippedGear: RpgGearPreview[] = [
  { icon: '🏹', name: 'Longbow of the Boreal Wind', slot: 'Weapon' },
  { icon: '🛡️', name: 'Shadow Leather Armor +2', slot: 'Armor' },
  { icon: '🦉', name: 'Cloak of the Owl', slot: 'Cloak' },
  { icon: '💍', name: 'Band of Keen Senses', slot: 'Ring' },
];

export const partyMembers: RpgPartyMemberPreview[] = [
  { avatar: 'T', name: 'Thorin Ironfist', role: 'Lv. 14 Warrior', hp: '112 / 140', percent: 80 },
  { avatar: 'E', name: 'Elandra', role: 'Lv. 13 Mage', hp: '78 / 90', percent: 87 },
  { avatar: 'K', name: 'Kael', role: 'Lv. 12 Rogue', hp: '68 / 85', percent: 80 },
];

export const activeQuests: RpgQuestPreview[] = [
  { icon: '◆', title: 'The Frostbound Relic', detail: 'Find the relic in Glimmerdeep.' },
  { icon: '▲', title: 'Secrets in the Snow', detail: 'Investigate the old watchtower.' },
  { icon: '⬟', title: 'Bounty: Icefang Alpha', detail: 'Track down the alpha beast.' },
];

export const quickActions: RpgQuickActionPreview[] = [
  { label: 'Talk', icon: '☯', command: 'Talk to Thorin about the tracks near the archway.' },
  { label: 'Travel', icon: '🧭', command: 'Travel deeper into Glimmerdeep Pass toward the watchtower.' },
  { label: 'Investigate', icon: '⌕', command: 'Investigate the clawed tracks and torn Northern Watch banner.' },
  { label: 'Rest', icon: '♨', command: 'Make a short camp and let the party recover.' },
  { label: 'Inventory', icon: '▣', command: 'Open inventory and check supplies before moving on.' },
  { label: 'Attack', icon: '⚔', command: 'Prepare an ambush in case Icefang scouts are nearby.' },
];

export const previewRecentEvents = [
  'You arrived at Glimmerdeep Pass.',
  'Thorin Ironfist: “Best keep our eyes open. This place gives me the chills.”',
  'You gained 120 XP.',
];

export const previewJournalEntries: RpgJournalEntryPreview[] = [
  { time: 'Day 18 • 09:42', title: 'Arrived at Glimmerdeep Pass', detail: 'Reached the ancient archway.' },
  { time: 'Day 18 • 08:15', title: 'Left Frostpine Hollow', detail: 'Followed the northern trail.' },
  { time: 'Day 17 • 21:30', title: 'Long Rest at Frostpine', detail: 'Recovered after the Icefang fight.' },
];

export const previewJournalDetail: RpgJournalDetailPreview = {
  title: 'Arrived at Glimmerdeep Pass',
  detail: 'The party makes its way through the winding mountain trail and reaches the ancient pass.',
  bullets: ['Discovered location: Glimmerdeep Pass', 'Weather: Cold, Windy', 'Detected tracks near the northern archway'],
  tags: ['Exploration', 'Discovery', 'Travel'],
};

export const inventoryItems: RpgInventoryItemPreview[] = [
  { icon: '🧪', count: '12', label: 'Healing potion' },
  { icon: '💧', count: '7', label: 'Mana tonic' },
  { icon: '🥩', count: '5', label: 'Trail rations' },
  { icon: '🔮', count: '3', label: 'Focus crystal' },
  { icon: '🪢', count: '2', label: 'Rope coil' },
  { icon: '🔥', count: '9', label: 'Torch' },
  { icon: '🍃', count: '4', label: 'Keenleaf' },
  { icon: '📜', count: '6', label: 'Scroll' },
];

export const hotbarAbilities: RpgHotbarAbilityPreview[] = [
  { key: '1', icon: '✦', label: 'Aimed Shot', abilityId: 'recon_aimed_shot', description: 'Take careful aim and turn position into a decisive opening.' },
  { key: '2', icon: '↯', label: 'Frost Arrow', abilityId: 'recon_frost_arrow', description: 'Slow a dangerous target and create a safer window to move.' },
  { key: '3', icon: '☘', label: 'Camouflage', abilityId: 'recon_camouflage', description: 'Blend into terrain and improve stealth or ambush positioning.' },
  { key: '4', icon: '✹', label: 'Radiant Flare', abilityId: 'recon_radiant_flare', description: 'Reveal threats and briefly control the surrounding ground.' },
  { key: '5', icon: '⟡', label: 'Volley', abilityId: 'recon_volley', description: 'Pressure several targets with a rapid spread of arrows.' },
  { key: '6', icon: '⇥', label: 'Dash', abilityId: 'recon_dash', description: 'Spend stamina to cross dangerous ground quickly.' },
];

export const previewSurvival: RpgSurvivalPreview = {
  actions: [
    { label: 'Eat', icon: 'E', command: 'I eat rations' },
    { label: 'Drink', icon: 'D', command: 'I drink water' },
    { label: 'Rest', icon: 'R', command: 'I rest' },
  ],
  detail: 'Needs rise with authoritative turns. Food, water, and rest provide deterministic relief.',
  needs: [
    { id: 'hunger', label: 'Hunger', percent: 24, severity: 'stable', value: '24 / 100' },
    { id: 'thirst', label: 'Thirst', percent: 18, severity: 'stable', value: '18 / 100' },
    { id: 'fatigue', label: 'Fatigue', percent: 31, severity: 'stable', value: '31 / 100' },
  ],
  source: 'preview',
  status: 'Stable',
  warnings: [],
};

export const previewWorldStateRows: RpgWorldStateRowPreview[] = [
  { icon: '☀', label: 'Calendar / Season', value: 'Early Spring' },
  { icon: '◷', label: 'Day / Time', value: 'Day 18 • 09:42' },
  { icon: '⌖', label: 'Region', value: 'mountain_pass' },
  { icon: '≋', label: 'Weather', value: 'Moderate Windy' },
  { icon: '❄', label: 'Temperature', value: '-12°C' },
  { icon: '↝', label: 'Wind', value: 'Strong' },
  { icon: '◌', label: 'Visibility', value: 'Clear' },
  { icon: '✦', label: 'Light', value: 'Daylight' },
  { icon: '▧', label: 'Terrain', value: 'Dry' },
  { icon: '⌂', label: 'Context', value: 'Outdoor • Exposed' },
  { icon: '⚠', label: 'Hazards', value: NOT_TRACKED },
];

export const npcRelationships: RpgNpcRelationshipPreview[] = [
  { name: 'Thorin Ironfist', stance: 'Ally', score: 78 },
  { name: 'Elandra', stance: 'Ally', score: 64 },
  { name: 'Kael', stance: 'Ally', score: 52 },
  { name: 'Captain Bryn', stance: 'Neutral', score: 10 },
];

export const previewEncounter: RpgEncounterPreview = {
  icon: '⚔',
  title: 'No active combat',
  detail: 'All quiet for now.',
  source: 'preview',
};

export const previewJobs: RpgJobCardPreview[] = [
  { id: 'preview-turn', title: 'rpg.turn', status: 'Running', progress: 68, detail: 'Load / Apply turn / Narrate / Checkpoint', source: 'preview' },
  { id: 'preview-narration', title: 'narration.generate', status: 'Running', progress: 42, detail: 'Generating presentation text', source: 'preview' },
  { id: 'preview-world', title: 'world.state.update', status: 'Queued', progress: 0, detail: 'Waiting for turn output', source: 'preview' },
];

export const previewSessionSummary: RpgSessionSummaryPreview = {
  id: 'preview-session',
  title: 'Preview campaign',
  location: 'Glimmerdeep Pass',
  summary:
    'The mountain winds howl through the narrow pass, carrying the scent of pine and snow. Jagged cliffs rise on both sides, and an ancient stone archway stands ahead, half-buried in drifts.',
  updatedAt: 'Day 18 • 09:42',
  turnLabel: 'Turn 12',
  checkpointLabel: 'Saved 2m ago',
  sortRank: 0,
  source: 'preview',
};

export function createRpgWorkspaceState(sources: RpgWorkspaceStateSources): RpgWorkspaceState {
  const sessions = sources.inventory?.sessions ?? [];
  const sessionSummaries = sessions
    .map(toSessionSummary)
    .sort((left, right) => right.sortRank - left.sortRank || left.title.localeCompare(right.title));
  const requestedSessionId = sources.selectedSessionId?.trim();
  const selectedInventorySummary = requestedSessionId
    ? sessionSummaries.find((session) => session.id === requestedSessionId)
    : undefined;
  const selectedResponseSession = requestedSessionId && sessionMatchesId(sources.selectedSession, requestedSessionId)
    ? sources.selectedSession
    : undefined;
  const selectedSessionSummary =
    selectedInventorySummary
    ?? (selectedResponseSession ? toSessionSummary(selectedResponseSession, 0) : undefined)
    ?? (requestedSessionId ? pendingSessionSummary(requestedSessionId) : sessionSummaries[0])
    ?? previewSessionSummary;
  const selectedSession =
    selectedSessionSummary.source === 'live'
      ? selectedResponseSession ?? findSessionById(sessions, selectedSessionSummary.id)
      : undefined;
  const rpgJobs = sources.jobs?.jobs?.filter((job) => job.module === 'rpg') ?? [];
  const rpgAssets = sources.assets?.assets?.filter((asset) => asset.type === 'rpg_checkpoint' || asset.module === 'rpg') ?? [];
  const rpgReports = sources.reports?.reports?.filter((report) => report.kind.includes('rpg') || report.id.includes('rpg')) ?? [];
  const jobCards = rpgJobs.length
    ? [...rpgJobs].sort(compareRpgJobsForCards).map(toJobCard)
    : selectedSessionSummary.source === 'live' ? [] : previewJobs;
  const heroSummary = buildHeroSummary(selectedSession);
  const persistedStoryMessages = buildInteractionStoryMessages(selectedSession, heroSummary);
  const turnMessages = buildRpgTurnJobTimelineEvents(rpgJobs, selectedSessionSummary.id, heroSummary.name);
  const sessionTimeline = buildTimelineEvents(selectedSession);
  const timeline = [
    ...turnMessages,
    ...sessionTimeline,
  ].slice(0, 12);
  const storyMessages = persistedStoryMessages.length
    ? persistedStoryMessages
    : buildStoryMessages(turnMessages, heroSummary);
  const recentEvents = buildRecentEvents(selectedSessionSummary, sessionTimeline);
  const narrativeLogEntries = timeline.map((event) => ({ time: event.time, title: event.title, detail: event.detail }));
  const journalEntries = buildJournalEntries(selectedSessionSummary, selectedSession);
  const journalDetail = buildJournalDetail(selectedSessionSummary, journalEntries);
  const worldStateRows = buildWorldStateRows(selectedSessionSummary, selectedSession);
  const checkpointSummary = buildCheckpointSummary(rpgAssets, selectedSessionSummary);

  return {
    heroSummary,
    heroStats: buildHeroStats(selectedSession),
    survival: buildSurvivalState(selectedSession),
    equippedGear: buildEquippedGear(selectedSession),
    partyMembers: buildPartyMembers(selectedSession),
    activeQuests: buildActiveQuests(selectedSession),
    quickActions: buildQuickActions(selectedSession),
    storyMessages,
    recentEvents,
    journalEntries,
    narrativeLogEntries,
    journalDetail,
    inventoryItems: buildInventoryItems(selectedSession),
    hotbarAbilities: buildHotbarAbilities(selectedSession),
    worldStateRows,
    npcRelationships: buildNpcRelationships(selectedSession),
    encounter: buildEncounter(selectedSession),
    sessionSummaries,
    selectedSessionSummary,
    checkpointSummary,
    sessions,
    rpgJobs,
    rpgAssets,
    rpgReports,
    jobCards,
  };
}

export function safeSessionId(session: Record<string, unknown>, index: number): string {
  const manifest = recordValue(session.manifest);
  const candidate = session.session_id ?? session.id ?? manifest?.session_id ?? manifest?.id ?? session.name ?? `session:${index + 1}`;
  return String(candidate);
}

export const progressPercent = jobProgressPercent;

function findSessionById(sessions: RpgSession[], id: string): RpgSession | undefined {
  return sessions.find((session, index) => safeSessionId(session, index) === id);
}

function pendingSessionSummary(id: string): RpgSessionSummaryPreview {
  return {
    id,
    title: 'Loading selected campaign',
    location: 'Loading location',
    summary: 'Loading the selected campaign without displaying another session.',
    updatedAt: 'Refreshing session',
    turnLabel: 'Turn count pending',
    checkpointLabel: 'Checkpoint pending',
    sortRank: Number.MAX_SAFE_INTEGER,
    source: 'live',
  };
}

function sessionMatchesId(session: RpgSession | undefined, id: string): boolean {
  if (!session) return false;
  const manifest = recordValue(session.manifest);
  const sessionId = firstString(session.session_id, session.id, manifest?.session_id, manifest?.id);
  return sessionId === id;
}

function toSessionSummary(session: RpgSession, index: number): RpgSessionSummaryPreview {
  const manifest = recordValue(session.manifest);
  const metadata = recordValue(session.metadata);
  const state = recordValue(session.state);
  const publishedWorld = recordValue(state?.published_world);
  const payload = recordValue(session.payload);
  const latestTimeline = buildTimelineEvents(session)[0];
  const snapshot = getEnvironmentSnapshot(session);
  const context = recordValue(snapshot?.context);
  const id = safeSessionId(session, index);
  const updatedRaw = firstString(
    session.updated_at,
    session.updatedAt,
    session.modified_at,
    session.created_at,
    manifest?.updated_at,
    manifest?.created_at,
    metadata?.updated_at,
    latestTimeline?.time,
  );
  const turnCount = firstNumber(session.turn_count, session.turns, session.current_turn, metadata?.turn_count, state?.turn_count);
  const checkpointLabel = firstString(session.checkpoint_id, session.checkpoint, session.checkpoint_path, session.storage_path, payload?.checkpoint_id) ?? 'Checkpoint indexed';
  const title = firstString(session.title, session.name, session.label, manifest?.title, session.session_id, session.id, manifest?.session_id, manifest?.id) ?? id;
  const location = firstString(
    context?.location_label,
    session.location,
    session.current_location,
    session.currentLocation,
    metadata?.location,
    metadata?.current_location,
    state?.location,
    payload?.location
  ) ?? NOT_TRACKED;
  const summary =
    firstString(latestTimeline?.detail, session.summary, session.description, session.last_event, metadata?.summary, metadata?.last_event, state?.summary, payload?.summary) ??
    'Live replay-persistence session indexed by the backend.';

  return {
    id,
    worldId: firstString(
      session.world_id,
      metadata?.world_id,
      state?.world_id,
      publishedWorld?.world_id,
      payload?.world_id,
    ),
    title,
    location,
    summary,
    updatedAt: compactTimestamp(updatedRaw) ?? 'Updated time unknown',
    turnLabel: typeof turnCount === 'number' ? `Turn ${turnCount}` : latestTimeline?.time !== undefined ? latestTimeline.time : 'Turn count unknown',
    checkpointLabel,
    sortRank: timestampRank(updatedRaw),
    source: 'live',
  };
}

function buildHeroSummary(session: RpgSession | undefined): RpgHeroSummaryPreview {
  const player = getPlayerRecord(session);
  if (!player) {
    if (!session) return previewHeroSummary;
    return {
      avatar: '?',
      name: 'Character data unavailable',
      subtitle: 'Live session',
      origin: 'Character details are not present in the loaded session.',
      xpLabel: NOT_TRACKED,
      xpPercent: 0,
      gold: NOT_TRACKED,
      renown: NOT_TRACKED,
      source: 'live',
    };
  }
  const stats = recordValue(player.stats);
  const resources = recordValue(player.resources);
  const reputation = recordValue(player.reputation);
  const name = firstString(player.name, player.character_name, player.characterName, player.hero_name, player.id) ?? 'Unnamed hero';
  const level = firstNumber(player.level, stats?.level);
  const role = firstString(player.class, player.role, player.archetype, player.job) ?? 'Adventurer';
  const origin = firstString(player.background, player.origin, player.title, player.description) ?? 'Live RPG character';
  const xpMetric = readMetric([player, stats, resources], ['xp', 'experience'], ['xp_max', 'max_xp', 'xp_to_next', 'next_level_xp', 'experience_max']);
  const xpCurrent = firstNumber(player.xp, player.experience, stats?.xp, resources?.xp);
  const xpLabel = xpMetric ? metricLabel(xpMetric.current, xpMetric.max) : xpCurrent !== undefined ? formatNumber(xpCurrent) : NOT_TRACKED;
  const xpPercent = xpMetric ? metricPercent(xpMetric.current, xpMetric.max) : 0;
  const gold = formatCurrency(player.currency, player.gold, player.money, resources?.gold, resources?.currency);
  const renown =
    firstString(player.renown, player.reputation, player.reputation_label, reputation?.label, reputation?.standing, reputation?.name) ??
    (firstNumber(player.renown, player.reputation_score, reputation?.score) !== undefined
      ? `Renown ${formatNumber(firstNumber(player.renown, player.reputation_score, reputation?.score) ?? 0)}`
      : NOT_TRACKED);
  return { avatar: initialFor(name), name, subtitle: [level !== undefined ? `Level ${level}` : undefined, role].filter(Boolean).join(' • '), origin, xpLabel, xpPercent, gold, renown, source: 'live' };
}

function buildHeroStats(session: RpgSession | undefined): RpgStatPreview[] {
  const player = getPlayerRecord(session);
  if (!player) return session ? emptyHeroStats() : heroStats;
  const stats = recordValue(player.stats);
  const resources = recordValue(player.resources);
  const sources = [player, stats, resources];
  const hp = readMetric(sources, ['hp', 'health', 'hit_points'], ['max_hp', 'max_health', 'health_max', 'hp_max', 'max_hit_points']);
  const stamina = readMetric(sources, ['stamina', 'energy'], ['max_stamina', 'stamina_max', 'max_energy', 'energy_max']);
  const mana = readMetric(sources, ['mana', 'mp'], ['max_mana', 'mana_max', 'max_mp', 'mp_max']);
  return [
    hp ? toStat('HP', hp, 'danger') : emptyStat('HP', 'danger'),
    stamina ? toStat('Stamina', stamina, 'success') : emptyStat('Stamina', 'success'),
    mana ? toStat('Mana', mana, 'mana') : emptyStat('Mana', 'mana'),
  ];
}

function buildSurvivalState(session: RpgSession | undefined): RpgSurvivalPreview {
  if (!session) return previewSurvival;

  const simulation = recordValue(session.simulation_state) ?? {};
  const climate = recordValue(simulation.climate_survival);
  const climateNeeds = recordValue(climate?.survival);
  const playerState = recordValue(simulation.player_state) ?? getPlayerRecord(session) ?? {};
  const playerNeeds = recordValue(playerState.survival_state);
  const resources = recordValue(playerState.resources);
  const legacyNeeds = recordValue(simulation.survival);
  const readNeed = (key: 'hunger' | 'thirst' | 'fatigue') => Math.max(
    0,
    Math.min(100, firstNumber(climateNeeds?.[key], playerNeeds?.[key], resources?.[key], legacyNeeds?.[key]) ?? 0),
  );
  const values = {
    hunger: readNeed('hunger'),
    thirst: readNeed('thirst'),
    fatigue: readNeed('fatigue'),
  };
  const warnings = firstArray(climateNeeds?.warnings, playerNeeds?.warnings, legacyNeeds?.warnings)
    .map((warning) => firstString(warning))
    .filter((warning): warning is string => Boolean(warning))
    .map((warning) => titleCase(warning));
  const maxPressure = Math.max(values.hunger, values.thirst, values.fatigue);
  const status: RpgSurvivalPreview['status'] = maxPressure >= 85
    ? 'Critical'
    : maxPressure >= 60 || warnings.length
      ? 'Warning'
      : 'Stable';
  const need = (id: RpgSurvivalNeedPreview['id'], label: string): RpgSurvivalNeedPreview => ({
    id,
    label,
    percent: values[id],
    severity: values[id] >= 85 ? 'critical' : values[id] >= 60 ? 'warning' : 'stable',
    value: `${values[id]} / 100`,
  });

  return {
    actions: [
      { label: 'Eat', icon: 'E', command: 'I eat rations' },
      { label: 'Drink', icon: 'D', command: 'I drink water' },
      { label: 'Rest', icon: 'R', command: 'I rest' },
    ],
    detail: status === 'Critical'
      ? 'Immediate survival relief is recommended.'
      : status === 'Warning'
        ? 'One or more needs are creating survival pressure.'
        : 'Needs rise with authoritative turns. Relief actions are resolved deterministically.',
    needs: [need('hunger', 'Hunger'), need('thirst', 'Thirst'), need('fatigue', 'Fatigue')],
    source: 'live',
    status,
    warnings,
  };
}

function buildEquippedGear(session: RpgSession | undefined): RpgGearPreview[] {
  if (!session) return equippedGear;
  const player = getPlayerRecord(session);
  const equipment = firstArray(player?.equipment, player?.equipped, player?.gear, recordValue(player?.inventory)?.equipped);
  if (!equipment.length) return [];
  return equipment.slice(0, 5).map((item, index) => {
    const record = recordValue(item) ?? { name: item };
    const slot = firstString(record.slot, record.type, record.category) ?? `Slot ${index + 1}`;
    const name = firstString(record.name, record.label, record.id) ?? `Equipped item ${index + 1}`;
    return { icon: iconForEquipment(slot, name), name, slot: titleCase(slot) };
  });
}

function buildPartyMembers(session: RpgSession | undefined): RpgPartyMemberPreview[] {
  if (!session) return partyMembers;
  const roots = getSessionRecords(session);
  const party = firstArray(...roots.flatMap((root) => [root.party, root.companions, root.party_members]));
  if (!party.length) return [];
  return party.slice(0, 4).map((member, index) => {
    const record = recordValue(member) ?? { name: member };
    const name = firstString(record.name, record.id, record.label) ?? `Companion ${index + 1}`;
    const level = firstNumber(record.level, record.lv);
    const role = firstString(record.role, record.class, record.archetype, record.type) ?? 'Companion';
    const hp = readMetric([record, recordValue(record.stats), recordValue(record.resources)], ['hp', 'health'], ['max_hp', 'max_health', 'health_max']);
    return { avatar: initialFor(name), name, role: [level !== undefined ? `Lv. ${level}` : undefined, role].filter(Boolean).join(' '), hp: hp ? metricLabel(hp.current, hp.max) : 'HP unknown', percent: hp ? metricPercent(hp.current, hp.max) : 50 };
  });
}

function buildActiveQuests(session: RpgSession | undefined): RpgQuestPreview[] {
  if (!session) return activeQuests;
  const roots = getSessionRecords(session);
  const quests = firstArray(...roots.flatMap((root) => [root.quests, root.active_quests, root.objectives, recordValue(root.journal)?.quests]));
  if (!quests.length) return [];
  return quests.slice(0, 4).map((quest, index) => {
    const record = recordValue(quest) ?? { title: quest };
    const title = firstString(record.title, record.name, record.id) ?? `Quest ${index + 1}`;
    const detail = firstString(record.detail, record.description, record.objective, record.next_step, record.summary) ?? 'Objective indexed in live session state.';
    return { icon: questIcon(record.status), title, detail };
  });
}

function buildInventoryItems(session: RpgSession | undefined): RpgInventoryItemPreview[] {
  if (!session) return inventoryItems;
  const player = getPlayerRecord(session);
  const roots = getSessionRecords(session);
  const inventoryState = recordValue(player?.inventory_state);
  const inventory = firstArray(
    inventoryState?.items,
    player?.inventory_items,
    player?.inventory,
    recordValue(player?.inventory)?.items,
    ...roots.map((root) => recordValue(root.inventory)?.items ?? root.items),
  );
  if (!inventory.length) return [];
  return inventory.slice(0, 8).map((item, index) => {
    const record = recordValue(item) ?? { name: item };
    const label = firstString(record.name, record.label, record.id) ?? `Item ${index + 1}`;
    const count = firstNumber(record.count, record.quantity, record.qty, record.amount);
    return { icon: iconForInventory(label), count: count !== undefined ? formatNumber(count) : '1', label };
  });
}

function buildQuickActions(session: RpgSession | undefined): RpgQuickActionPreview[] {
  if (!session) return quickActions;
  const roots = getSessionRecords(session);
  const actions = firstArray(
    ...roots.flatMap((root) => [
      root.quick_actions,
      root.suggested_actions,
      recordValue(root.narrative_affordances)?.suggested_actions,
      recordValue(recordValue(root.last_turn_contract)?.presentation)?.available_actions,
    ]),
  );
  return actions.slice(0, 6).map((action, index) => {
    const record = recordValue(action);
    const command = firstString(record?.command, record?.action, record?.text, action) ?? `Review live action ${index + 1}`;
    const label = firstString(record?.label, record?.title, record?.name) ?? quickActionLabel(command);
    return { label, icon: quickActionIcon(command), command };
  });
}

function buildHotbarAbilities(session: RpgSession | undefined): RpgHotbarAbilityPreview[] {
  if (!session) return hotbarAbilities;
  const roots = getSessionRecords(session);
  const hotbar = firstRecord(...roots.flatMap((root) => [root.hotbar, recordValue(root.ability_state)?.hotbar]));
  const abilities = firstArray(...roots.map((root) => recordValue(root.ability_tree)?.abilities));
  const abilityById = new Map(
    abilities.map((ability) => {
      const record = recordValue(ability) ?? {};
      return [firstString(record.ability_id, record.id) ?? '', record] as const;
    }),
  );
  if (!hotbar) return [];
  return Object.entries(hotbar)
    .sort(([left], [right]) => Number(left) - Number(right))
    .slice(0, 6)
    .map(([slot, value]) => {
      const valueRecord = recordValue(value);
      const abilityId = firstString(valueRecord?.ability_id, valueRecord?.id, value);
      const ability = abilityId ? abilityById.get(abilityId) : undefined;
      return {
        key: slot,
        icon: firstString(ability?.icon, valueRecord?.icon) ?? '✦',
        label: firstString(ability?.name, valueRecord?.name, abilityId ? titleCase(abilityId) : undefined) ?? `Hotbar ${slot}`,
        abilityId,
        description: firstString(ability?.description, valueRecord?.description),
      };
    });
}

function buildRecentEvents(selectedSession: RpgSessionSummaryPreview, timeline: TimelineEvent[]): string[] {
  if (selectedSession.source === 'preview') return previewRecentEvents;
  if (timeline.length) return timeline.slice(0, 4).map((event) => formatTimelineEvent(event));
  return [`Loaded ${selectedSession.title}.`, `Current location: ${selectedSession.location}.`, `${selectedSession.turnLabel} • ${selectedSession.updatedAt}.`];
}

function buildJournalEntries(selectedSession: RpgSessionSummaryPreview, session: RpgSession | undefined): RpgJournalEntryPreview[] {
  if (selectedSession.source === 'preview') return previewJournalEntries;
  const roots = getSessionRecords(session);
  const journal = firstRecord(
    ...roots.flatMap((root) => [root.player_journal, recordValue(root.runtime_state)?.player_journal]),
  );
  const seenDays = new Set<string>();
  const entries = firstArray(journal?.entries)
    .map((value) => recordValue(value))
    .filter((value): value is Record<string, unknown> => Boolean(value))
    .map((entry, index) => {
      const time = recordValue(entry.time);
      const voice = recordValue(entry.voice);
      const day = firstNumber(entry.day, time?.absolute_day) ?? index + 1;
      const dayLabel = firstString(entry.day_label) ?? `Day ${day}`;
      const timeLabel = firstString(time?.time_label);
      const voiceLabel = firstString(voice?.label);
      return {
        time: [dayLabel, timeLabel].filter(Boolean).join(' • '),
        title: firstString(entry.title) ?? (voiceLabel ? `${voiceLabel}'s journal` : 'My journal'),
        detail: firstString(entry.text, entry.summary, entry.detail) ?? 'No account has been written for this day yet.',
      };
    })
    .reverse()
    .filter((entry) => {
      const dayKey = entry.time.split(' • ')[0];
      if (seenDays.has(dayKey)) return false;
      seenDays.add(dayKey);
      return true;
    });
  if (entries.length) return entries;
  return [
    { time: 'Day 1', title: 'My journal', detail: 'The first daily account will be written after the next turn.' },
  ];
}

function buildJournalDetail(selectedSession: RpgSessionSummaryPreview, entries: RpgJournalEntryPreview[]): RpgJournalDetailPreview {
  if (selectedSession.source === 'preview') return previewJournalDetail;
  const latest = entries[0];
  if (latest) {
    return {
      title: latest.title,
      detail: latest.detail,
      bullets: [`Session id: ${selectedSession.id}`, `Location: ${selectedSession.location}`, `${selectedSession.turnLabel} • ${selectedSession.updatedAt}`, `Checkpoint: ${selectedSession.checkpointLabel}`],
      tags: ['Player perspective', 'Daily journal', 'Character voice'],
    };
  }
  return {
    title: `Live session: ${selectedSession.title}`,
    detail: selectedSession.summary,
    bullets: [`Session id: ${selectedSession.id}`, `Location: ${selectedSession.location}`, `${selectedSession.turnLabel} • ${selectedSession.updatedAt}`, `Checkpoint: ${selectedSession.checkpointLabel}`],
    tags: ['Player perspective', 'Daily journal', 'Character voice'],
  };
}

function buildWorldStateRows(selectedSession: RpgSessionSummaryPreview, session: RpgSession | undefined): RpgWorldStateRowPreview[] {
  if (selectedSession.source === 'preview' || !session) return previewWorldStateRows;
  const snapshot = getEnvironmentSnapshot(session);
  if (!snapshot) {
    return environmentRowsFromValues({});
  }
  const calendar = recordValue(snapshot.calendar);
  const weather = recordValue(snapshot.weather);
  const context = recordValue(snapshot.context);
  const display = recordValue(snapshot.display);
  const hazards = firstArray(snapshot.hazards);
  return environmentRowsFromValues({
    season: firstString(display?.season, calendar?.season_label, snapshot.season_label),
    dayTime: firstString(display?.day_time, calendar?.time_label, snapshot.time_label),
    region: firstString(snapshot.region_id),
    weather: firstString(display?.weather, weather?.label, weather?.condition),
    temperature: firstString(display?.temperature, snapshot.temperature_label, snapshot.temperature_c),
    wind: firstString(display?.wind, snapshot.wind),
    visibility: firstString(display?.visibility, snapshot.visibility),
    light: firstString(display?.light, snapshot.light_level),
    terrain: firstString(display?.terrain, snapshot.terrain_condition),
    context: firstString(display?.context, context?.label, context?.exposure),
    hazards: hazards.length ? hazards.map((hazard) => firstString(recordValue(hazard)?.id, hazard)).filter(Boolean).join(', ') : undefined,
  });
}

function environmentRowsFromValues(values: Record<string, string | undefined>): RpgWorldStateRowPreview[] {
  return [
    { icon: '☀', label: 'Calendar / Season', value: values.season ?? NOT_TRACKED },
    { icon: '◷', label: 'Day / Time', value: values.dayTime ?? NOT_TRACKED },
    { icon: '⌖', label: 'Region', value: values.region ?? NOT_TRACKED },
    { icon: '≋', label: 'Weather', value: values.weather ?? NOT_TRACKED },
    { icon: '❄', label: 'Temperature', value: values.temperature ?? NOT_TRACKED },
    { icon: '↝', label: 'Wind', value: values.wind ?? NOT_TRACKED },
    { icon: '◌', label: 'Visibility', value: values.visibility ?? NOT_TRACKED },
    { icon: '✦', label: 'Light', value: values.light ?? NOT_TRACKED },
    { icon: '▧', label: 'Terrain', value: values.terrain ?? NOT_TRACKED },
    { icon: '⌂', label: 'Context', value: values.context ?? NOT_TRACKED },
    { icon: '⚠', label: 'Hazards', value: values.hazards ?? NOT_TRACKED },
  ];
}

function getEnvironmentSnapshot(session: RpgSession | undefined): Record<string, unknown> | undefined {
  const state = recordValue(session?.state);
  const payload = recordValue(session?.payload);
  return firstRecord(session?.environment_snapshot, state?.environment_snapshot, payload?.environment_snapshot);
}

function buildNpcRelationships(session: RpgSession | undefined): RpgNpcRelationshipPreview[] {
  if (!session) return npcRelationships;
  const roots = getSessionRecords(session);
  const candidates = roots.flatMap((root) => [root.npc_relationships, root.relationships, root.social_state, root.social, recordValue(root.npcs)?.relationships, recordValue(root.memory)?.npc_relationships]);
  const relationshipArray = firstArray(...candidates);
  const relationshipRecord = relationshipArray.length ? undefined : firstRecord(...candidates);
  const relationshipItems = relationshipArray.length
    ? relationshipArray
    : relationshipRecord
      ? Object.entries(relationshipRecord).map(([name, value]) => ({ ...(recordValue(value) ?? { score: value }), name }))
      : [];
  if (!relationshipItems.length) return [];
  return relationshipItems.slice(0, 5).map((relationship, index) => {
    const record = recordValue(relationship) ?? { name: relationship };
    const name = firstString(record.name, record.npc, record.character, record.id, record.label) ?? `NPC ${index + 1}`;
    const score = relationshipScore(firstNumber(record.score, record.affinity, record.trust, record.value, record.relationship, record.standing_score));
    const stance = firstString(record.stance, record.status, record.relationship_label, record.label) ?? stanceForScore(score);
    return { name, stance, score };
  });
}

function buildEncounter(session: RpgSession | undefined): RpgEncounterPreview {
  const roots = getSessionRecords(session);
  const encounter = firstRecord(...roots.flatMap((root) => [root.encounter, root.encounter_state, root.current_encounter, root.combat, root.combat_state, recordValue(root.state)?.combat]));
  if (!encounter) {
    return session
      ? { icon: '◇', title: 'No encounter data', detail: 'The live session does not currently expose encounter state.', source: 'live' }
      : previewEncounter;
  }
  const status = firstString(encounter.status, encounter.state, encounter.phase, encounter.mode);
  const enemies = firstArray(encounter.enemies, encounter.opponents, encounter.hostiles, encounter.combatants)
    .map((enemy, index) => firstString(recordValue(enemy)?.name, recordValue(enemy)?.id, enemy) ?? `Combatant ${index + 1}`)
    .slice(0, 3);
  const statusText = String(status ?? '').toLowerCase();
  const active = Boolean(status && !['none', 'idle', 'inactive', 'resolved', 'complete', 'completed'].some((token) => statusText.includes(token)));
  const title = firstString(encounter.title, encounter.name, encounter.label) ?? (active ? 'Active encounter' : 'Encounter indexed');
  const detail = firstString(encounter.summary, encounter.detail, encounter.description, encounter.objective) ?? (enemies.length ? `Combatants: ${enemies.join(', ')}` : status ? `Status: ${status}` : 'Encounter state indexed from live session.');
  return { icon: active ? '⚔' : '◇', title, detail, source: 'live' };
}

function buildCheckpointSummary(assets: RpgAsset[], selectedSession: RpgSessionSummaryPreview): RpgCheckpointSummaryPreview {
  if (!assets.length) {
    return {
      label: selectedSession.source === 'live' ? selectedSession.checkpointLabel : 'Preview only',
      detail: selectedSession.source === 'live' ? `Session checkpoint: ${selectedSession.id}` : 'No RPG checkpoint assets indexed',
      source: selectedSession.source,
    };
  }
  const latestAsset = [...assets].sort((left, right) => timestampRank(right.created_at) - timestampRank(left.created_at))[0];
  return { label: 'Latest checkpoint', detail: latestAsset.file_name || latestAsset.id, source: 'live' };
}

function toJobCard(job: RpgJob): RpgJobCardPreview {
  const errorDetail = jobErrorDetail(job);
  return {
    id: String(job.id),
    title: String(job.type),
    status: String(job.status),
    progress: progressPercent(job.progress),
    detail: job.stages?.map((stage) => stage.label).join(' / ') || String(job.resource_class),
    ...(errorDetail ? { errorDetail } : {}),
    source: 'live',
  };
}

function compareRpgJobsForCards(left: RpgJob, right: RpgJob): number {
  const leftActiveRank = activeJobRank(left.status);
  const rightActiveRank = activeJobRank(right.status);
  if (leftActiveRank !== rightActiveRank) return leftActiveRank - rightActiveRank;
  const leftUpdated = timestampRank(firstString(left.updated_at, left.created_at));
  const rightUpdated = timestampRank(firstString(right.updated_at, right.created_at));
  if (leftUpdated !== rightUpdated) return rightUpdated - leftUpdated;
  return String(left.id).localeCompare(String(right.id));
}

function activeJobRank(status: unknown): number {
  const normalized = String(status ?? '').toLowerCase();
  return ['queued', 'running', 'pending'].includes(normalized) ? 0 : 1;
}

function jobErrorDetail(job: RpgJob): string | undefined {
  const jobError = recordValue(job.error);
  const stageError = job.stages?.map((stage) => recordValue(stage.error)).find(Boolean);
  return firstString(
    jobError?.message,
    jobError?.code,
    stageError?.message,
    stageError?.code,
  );
}

function toStat(label: string, metric: { current: number; max: number }, tone: RpgStatPreview['tone']): RpgStatPreview {
  return { label, value: metricLabel(metric.current, metric.max), percent: metricPercent(metric.current, metric.max), tone };
}

function emptyStat(label: string, tone: RpgStatPreview['tone']): RpgStatPreview {
  return { label, value: NOT_TRACKED, percent: 0, tone };
}

function emptyHeroStats(): RpgStatPreview[] {
  return [emptyStat('HP', 'danger'), emptyStat('Stamina', 'success'), emptyStat('Mana', 'mana')];
}

function buildTimelineEvents(session: RpgSession | undefined): TimelineEvent[] {
  const roots = getSessionRecords(session);
  if (!roots.length) return [];
  const items = firstArray(
    ...roots.flatMap((root) => [root.timeline, root.recent_events, root.events, root.event_log, root.turn_history, root.turns, root.history, root.dialogue_log, root.dialogue, root.logs, recordValue(root.journal)?.entries])
  );
  return items
    .map(toTimelineEvent)
    .filter((event): event is TimelineEvent => Boolean(event))
    .slice(0, 8);
}

function toTimelineEvent(item: unknown, index: number): TimelineEvent | undefined {
  if (typeof item === 'string' && item.trim()) return { time: `Event ${index + 1}`, title: item.trim(), detail: item.trim(), kind: 'event' };
  const record = recordValue(item);
  if (!record) return undefined;
  const actor = firstString(record.actor, record.speaker, record.character, record.npc, record.source);
  const command = firstString(record.command, record.action, record.player_action, record.playerAction, record.input);
  const narration = firstString(record.narration, record.response, record.output, record.summary, record.result, record.text, record.message, record.description);
  const detail = narration ?? command ?? firstString(record.detail, record.title, record.label, record.event) ?? 'RPG event indexed from live session.';
  const title = firstString(record.title, record.label, record.event, record.kind, record.type) ?? (command ? 'Player command' : actor ? `${actor} speaks` : `Session event ${index + 1}`);
  const time = firstString(record.time, record.timestamp, record.created_at, record.updated_at, record.turn_label) ?? turnLabelFor(record, index);
  const kind = firstString(record.kind, record.type, record.category, command ? 'command' : undefined);
  return { time: compactTimestamp(time) ?? time, title, detail, actor, kind };
}

function formatTimelineEvent(event: TimelineEvent): string {
  return event.actor && !event.detail.startsWith(`${event.actor}:`) ? `${event.actor}: ${event.detail}` : event.detail;
}

function turnLabelFor(record: Record<string, unknown>, index: number): string {
  const turn = firstNumber(record.turn, record.turn_count, record.index, record.sequence);
  return turn !== undefined ? `Turn ${turn}` : `Event ${index + 1}`;
}

function getPlayerRecord(session: RpgSession | undefined): Record<string, unknown> | undefined {
  const canonicalPlayer = recordValue(recordValue(session?.simulation_state)?.player_state);
  const roots = getSessionRecords(session);
  const projectedPlayer = firstRecord(...roots.flatMap((root) => [root.player, root.hero, root.character, root.player_state, root.character_state, recordValue(root.state)?.player]));
  if (canonicalPlayer && projectedPlayer) {
    return {
      ...projectedPlayer,
      ...canonicalPlayer,
      resources: {
        ...recordValue(projectedPlayer.resources),
        ...recordValue(canonicalPlayer.resources),
      },
    };
  }
  return canonicalPlayer ?? projectedPlayer;
}

function getSessionRecords(session: RpgSession | undefined): Record<string, unknown>[] {
  if (!session) return [];
  return [
    session,
    recordValue(session.simulation_state),
    recordValue(session.metadata),
    recordValue(session.state),
    recordValue(session.payload),
    recordValue(session.runtime_state),
  ].filter((record): record is Record<string, unknown> => Boolean(record));
}

function readMetric(records: Array<Record<string, unknown> | undefined>, currentKeys: string[], maxKeys: string[]): { current: number; max: number } | undefined {
  for (const record of records) {
    if (!record) continue;
    const nestedMetric = firstRecord(...currentKeys.map((key) => recordValue(record[key])));
    const nestedCurrent = firstNumber(nestedMetric?.current, nestedMetric?.value, nestedMetric?.amount);
    const nestedMax = firstNumber(nestedMetric?.max, nestedMetric?.total, nestedMetric?.maximum);
    if (nestedCurrent !== undefined && nestedMax !== undefined) return { current: nestedCurrent, max: nestedMax };
    const current = firstNumber(...currentKeys.flatMap((key) => [record[key], record[`current_${key}`], record[`current${titleCase(key)}`]]));
    const max = firstNumber(...maxKeys.flatMap((key) => [record[key], record[`maximum_${key}`]]));
    if (current !== undefined && max !== undefined) return { current, max };
  }
  return undefined;
}

function formatCurrency(...values: unknown[]): string {
  const currencyRecord = firstRecord(...values.map((value) => recordValue(value)));
  if (currencyRecord) {
    const gold = firstNumber(currencyRecord.gold, currencyRecord.gp, currencyRecord.coins);
    const silver = firstNumber(currencyRecord.silver, currencyRecord.sp);
    const copper = firstNumber(currencyRecord.copper, currencyRecord.cp);
    if (gold !== undefined || silver !== undefined || copper !== undefined) {
      return [gold !== undefined ? `${formatNumber(gold)}g` : undefined, silver !== undefined ? `${formatNumber(silver)}s` : undefined, copper !== undefined ? `${formatNumber(copper)}c` : undefined]
        .filter(Boolean)
        .join(' ');
    }
  }
  const numeric = firstNumber(...values);
  if (numeric !== undefined) return formatNumber(numeric);
  return firstString(...values) ?? NOT_TRACKED;
}

function buildRpgTurnJobTimelineEvents(jobs: RpgJob[], sessionId: string, playerName: string): TimelineEvent[] {
  if (!sessionId || sessionId === previewSessionSummary.id) return [];
  return jobs
    .filter((job) => {
      const inputRef = recordValue(job.input_ref);
      return (job.type === 'rpg.turn' || job.type === 'rpg.turn.foreground_record')
        && firstString(inputRef?.session_id) === sessionId;
    })
    .flatMap((job) => {
      const inputPayload = recordValue(job.input_payload);
      const command = firstString(inputPayload?.command)?.trim();
      const output = firstArray(job.output_refs)
        .map(recordValue)
        .find((candidate) => candidate && firstString(candidate.type, candidate.kind) === 'rpg_turn_response');
      const response = cleanRpgTurnResponse(rpgTurnOutputText(output));
      const events: TimelineEvent[] = [];
      if (command) {
        events.push({
          actor: playerName,
          detail: command,
          kind: 'player_message',
          time: compactTimestamp(firstString(job.created_at)) ?? 'Queued turn',
          title: 'Player message',
        });
      }
      if (response) {
        const speaker = inferResponseSpeaker(response);
        events.push({
          actor: speaker,
          detail: response,
          kind: speaker === 'Omnix' ? 'narrator_message' : 'npc_message',
          time: compactTimestamp(firstString(job.completed_at, job.updated_at)) ?? 'Completed turn',
          title: speaker === 'Omnix' ? 'Narrator message' : `${speaker} response`,
        });
      }
      return events;
    });
}

/**
 * The reply text of an `rpg_turn_response` job output: the gateway's foreground
 * record carries the turn response (`turn_response`); older outputs carry the text.
 */
export function rpgTurnOutputText(output: Record<string, unknown> | undefined): string | undefined {
  const turn = recordValue(output?.turn_response);
  return firstString(output?.content, output?.text, turn?.response, turn?.content, recordValue(turn?.visible_response)?.plain_text);
}

function cleanRpgTurnResponse(response: string | undefined): string | undefined {
  if (!response) return undefined;
  const paragraphs = response.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean);
  const narrativeParagraphs = paragraphs.filter((paragraph) => !/^(?:Action|Result):\s*/i.test(paragraph));
  const visibleParagraphs = narrativeParagraphs.length ? narrativeParagraphs : paragraphs;
  const text = visibleParagraphs
    .map((paragraph) => paragraph.replace(/,['’](?=\s)/g, ',"').replace(/(\s)['’](?=[A-Z])/g, '$1"'))
    .join('\n\n');
  return text && !EMPTY_VISIBLE_RESPONSE_TEXT.has(text.toLowerCase()) ? text : undefined;
}

function buildStoryMessages(events: TimelineEvent[], hero: RpgHeroSummaryPreview): RpgStoryMessagePreview[] {
  const turns: TimelineEvent[][] = [];
  for (const event of events) {
    if (event.kind === 'player_message' || !turns.length) {
      turns.push([event]);
    } else {
      turns[turns.length - 1].push(event);
    }
  }

  return turns.slice(0, 3).reverse().flat().map((event) => {
    if (event.kind === 'player_message') {
      return { avatar: hero.avatar, speaker: `${hero.name} (You)`, text: event.detail, tone: 'player' };
    }
    const speaker = event.actor ?? 'Omnix';
    const isNarrator = speaker === 'Omnix';
    return {
      avatar: speaker.charAt(0).toUpperCase() || 'O',
      speaker: isNarrator ? 'Omnix (Narrator)' : speaker,
      text: event.detail,
      tone: isNarrator ? 'narrator' : 'npc',
    };
  });
}

function buildInteractionStoryMessages(
  session: RpgSession | undefined,
  hero: RpgHeroSummaryPreview,
): RpgStoryMessagePreview[] {
  const runtime = recordValue(session?.runtime_state);
  const interactions = firstArray(runtime?.recent_interactions);
  if (!interactions.length) return [];
  return interactions.slice(-12).flatMap((item) => {
    const event = recordValue(item);
    if (!event) return [];
    const interactionId = firstString(event.interaction_id);
    const playerInput = firstString(event.player_input);
    const visible = recordValue(event.visible_response);
    const narration = firstString(visible?.narration, event.narration);
    const visibleMessages = firstArray(visible?.messages).map(recordValue).filter(Boolean) as Record<string, unknown>[];
    const result: RpgStoryMessagePreview[] = [];
    if (playerInput) {
      result.push({
        id: interactionId ? `${interactionId}:player` : undefined,
        interactionId,
        messageKind: 'player',
        messageIndex: 0,
        avatar: hero.avatar,
        speaker: `${hero.name} (You)`,
        text: playerInput,
        tone: 'player',
      });
    }
    if (narration) {
      result.push({
        id: interactionId ? `${interactionId}:narration` : undefined,
        interactionId,
        messageKind: 'narration',
        messageIndex: 0,
        avatar: 'O',
        speaker: 'Omnix (Narrator)',
        text: narration,
        tone: 'narrator',
      });
    }
    visibleMessages.forEach((message, index) => {
      const text = firstString(message.text);
      if (!text) return;
      const speaker = firstString(message.speaker, event.speaker) ?? 'Omnix';
      result.push({
        id: interactionId ? `${interactionId}:message:${index}` : undefined,
        interactionId,
        messageKind: firstString(message.kind) ?? 'npc_dialogue',
        messageIndex: index,
        avatar: speaker.charAt(0).toUpperCase() || 'O',
        speaker: speaker === 'Omnix' ? 'Omnix (Narrator)' : speaker,
        text,
        tone: speaker === 'Omnix' ? 'narrator' : 'npc',
      });
    });
    return result;
  }).slice(-40);
}

function inferResponseSpeaker(response: string): string {
  const match = response.match(/^([A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*){0,2})\s+(?=[a-z])/);
  const candidate = match?.[1];
  if (!candidate || /^(?:A|An|He|Her|His|I|It|Its|She|The|Their|They|This|We|You|Your)$/i.test(candidate)) {
    return 'Omnix';
  }
  return candidate;
}

function quickActionLabel(command: string): string {
  const [verb = 'Action'] = command.trim().split(/\s+/);
  return titleCase(verb);
}

function quickActionIcon(command: string): string {
  const normalized = command.toLowerCase();
  if (normalized.startsWith('talk') || normalized.startsWith('ask')) return '☯';
  if (normalized.startsWith('travel') || normalized.startsWith('go')) return '🧭';
  if (/(look|listen|check|search|investigate|inspect)/.test(normalized)) return '⌕';
  if (/(rest|camp)/.test(normalized)) return '♨';
  if (/(attack|fight|ambush)/.test(normalized)) return '⚔';
  return '◇';
}

function metricLabel(current: number, max: number): string {
  return `${formatNumber(current)} / ${formatNumber(max)}`;
}

function metricPercent(current: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((current / max) * 100)));
}

function relationshipScore(value: number | undefined): number {
  if (value === undefined) return 50;
  if (value > 0 && value <= 1) return Math.round(value * 100);
  return Math.max(0, Math.min(100, Math.round(value)));
}

function stanceForScore(score: number): string {
  if (score >= 75) return 'Ally';
  if (score >= 55) return 'Friendly';
  if (score >= 35) return 'Neutral';
  if (score >= 15) return 'Wary';
  return 'Hostile';
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value.replace(/,/g, ''));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function firstRecord(...values: unknown[]): Record<string, unknown> | undefined {
  return values.map(recordValue).find(Boolean);
}

function firstArray(...values: unknown[]): unknown[] {
  for (const value of values) {
    if (Array.isArray(value)) return value;
  }
  return [];
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) return value as Record<string, unknown>;
  return undefined;
}

function compactTimestamp(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (!value.includes('T')) return value;
  const [date, time = ''] = value.split('T');
  const [hour = '00', minute = '00'] = time.split(':');
  return `${date} ${hour}:${minute} UTC`;
}

function timestampRank(value: string | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function initialFor(name: string): string {
  return name.trim().charAt(0).toUpperCase() || 'A';
}

function titleCase(value: string): string {
  return value
    .replace(/[_-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function iconForEquipment(slot: string, name: string): string {
  const text = `${slot} ${name}`.toLowerCase();
  if (text.includes('bow')) return '🏹';
  if (text.includes('armor') || text.includes('shield')) return '🛡️';
  if (text.includes('ring')) return '💍';
  if (text.includes('cloak')) return '🦉';
  if (text.includes('sword') || text.includes('blade')) return '⚔';
  return '◇';
}

function iconForInventory(label: string): string {
  const text = label.toLowerCase();
  if (text.includes('potion') || text.includes('heal')) return '🧪';
  if (text.includes('mana')) return '💧';
  if (text.includes('ration') || text.includes('food')) return '🥩';
  if (text.includes('rope')) return '🪢';
  if (text.includes('torch') || text.includes('fire')) return '🔥';
  if (text.includes('scroll') || text.includes('letter')) return '📜';
  return '▣';
}

function questIcon(status: unknown): string {
  const text = String(status ?? '').toLowerCase();
  if (text.includes('complete')) return '✓';
  if (text.includes('failed')) return '×';
  if (text.includes('urgent')) return '▲';
  return '◆';
}
