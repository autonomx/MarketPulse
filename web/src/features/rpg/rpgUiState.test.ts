import { describe, expect, it } from 'vitest';
import type { AssetListResponse, JobListResponse, ReportListResponse } from '../../api/client';
import { createRpgWorkspaceState, progressPercent, rpgTurnOutputText, safeSessionId } from './rpgUiState';
import { fixture } from '../../test/fixture';

describe('rpg UI state', () => {
  it('keeps preview data until live RPG sessions are available', () => {
    const state = createRpgWorkspaceState({});

    expect(state.jobCards).toHaveLength(3);
    expect(state.jobCards[0]).toMatchObject({ source: 'preview', title: 'rpg.turn' });
    expect(state.selectedSessionSummary).toMatchObject({ source: 'preview', title: 'Preview campaign' });
    expect(state.heroSummary).toMatchObject({ source: 'preview', name: 'Alyndra' });
    expect(state.worldStateRows.map((row) => row.label)).toContain('Calendar / Season');
    expect(state.worldStateRows.map((row) => row.label)).toContain('Hazards');
  });

  it('normalizes live RPG sessions, jobs, assets, and reports for the workspace', () => {
    const inventory = {
      sessions: [
        { session_id: 'session:older', updated_at: '2026-06-15T00:00:00Z', title: 'Older session', location: 'Old Road', turn_count: 3 },
        {
          session_id: 'session:live',
          updated_at: '2026-06-16T00:00:00Z',
          title: 'Live campaign',
          location: 'Rusty Flagon Tavern',
          summary: 'Bran is waiting near the bar with news about the quarry.',
          turn_count: 12,
          checkpoint_path: 'checkpoints/live.json',
        },
      ],
    };
    const jobs = {
      jobs: [
        {
          id: 'job:rpg',
          module: 'rpg',
          type: 'rpg.turn',
          status: 'running',
          resource_class: 'gpu:llm',
          priority: 0,
          progress: { current: 1, total: 4 },
          stages: [{ id: 'narrate', label: 'Generate narration', resource_class: 'gpu:llm', status: 'running' }],
          created_at: '2026-06-16T00:00:00Z',
          updated_at: '2026-06-16T00:00:00Z',
        },
        { id: 'job:chat', module: 'chat', type: 'chat.generate', status: 'queued', resource_class: 'gpu:llm', priority: 0, created_at: '2026-06-16T00:00:00Z', updated_at: '2026-06-16T00:00:00Z' },
      ],
    } as JobListResponse;
    const assets = {
      assets: [
        { id: 'asset:rpg', module: 'rpg', type: 'rpg_checkpoint', mime_type: 'application/json', file_name: 'session.json', created_at: '2026-06-16T00:00:00Z' },
        { id: 'asset:chat', module: 'chat', type: 'chat_transcript', mime_type: 'application/json', file_name: 'session.json', created_at: '2026-06-16T00:00:00Z' },
      ],
    } as AssetListResponse;
    const reports = {
      reports: [
        { id: 'rpg/autoplay.json', kind: 'rpg_autoplay', path: 'reports/rpg/autoplay.json', size_bytes: 42 },
        { id: 'chat/summary.json', kind: 'chat_summary', path: 'reports/chat/summary.json', size_bytes: 42 },
      ],
    } as ReportListResponse;

    const state = createRpgWorkspaceState({ inventory, jobs, assets, reports });

    expect(state.sessions).toHaveLength(2);
    expect(state.sessionSummaries[0]).toMatchObject({ id: 'session:live', title: 'Live campaign', location: 'Rusty Flagon Tavern', turnLabel: 'Turn 12', updatedAt: '2026-06-16 00:00 UTC' });
    expect(state.worldStateRows[0]).toMatchObject({ label: 'Calendar / Season', value: 'Not tracked yet' });
    expect(state.checkpointSummary).toMatchObject({ label: 'Latest checkpoint', detail: 'session.json' });
    expect(state.rpgJobs).toHaveLength(1);
    expect(state.rpgAssets).toHaveLength(1);
    expect(state.rpgReports).toHaveLength(1);
    expect(state.jobCards[0]).toMatchObject({ id: 'job:rpg', progress: 25, source: 'live', title: 'rpg.turn' });
  });

  it('binds a live session to its published World Forge world', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [{
          session_id: 'session:vesper',
          title: 'Alyndra — Tidebreak Docks',
          state: {
            published_world: {
              world_id: 'world:vesper-9-city-of-borrowed-minds',
              world_release: 1,
            },
          },
        }],
      },
      selectedSessionId: 'session:vesper',
    });

    expect(state.selectedSessionSummary.worldId).toBe(
      'world:vesper-9-city-of-borrowed-minds',
    );
  });

  it('projects failed job reasons into RPG job cards', () => {
    const jobs = {
      jobs: [
        {
          id: 'job:rpg-report-failed',
          module: 'rpg',
          type: 'rpg.report.last10',
          status: 'failed',
          resource_class: 'cpu',
          priority: 0,
          progress: { current: 2, total: 3 },
          stages: [
            { id: 'load-session', label: 'Load RPG session', resource_class: 'cpu', status: 'done' },
            {
              id: 'collect-turns',
              label: 'Collect last 10 turns',
              resource_class: 'cpu',
              status: 'failed',
              error: { code: 'session_not_found', message: 'RPG session could not be loaded.', retryable: false },
            },
          ],
          created_at: '2026-06-16T00:00:00Z',
          updated_at: '2026-06-16T00:00:00Z',
          error: { code: 'report_failed', message: 'Last 10 turn report failed before ZIP creation.', retryable: false },
        },
        {
          id: 'job:rpg-stage-failed',
          module: 'rpg',
          type: 'rpg.turn',
          status: 'failed',
          resource_class: 'gpu:llm',
          priority: 0,
          stages: [
            {
              id: 'narrate',
              label: 'Generate narration',
              resource_class: 'gpu:llm',
              status: 'failed',
              error: { code: 'provider_unavailable', message: 'Narration provider is unavailable.', retryable: true },
            },
          ],
          created_at: '2026-06-16T00:00:00Z',
          updated_at: '2026-06-16T00:00:00Z',
        },
      ],
    } as JobListResponse;

    const state = createRpgWorkspaceState({ jobs });

    expect(state.jobCards[0]).toMatchObject({
      id: 'job:rpg-report-failed',
      errorDetail: 'Last 10 turn report failed before ZIP creation.',
    });
    expect(state.jobCards[1]).toMatchObject({
      id: 'job:rpg-stage-failed',
      errorDetail: 'Narration provider is unavailable.',
    });
  });

  it('orders active RPG jobs before older completed job cards', () => {
    const jobs = {
      jobs: [
        {
          id: 'job:completed-newer',
          module: 'rpg',
          type: 'rpg.turn',
          status: 'completed',
          resource_class: 'gpu:llm',
          priority: 0,
          progress: { current: 4, total: 4 },
          stages: [],
          created_at: '2026-06-16T00:10:00Z',
          updated_at: '2026-06-16T00:20:00Z',
        },
        {
          id: 'job:running-current',
          module: 'rpg',
          type: 'rpg.report.last10',
          status: 'running',
          resource_class: 'cpu',
          priority: 0,
          progress: { current: 1, total: 3 },
          stages: [{ id: 'collect-turns', label: 'Collect last 10 turns', resource_class: 'cpu', status: 'running' }],
          created_at: '2026-06-16T00:05:00Z',
          updated_at: '2026-06-16T00:06:00Z',
        },
      ],
    } as JobListResponse;

    const state = createRpgWorkspaceState({ jobs });

    expect(state.jobCards.map((job) => job.id)).toEqual(['job:running-current', 'job:completed-newer']);
    expect(state.jobCards[0]).toMatchObject({ progress: 33, status: 'running', title: 'rpg.report.last10' });
  });

  it('renders environment snapshot rows without moving reputation into world state', () => {
    const inventory = {
      sessions: [
        {
          session_id: 'world-live',
          title: 'World rail campaign',
          location: 'Rusty Flagon Tavern',
          updated_at: '2026-06-16T00:00:00Z',
          state: {
            environment_snapshot: {
              region_id: 'market_road',
              calendar: { season_label: 'Early Spring', time_label: 'Day 1' },
              display: {
                season: 'Early Spring',
                day_time: 'Day 1',
                weather: 'Rain',
                temperature: '7C',
                wind: 'Light',
                visibility: 'Interior',
                light: 'Lamp Lit',
                terrain: 'Interior Floor',
                context: 'Indoor',
              },
              context: { location_label: 'Rusty Flagon Tavern' },
            },
            player: { name: 'Mira Vale', reputation: { label: 'Trusted', score: 61 } },
            relationships: [{ name: 'Bran', stance: 'Ally', score: 82 }],
            encounter: { status: 'active', title: 'Road encounter', enemies: [{ name: 'Lookout' }] },
          },
        },
      ],
    };

    const state = createRpgWorkspaceState({ inventory, selectedSessionId: 'world-live' });
    const rows = Object.fromEntries(state.worldStateRows.map((row) => [row.label, row.value]));

    expect(rows['Calendar / Season']).toBe('Early Spring');
    expect(rows['Day / Time']).toBe('Day 1');
    expect(rows.Region).toBe('market_road');
    expect(rows.Weather).toBe('Rain');
    expect(rows.Temperature).toBe('7C');
    expect(rows.Reputation).toBeUndefined();
    expect(state.npcRelationships[0]).toEqual({ name: 'Bran', stance: 'Ally', score: 82 });
    expect(state.encounter).toMatchObject({ icon: '⚔', source: 'live', title: 'Road encounter', detail: 'Combatants: Lookout' });
  });

  it('derives player rail content from live session state when available', () => {
    const inventory = {
      sessions: [
        {
          session_id: 'player-live',
          title: 'Player state campaign',
          updated_at: '2026-06-16T00:00:00Z',
          state: {
            player: {
              name: 'Mira Vale',
              level: 4,
              class: 'Scout',
              background: 'Caravan outrider',
              hp: { current: 42, max: 50 },
              stamina: { current: 31, max: 40 },
              mana: { current: 8, max: 20 },
              xp: 350,
              xp_to_next: 500,
              currency: { gold: 17, silver: 5, copper: 2 },
              reputation: { label: 'Trusted', score: 18 },
              equipment: [{ name: 'Shortbow', slot: 'weapon' }],
              inventory: { items: [{ name: 'Ration', quantity: 3 }] },
            },
            party: [{ name: 'Bran', role: 'Innkeeper', level: 2, hp: 18, max_hp: 22 }],
            quests: [{ title: 'Find the Quarry Trail', objective: 'Ask Bran about the old quarry road.', status: 'active' }],
          },
          simulation_state: {
            climate_survival: {
              format_version: 'n1231_climate_survival_state_v1',
              runtime_enforced: true,
              survival: {
                hunger: 72,
                thirst: 41,
                fatigue: 86,
                warnings: ['hunger_high', 'fatigue_high'],
              },
            },
          },
        },
      ],
    };

    const state = createRpgWorkspaceState({ inventory, selectedSessionId: 'player-live' });

    expect(state.heroSummary).toMatchObject({ source: 'live', avatar: 'M', name: 'Mira Vale', subtitle: 'Level 4 • Scout', origin: 'Caravan outrider', xpLabel: '350 / 500', xpPercent: 70, gold: '17g 5s 2c', renown: 'Trusted' });
    expect(state.heroStats).toEqual([
      { label: 'HP', value: '42 / 50', percent: 84, tone: 'danger' },
      { label: 'Stamina', value: '31 / 40', percent: 78, tone: 'success' },
      { label: 'Mana', value: '8 / 20', percent: 40, tone: 'mana' },
    ]);
    expect(state.equippedGear[0]).toMatchObject({ name: 'Shortbow', slot: 'Weapon' });
    expect(state.partyMembers[0]).toMatchObject({ avatar: 'B', name: 'Bran', role: 'Lv. 2 Innkeeper', hp: '18 / 22', percent: 82 });
    expect(state.activeQuests[0]).toMatchObject({ title: 'Find the Quarry Trail', detail: 'Ask Bran about the old quarry road.' });
    expect(state.inventoryItems[0]).toMatchObject({ label: 'Ration', count: '3' });
    expect(state.survival).toMatchObject({ source: 'live', status: 'Critical', warnings: ['Hunger High', 'Fatigue High'] });
    expect(state.survival.needs).toEqual([
      { id: 'hunger', label: 'Hunger', percent: 72, severity: 'warning', value: '72 / 100' },
      { id: 'thirst', label: 'Thirst', percent: 41, severity: 'stable', value: '41 / 100' },
      { id: 'fatigue', label: 'Fatigue', percent: 86, severity: 'critical', value: '86 / 100' },
    ]);
    expect(state.survival.actions.map((action) => action.command)).toEqual(['I eat rations', 'I drink water', 'I rest']);
  });

  it('hydrates a bounded session summary from the selected session detail', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [
          {
            manifest: { session_id: 'created-live', title: 'Elara - Rusty Flagon' },
            state: { world: {}, scene: {} },
          },
        ],
      },
      jobs: fixture({ jobs: [], has_more: false }),
      selectedSessionId: 'created-live',
      selectedSession: {
        manifest: { session_id: 'created-live', title: 'Elara - Rusty Flagon' },
        state: {
          ability_tree: {
            abilities: [{ ability_id: 'recon_aimed_shot', icon: '✦', name: 'Aimed Shot' }],
          },
          encounter: { status: 'inactive', title: 'No active combat', summary: 'All quiet for now.' },
          hotbar: { 1: 'recon_aimed_shot' },
          party: [],
          player: {
            name: 'Elara',
            level: 1,
            class: 'Frontier Scout',
            background: 'Wanderer',
            currency: { gold: 0 },
            equipment: [{ name: 'Travel cloak', slot: 'clothing' }],
            inventory: [{ name: 'Trail rations', quantity: 3 }],
            renown: 'Unknown (0)',
            resources: {
              hp: { current: 92, max: 92 },
              stamina: { current: 91, max: 91 },
              mana: { current: 30, max: 30 },
            },
            xp: { current: 0, max: 100 },
          },
          quests: [{ id: 'tavern_rumor', title: 'Rumor at the Rusty Flagon', status: 'active', objective: 'Ask Bran which rumor is true.' }],
          quick_actions: ['Talk to Bran'],
          relationships: [],
        },
      },
    });

    expect(state.heroSummary).toMatchObject({ source: 'live', name: 'Elara', subtitle: 'Level 1 • Frontier Scout', xpLabel: '0 / 100', gold: '0g' });
    expect(state.heroStats.map((stat) => stat.value)).toEqual(['92 / 92', '91 / 91', '30 / 30']);
    expect(state.equippedGear[0]).toMatchObject({ name: 'Travel cloak', slot: 'Clothing' });
    expect(state.partyMembers).toEqual([]);
    expect(state.activeQuests[0]).toMatchObject({ title: 'Rumor at the Rusty Flagon', detail: 'Ask Bran which rumor is true.' });
    expect(state.quickActions).toEqual([{ command: 'Talk to Bran', icon: '☯', label: 'Talk' }]);
    expect(state.inventoryItems).toEqual([{ count: '3', icon: '🥩', label: 'Trail rations' }]);
    expect(state.hotbarAbilities).toEqual([{ abilityId: 'recon_aimed_shot', description: undefined, icon: '✦', key: '1', label: 'Aimed Shot' }]);
    expect(state.npcRelationships).toEqual([]);
    expect(state.encounter).toMatchObject({ source: 'live', title: 'No active combat', detail: 'All quiet for now.' });
    expect(state.jobCards).toEqual([]);
  });

  it('prefers canonical purchased inventory and currency over stale legacy player fields', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [{ manifest: { session_id: 'purchase-live', title: 'Elara - Rusty Flagon' } }],
      },
      jobs: fixture({ jobs: [], has_more: false }),
      selectedSessionId: 'purchase-live',
      selectedSession: {
        manifest: { session_id: 'purchase-live', title: 'Elara - Rusty Flagon' },
        state: {
          player: {
            name: 'Elara',
            currency: { gold: 0, silver: 10, copper: 0 },
            inventory: [{ id: 'ration', name: 'Trail rations', quantity: 3 }],
          },
        },
        simulation_state: {
          player_state: {
            name: 'Elara',
            currency: { gold: 0, silver: 5, copper: 0 },
            inventory_state: {
              currency: { gold: 0, silver: 5, copper: 0 },
              items: [
                { item_id: 'ration', name: 'Trail rations', qty: 3 },
                { item_id: 'dried_rations', name: 'Dried rations', qty: 1 },
              ],
            },
          },
        },
      },
    });

    expect(state.heroSummary.gold).toBe('0g 5s 0c');
    expect(state.inventoryItems.map(({ count, label }) => ({ count, label }))).toEqual([
      { count: '3', label: 'Trail rations' },
      { count: '1', label: 'Dried rations' },
    ]);
  });

  it('projects registered service offers from the latest turn contract', () => {
    const state = createRpgWorkspaceState({
      inventory: { sessions: [{ session_id: 'service-live', title: 'Tavern' }] },
      jobs: fixture({ jobs: [], has_more: false }),
      selectedSessionId: 'service-live',
      selectedSession: {
        manifest: { session_id: 'service-live' },
        runtime_state: {
          last_turn_contract: {
            presentation: {
              available_actions: [
                { label: 'Hot stew - 3 silver', command: 'I buy hot stew from Bran' },
              ],
            },
          },
        },
      },
    });

    expect(state.quickActions).toHaveLength(1);
    expect(state.quickActions[0]).toMatchObject({
      command: 'I buy hot stew from Bran',
      label: 'Hot stew - 3 silver',
    });
  });

  it('projects selected-session RPG turn commands and responses into the live timeline', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [{ session_id: 'session-live', title: 'Live campaign', updated_at: '2026-06-22T01:00:00Z' }],
      },
      jobs: fixture<JobListResponse>({
        has_more: false,
        jobs: [
          {
            id: 'job:turn-live',
            module: 'rpg',
            type: 'rpg.turn',
            status: 'completed',
            resource_class: 'gpu:llm',
            priority: 0,
            stages: [],
            input_ref: { session_id: 'session-live' },
            input_payload: { command: 'I ask Bran how he is.' },
            output_refs: [{
              type: 'rpg_turn_response',
              content: (
                'Bran smiles and says he is well.\n\n'
                + 'Action: You ask Bran how he is.\n\n'
                + 'Result: You ask Bran how he is.'
              ),
            }],
            created_at: '2026-06-22T01:19:36Z',
            updated_at: '2026-06-22T01:19:39Z',
            completed_at: '2026-06-22T01:19:39Z',
          },
          {
            id: 'job:other-session',
            module: 'rpg',
            type: 'rpg.turn',
            status: 'completed',
            resource_class: 'gpu:llm',
            priority: 0,
            stages: [],
            input_ref: { session_id: 'session-other' },
            input_payload: { command: 'This belongs elsewhere.' },
            output_refs: [{ type: 'rpg_turn_response', content: 'Wrong session response.' }],
            created_at: '2026-06-22T01:18:00Z',
            updated_at: '2026-06-22T01:18:01Z',
            completed_at: '2026-06-22T01:18:01Z',
          },
          {
            id: 'job:turn-older',
            module: 'rpg',
            type: 'rpg.turn',
            status: 'completed',
            resource_class: 'gpu:llm',
            priority: 0,
            stages: [],
            input_ref: { session_id: 'session-live' },
            input_payload: { command: 'I greet Bran.' },
            output_refs: [{ type: 'rpg_turn_response', content: 'Bran nods in greeting.' }],
            created_at: '2026-06-22T01:17:00Z',
            updated_at: '2026-06-22T01:17:01Z',
            completed_at: '2026-06-22T01:17:01Z',
          },
        ],
      }),
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        runtime_state: {
          player_journal: {
            entries: [
              {
                entry_id: 'journal:day:1',
                day: 1,
                day_label: 'Day 1',
                title: "A Cautious Wanderer's Journal",
                text: 'I kept my eyes open and measured each choice. Bran shared what he knew about the road.',
                time: { absolute_day: 1, time_label: '08:00' },
                voice: { label: 'Wanderer', temperament: 'cautious', genre: 'fantasy' },
              },
            ],
          },
        },
        state: {
          player: { name: 'Elara' },
          timeline: [{ title: 'Conversation continues', detail: 'Elara and Bran catch up beside the bar.', turn: 2 }],
        },
      },
    });

    expect(state.recentEvents).toEqual(['Elara and Bran catch up beside the bar.']);
    expect(state.storyMessages).toEqual([
      { avatar: 'E', speaker: 'Elara (You)', text: 'I greet Bran.', tone: 'player' },
      { avatar: 'B', speaker: 'Bran', text: 'Bran nods in greeting.', tone: 'npc' },
      { avatar: 'E', speaker: 'Elara (You)', text: 'I ask Bran how he is.', tone: 'player' },
      { avatar: 'B', speaker: 'Bran', text: 'Bran smiles and says he is well.', tone: 'npc' },
    ]);
    expect(state.journalEntries).toEqual([
      {
        detail: 'I kept my eyes open and measured each choice. Bran shared what he knew about the road.',
        time: 'Day 1 • 08:00',
        title: "A Cautious Wanderer's Journal",
      },
    ]);
    expect(state.narrativeLogEntries.slice(0, 2)).toEqual([
      { detail: 'I ask Bran how he is.', time: '2026-06-22 01:19 UTC', title: 'Player message' },
      { detail: 'Bran smiles and says he is well.', time: '2026-06-22 01:19 UTC', title: 'Bran response' },
    ]);
    expect(state.recentEvents).not.toContain('This belongs elsewhere.');
    expect(state.recentEvents).not.toContain('Wrong session response.');
  });

  it('preserves the latest persisted interaction turns with authoritative speakers', () => {
    const interaction = (id: string, input: string, speaker: string, line: string) => ({
      interaction_id: id,
      kind: 'npc_dialogue',
      player_input: input,
      speaker,
      visible_response: {
        narration: `${speaker} considers the question.`,
        messages: [{ kind: 'npc_dialogue', speaker, text: line }],
      },
    });
    const state = createRpgWorkspaceState({
      inventory: { sessions: [{ session_id: 'session-live', title: 'Live campaign' }] },
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        state: { player: { name: 'Alyndra' } },
        runtime_state: {
          recent_interactions: [
            interaction('interaction:79', 'How is business?', 'Bran', 'Steady enough.'),
            interaction('interaction:80', 'Any troubles lately?', 'Bran', 'Nothing unusual.'),
          ],
        },
      },
      jobs: fixture({ jobs: [], has_more: false }),
    });

    expect(state.storyMessages.filter((message) => message.tone === 'player').map((message) => message.text)).toEqual([
      'How is business?',
      'Any troubles lately?',
    ]);
    expect(state.storyMessages.filter((message) => message.tone === 'npc').map((message) => message.speaker)).toEqual([
      'Bran',
      'Bran',
    ]);
    expect(state.storyMessages.map((message) => message.interactionId)).toContain('interaction:79');
  });

  it('merges canonical mechanic fields without hiding projected player identity', () => {
    const state = createRpgWorkspaceState({
      inventory: { sessions: [{ session_id: 'session-live', title: 'Live campaign' }] },
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        state: {
          player: {
            name: 'Alyndra',
            class: 'Ranger',
            background: 'Wanderer',
            currency: { gold: 0, silver: 10, copper: 0 },
          },
        },
        simulation_state: {
          player_state: { currency: { gold: 0, silver: 8, copper: 5 } },
        },
      },
      jobs: fixture({ jobs: [], has_more: false }),
    });

    expect(state.heroSummary.name).toBe('Alyndra');
    expect(state.heroSummary.subtitle).toContain('Ranger');
    expect(state.heroSummary.gold).toBe('0g 8s 5c');
  });

  it('does not project empty array RPG turn responses into the live timeline', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [{ session_id: 'session-live', title: 'Live campaign', updated_at: '2026-06-22T01:00:00Z' }],
      },
      jobs: fixture<JobListResponse>({
        has_more: false,
        jobs: [
          {
            id: 'job:empty-response',
            module: 'rpg',
            type: 'rpg.turn',
            status: 'completed',
            resource_class: 'gpu:llm',
            priority: 0,
            stages: [],
            input_ref: { session_id: 'session-live' },
            input_payload: { command: 'Listen to the hearth-side conversation' },
            output_refs: [{ type: 'rpg_turn_response', content: '[]' }],
            created_at: '2026-06-22T01:19:36Z',
            updated_at: '2026-06-22T01:19:39Z',
            completed_at: '2026-06-22T01:19:39Z',
          },
        ],
      }),
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        state: { player: { name: 'Elara' } },
      },
    });

    expect(state.storyMessages).toEqual([
      { avatar: 'E', speaker: 'Elara (You)', text: 'Listen to the hearth-side conversation', tone: 'player' },
    ]);
    expect(state.storyMessages.some((message) => message.text === '[]')).toBe(false);
  });

  it('projects foreground-record turn jobs while the durable session refreshes', () => {
    const state = createRpgWorkspaceState({
      inventory: { sessions: [{ session_id: 'session-live', title: 'Live campaign' }] },
      jobs: fixture<JobListResponse>({
        has_more: false,
        jobs: [{
          id: 'job:foreground-turn',
          module: 'rpg',
          type: 'rpg.turn.foreground_record',
          status: 'completed',
          resource_class: 'cpu',
          priority: 0,
          stages: [],
          input_ref: { session_id: 'session-live' },
          input_payload: { command: 'Ask the innkeeper about rooms.' },
          output_refs: [{ type: 'rpg_turn_response', content: 'The innkeeper lists two rooms and their prices.' }],
          created_at: '2026-06-22T01:19:36Z',
          updated_at: '2026-06-22T01:19:39Z',
          completed_at: '2026-06-22T01:19:39Z',
        }],
      }),
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        state: { player: { name: 'Alyndra' } },
      },
    });

    expect(state.storyMessages.map((message) => message.text)).toEqual([
      'Ask the innkeeper about rooms.',
      'The innkeeper lists two rooms and their prices.',
    ]);
  });

  it('retains twelve persisted interactions instead of replacing intermediate turns', () => {
    const interactions = Array.from({ length: 12 }, (_, index) => ({
      interaction_id: `interaction:${index + 1}`,
      kind: 'npc_dialogue',
      player_input: `Player turn ${index + 1}`,
      speaker: 'Innkeeper',
      visible_response: {
        narration: '',
        messages: [{ kind: 'npc_dialogue', speaker: 'Innkeeper', text: `Reply ${index + 1}` }],
      },
    }));
    const state = createRpgWorkspaceState({
      inventory: { sessions: [{ session_id: 'session-live', title: 'Live campaign' }] },
      jobs: fixture({ jobs: [], has_more: false }),
      selectedSessionId: 'session-live',
      selectedSession: {
        manifest: { session_id: 'session-live', title: 'Live campaign' },
        state: { player: { name: 'Alyndra' } },
        runtime_state: { recent_interactions: interactions },
      },
    });

    expect(state.storyMessages.filter((message) => message.tone === 'player')).toHaveLength(12);
    expect(state.storyMessages.map((message) => message.text)).toContain('Player turn 2');
    expect(state.storyMessages.map((message) => message.text)).toContain('Reply 11');
  });

  it('does not leak the previous campaign conversation while a new selection loads', () => {
    const state = createRpgWorkspaceState({
      inventory: {
        sessions: [
          {
            session_id: 'session-old',
            title: 'Old campaign',
            timeline: [{ title: 'Old conversation', detail: 'Bran remembers the previous campaign.' }],
          },
        ],
      },
      jobs: fixture<JobListResponse>({
        has_more: false,
        jobs: [
          {
            id: 'job:old-turn',
            module: 'rpg',
            type: 'rpg.turn',
            status: 'completed',
            resource_class: 'gpu:llm',
            priority: 0,
            stages: [],
            input_ref: { session_id: 'session-old' },
            input_payload: { command: 'Ask Bran about yesterday.' },
            output_refs: [{ type: 'rpg_turn_response', content: 'Bran answers the old question.' }],
            created_at: '2026-06-22T01:00:00Z',
            updated_at: '2026-06-22T01:00:01Z',
            completed_at: '2026-06-22T01:00:01Z',
          },
        ],
      }),
      selectedSessionId: 'session-new',
    });

    expect(state.selectedSessionSummary).toMatchObject({ id: 'session-new', title: 'Loading selected campaign' });
    expect(state.storyMessages).toEqual([]);
    expect(state.recentEvents).not.toContain('Bran remembers the previous campaign.');
    expect(state.narrativeLogEntries).toEqual([]);
  });

  it('derives safe session labels and bounded progress percentages', () => {
    expect(safeSessionId({ name: 'named-session' }, 0)).toBe('named-session');
    expect(safeSessionId({}, 1)).toBe('session:2');
    expect(progressPercent({ current: 3, total: 4 })).toBe(75);
    expect(progressPercent({ current: 9, total: 4 })).toBe(100);
    expect(progressPercent({ current: 1, total: 0 })).toBe(0);
  });
});

describe('rpgTurnOutputText', () => {
  it('reads the reply from the foreground record the gateway completes', () => {
    expect(rpgTurnOutputText({ type: 'rpg_turn_response', turn_response: { response: 'The door creaks open.' } })).toBe('The door creaks open.');
    expect(rpgTurnOutputText({ type: 'rpg_turn_response', turn_response: { visible_response: { plain_text: 'Rain falls.' } } })).toBe('Rain falls.');
  });

  it('still reads outputs that carry the text', () => {
    expect(rpgTurnOutputText({ type: 'rpg_turn_response', content: 'You see a door.' })).toBe('You see a door.');
    expect(rpgTurnOutputText(undefined)).toBeUndefined();
  });
});
