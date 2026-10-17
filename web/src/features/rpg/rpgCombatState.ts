import type { RpgEncounterPreview, RpgHeroSummaryPreview, RpgPartyMemberPreview } from './rpgUiState';

export interface RpgCombatActionPreview {
  label: string;
  icon: string;
  command: string;
  disabled: boolean;
  reason?: string;
}

export interface RpgCombatantPreview {
  name: string;
  role: string;
  hpLabel: string;
  hpPercent: number;
  status: string;
  tone: 'ally' | 'enemy' | 'neutral';
}

export interface RpgCombatSurfacePreview {
  active: boolean;
  source: 'live' | 'preview';
  title: string;
  statusLabel: string;
  roundLabel: string;
  activeActorLabel: string;
  initiativeQueue: string[];
  combatants: RpgCombatantPreview[];
  actions: RpgCombatActionPreview[];
  resultDeltas: string[];
}

interface RpgCombatSurfaceSources {
  encounter: RpgEncounterPreview;
  heroSummary: RpgHeroSummaryPreview;
  partyMembers: RpgPartyMemberPreview[];
}

export function createRpgCombatSurfaceState({ encounter, heroSummary, partyMembers }: RpgCombatSurfaceSources): RpgCombatSurfacePreview {
  const active = isActiveEncounter(encounter);
  const enemyNames = active ? parseEnemyNames(encounter.detail) : [];
  const allyNames = [heroSummary.name, ...partyMembers.slice(0, 2).map((member) => member.name)].filter(Boolean);
  const initiativeQueue = active ? interleaveInitiative(allyNames, enemyNames) : [];

  return {
    active,
    source: encounter.source,
    title: active ? encounter.title : 'No active combat',
    statusLabel: active ? 'Combat turn gate active' : 'Exploration mode',
    roundLabel: active ? 'Round tracked by simulation' : 'No combat round',
    activeActorLabel: active ? `${initiativeQueue[0] ?? heroSummary.name} has tactical focus` : 'No active actor',
    initiativeQueue,
    combatants: active ? buildCombatants(enemyNames, partyMembers) : [],
    actions: buildCombatActions(active, encounter.title),
    resultDeltas: active
      ? ['Use the action composer to submit deterministic combat commands.', encounter.detail, 'Combat deltas will appear in the story and history logs after the turn resolves.']
      : ['No combat deltas for the selected session.'],
  };
}

function isActiveEncounter(encounter: RpgEncounterPreview): boolean {
  if (encounter.source !== 'live') {
    return false;
  }

  const text = `${encounter.title} ${encounter.detail}`.toLowerCase();
  return !['no active combat', 'all quiet', 'inactive', 'resolved', 'complete', 'completed'].some((token) => text.includes(token));
}

function parseEnemyNames(detail: string): string[] {
  const combatantsMatch = detail.match(/combatants:\s*(.+)$/i);
  const source = combatantsMatch?.[1] ?? detail;
  const candidates = source
    .split(/,|\band\b/i)
    .map((item) => item.trim().replace(/[.!]$/u, ''))
    .filter(Boolean)
    .filter((item) => !/^status:/i.test(item));

  return candidates.length ? candidates.slice(0, 4) : ['Unknown hostile'];
}

function interleaveInitiative(allies: string[], enemies: string[]): string[] {
  const queue: string[] = [];
  const max = Math.max(allies.length, enemies.length);
  for (let index = 0; index < max; index += 1) {
    if (allies[index]) {
      queue.push(allies[index]);
    }
    if (enemies[index]) {
      queue.push(enemies[index]);
    }
  }
  return queue.slice(0, 6);
}

function buildCombatants(enemyNames: string[], partyMembers: RpgPartyMemberPreview[]): RpgCombatantPreview[] {
  const enemies = enemyNames.map((name, index) => ({
    name,
    role: 'Hostile',
    hpLabel: index === 0 ? 'HP unknown' : 'Guarded',
    hpPercent: Math.max(28, 68 - index * 14),
    status: index === 0 ? 'Threatening' : 'Watching',
    tone: 'enemy' as const,
  }));
  const allies = partyMembers.slice(0, 2).map((member) => ({
    name: member.name,
    role: member.role,
    hpLabel: member.hp,
    hpPercent: member.percent,
    status: 'Ready',
    tone: 'ally' as const,
  }));

  return [...enemies, ...allies].slice(0, 6);
}

function buildCombatActions(active: boolean, encounterTitle: string): RpgCombatActionPreview[] {
  const disabledReason = active ? undefined : 'Only available during active combat.';
  return [
    {
      label: 'Attack',
      icon: '⚔',
      command: `Attack the most immediate threat in ${encounterTitle}.`,
      disabled: !active,
      reason: disabledReason,
    },
    {
      label: 'Defend',
      icon: '🛡',
      command: 'Take a defensive stance and protect the weakest party member.',
      disabled: !active,
      reason: disabledReason,
    },
    {
      label: 'Use ability',
      icon: '✦',
      command: 'Use my best available combat ability against the highest-priority enemy.',
      disabled: !active,
      reason: disabledReason,
    },
    {
      label: 'Inspect enemy',
      icon: '⌕',
      command: 'Study the enemy formation, wounds, and tactical weaknesses before acting.',
      disabled: !active,
      reason: disabledReason,
    },
    {
      label: 'Rally party',
      icon: '☯',
      command: 'Call out a tactical plan and rally the party to focus fire safely.',
      disabled: !active,
      reason: disabledReason,
    },
    {
      label: 'Escape',
      icon: '⇥',
      command: 'Look for a safe escape route and withdraw if the party can do so without exposing anyone.',
      disabled: !active,
      reason: disabledReason,
    },
  ];
}
