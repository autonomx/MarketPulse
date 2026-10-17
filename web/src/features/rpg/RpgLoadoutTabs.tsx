import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rpgSessionClient, type RpgLoadoutActionRequest, type RpgLaunchResponse } from './api/rpgSessionClient';
import { applyRpgItemCommand, applyRpgItemResolve, fetchRpgAbilityDetails, fetchRpgItemDiagnostics } from './rpgItemApi';
import { RpgItemPanel } from './RpgItemPanel';
import {
  buildMerchantEntryPreviews,
  buildSelectedItemActions,
  type RpgItemUiAction,
  type RpgMerchantEntryPreview,
} from './rpgItemUiState';
import type { RpgHotbarAbilityPreview, RpgInventoryItemPreview } from './rpgUiState';
import './RpgLoadoutTabs.css';

type RpgLoadoutTab = 'inventory' | 'abilities' | 'hotbar' | 'skills' | 'traits' | 'effects' | 'coverage';
type AbilityStatus = 'unlocked' | 'available' | 'locked';

interface RpgLoadoutTabsProps {
  inventoryItems: RpgInventoryItemPreview[];
  hotbarAbilities: RpgHotbarAbilityPreview[];
  isApplyingLoadoutAction?: boolean;
  onApplyLoadoutAction?: (request: RpgLoadoutActionRequest) => void;
  onSelectCommand: (command: string) => void;
  selectedSessionId?: string | null;
}

interface AbilityPreview {
  id: string;
  name: string;
  icon: string;
  description: string;
  kind: 'active' | 'passive' | 'narrative_trait' | string;
  capability: string;
  powerSource: string;
  purpose: string;
  dimensions: string[];
  levelRequired: number;
  rank: number;
  maxRank: number;
  cooldown: number;
  resourceCost: Record<string, number>;
  prerequisites: string[];
  influenceTags: string[];
  hooks: string[];
  categoryName: string;
  isUnlocked: boolean;
  isAvailable: boolean;
  missingPrerequisites: string[];
  status: AbilityStatus;
  statusLabel: string;
}

interface AbilityCategoryPreview {
  id: string;
  name: string;
  capability: string;
  dimensions: string[];
  abilities: AbilityPreview[];
}

interface HotbarSlotPreview {
  slot: string;
  ability?: AbilityPreview;
}

interface SkillProgressionPreview {
  id: string;
  label: string;
  rank: number;
  xp: number;
  source: string;
}

interface EffectPreview {
  id: string;
  name: string;
  detail: string;
  dimensions: string[];
  remaining?: number;
}

interface AbilityCoveragePreview {
  ok: boolean;
  score: number;
  totalObservations: number;
  coveredDimensions: string[];
  missingDimensions: string[];
  dimensionCounts: Record<string, number>;
  sourceCounts: Record<string, number>;
  warnings: string[];
}

interface AbilityOverview {
  className: string;
  treeId: string;
  abilityPoints: number;
  playerLevel: number;
  categories: AbilityCategoryPreview[];
  allAbilities: AbilityPreview[];
  hotbarSlots: HotbarSlotPreview[];
  hotbarAbilities: RpgHotbarAbilityPreview[];
  skills: SkillProgressionPreview[];
  traits: AbilityPreview[];
  activeEffects: EffectPreview[];
  coverage: AbilityCoveragePreview;
  source: 'live' | 'preview';
}

interface LoadoutDetailAction {
  label: string;
  command: string;
  apply?: () => void;
}

const tabs: Array<{ id: RpgLoadoutTab; label: string }> = [
  { id: 'inventory', label: 'Inventory' },
  { id: 'abilities', label: 'Abilities' },
  { id: 'hotbar', label: 'Hotbar' },
  { id: 'skills', label: 'Skills' },
  { id: 'traits', label: 'Traits' },
  { id: 'effects', label: 'Effects' },
  { id: 'coverage', label: 'Coverage' },
];

const REQUIRED_DIMENSIONS = ['resources', 'information', 'relationships', 'access', 'environment', 'position', 'narrative', 'economy', 'world'];
const LOADOUT_ITEM_ACTIONS = new Set(['inspect', 'use', 'equip', 'drop', 'salvage', 'craft', 'modify']);

export function RpgLoadoutTabs({ inventoryItems, hotbarAbilities, isApplyingLoadoutAction = false, onApplyLoadoutAction, onSelectCommand, selectedSessionId }: RpgLoadoutTabsProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<RpgLoadoutTab>('inventory');
  const [activeInventoryIndex, setActiveInventoryIndex] = useState(0);
  const [activeAbilityIndex, setActiveAbilityIndex] = useState(0);
  const [activeHotbarSlot, setActiveHotbarSlot] = useState('1');
  const wasApplyingAction = useRef(false);

  const sessionQuery = useQuery({
    enabled: Boolean(selectedSessionId),
    queryKey: ['feature', 'rpg', 'ability-tree-session', selectedSessionId],
    queryFn: () => rpgSessionClient.getRpgSession(selectedSessionId ?? ''),
    staleTime: 0,
  });
  const itemDiagnosticsQuery = useQuery({
    enabled: Boolean(selectedSessionId) && activeTab === 'inventory',
    queryKey: ['feature', 'rpg', 'item-diagnostics', selectedSessionId],
    queryFn: () => fetchRpgItemDiagnostics(selectedSessionId ?? '', { objectiveLimit: 4, scenarioLimit: 4 }),
    staleTime: 0,
  });
  const abilityOverview = useMemo(
    () => buildAbilityOverview(sessionQuery.data, hotbarAbilities, Boolean(selectedSessionId)),
    [hotbarAbilities, selectedSessionId, sessionQuery.data],
  );
  const displayedHotbarAbilities = abilityOverview.hotbarAbilities.length ? abilityOverview.hotbarAbilities : hotbarAbilities;
  const activeItem = inventoryItems[Math.min(activeInventoryIndex, Math.max(inventoryItems.length - 1, 0))];
  const activeAbility = abilityOverview.allAbilities[Math.min(activeAbilityIndex, Math.max(abilityOverview.allAbilities.length - 1, 0))];
  const coveragePercent = Math.round(abilityOverview.coverage.score * 100);

  const merchantEntries = useMemo(
    () =>
      buildMerchantEntryPreviews(
        firstRecord(
          itemDiagnosticsQuery.data?.merchant,
          itemDiagnosticsQuery.data?.merchant_menu,
          recordValue(itemDiagnosticsQuery.data?.diagnostics)?.merchant,
          recordValue(itemDiagnosticsQuery.data?.diagnostics)?.merchant_menu,
        ),
      ),
    [itemDiagnosticsQuery.data],
  );

  const refreshItemPanelQueries = async () => {
    await Promise.all([
      sessionQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'replay-inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'item-diagnostics', selectedSessionId] }),
    ]);
  };
  const itemPanelMutation = useMutation({
    mutationFn: (request: RpgItemPanelRequest) => {
      if (!selectedSessionId) {
        throw new Error('Select a live RPG session before applying item actions.');
      }
      return applyRpgItemPanelRequest(selectedSessionId, request);
    },
    onSuccess: refreshItemPanelQueries,
  });
  const itemPanelPending = isApplyingLoadoutAction || itemPanelMutation.isPending || itemDiagnosticsQuery.isFetching;
  const panelDisabled = isApplyingLoadoutAction || sessionQuery.isFetching;

  useEffect(() => {
    if (wasApplyingAction.current && !isApplyingLoadoutAction && selectedSessionId) {
      void sessionQuery.refetch();
    }
    wasApplyingAction.current = isApplyingLoadoutAction;
  }, [isApplyingLoadoutAction, selectedSessionId, sessionQuery]);

  return (
    <section className="rpg-card rpg-inventory-card">
      <div className="rpg-tabs" role="tablist" aria-label="Inventory and ability tabs">
        {tabs.map((tab) => (
          <button
            aria-controls={`rpg-${tab.id}-loadout-panel`}
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? 'active' : undefined}
            id={`rpg-${tab.id}-loadout-tab`}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="group" className="rpg-ability-summary" aria-label="Ability tree summary">
        <strong>{abilityOverview.className}</strong>
        <span>
          {abilityOverview.source === 'live'
            ? `Level ${abilityOverview.playerLevel} • ${abilityOverview.abilityPoints} ability point${abilityOverview.abilityPoints === 1 ? '' : 's'} • ${coveragePercent}% dimension coverage`
            : 'Preview ability kit'}
        </span>
        <small>{abilityOverview.treeId}</small>
      </div>

      {activeTab === 'inventory' ? (
        <InventoryPanel
          activeItemIndex={activeInventoryIndex}
          hotbarAbilities={displayedHotbarAbilities}
          inventoryItems={inventoryItems}
          isApplyingLoadoutAction={itemPanelPending}
          itemActions={buildSelectedItemActions({ item: activeItem, selectedSessionId })}
          itemMerchantEntries={merchantEntries}
          onApplyItemAction={(action) => itemPanelMutation.mutate({ kind: 'action', action })}
          onApplyItemMerchantEntry={(entry) => itemPanelMutation.mutate({ kind: 'merchant', entry })}
          onApplyLoadoutAction={onApplyLoadoutAction}
          onSelectCommand={onSelectCommand}
          onSelectItem={setActiveInventoryIndex}
          selectedSessionId={selectedSessionId}
        />
      ) : null}
      {activeTab === 'abilities' ? (
        <AbilitiesPanel
          abilityOverview={abilityOverview}
          activeAbility={activeAbility}
          activeAbilityIndex={activeAbilityIndex}
          isApplyingLoadoutAction={panelDisabled}
          onApplyLoadoutAction={onApplyLoadoutAction}
          onSelectAbility={setActiveAbilityIndex}
          onSelectCommand={onSelectCommand}
          selectedSessionId={selectedSessionId}
        />
      ) : null}
      {activeTab === 'hotbar' ? (
        <HotbarPanel
          abilityOverview={abilityOverview}
          activeHotbarSlot={activeHotbarSlot}
          isApplyingLoadoutAction={panelDisabled}
          onApplyLoadoutAction={onApplyLoadoutAction}
          onSelectCommand={onSelectCommand}
          onSelectSlot={setActiveHotbarSlot}
          selectedSessionId={selectedSessionId}
        />
      ) : null}
      {activeTab === 'skills' ? <SkillsPanel disabled={panelDisabled} onSelectCommand={onSelectCommand} skills={abilityOverview.skills} /> : null}
      {activeTab === 'traits' ? <TraitsPanel disabled={panelDisabled} onSelectCommand={onSelectCommand} traits={abilityOverview.traits} /> : null}
      {activeTab === 'effects' ? <EffectsPanel disabled={panelDisabled} effects={abilityOverview.activeEffects} onSelectCommand={onSelectCommand} /> : null}
      {activeTab === 'coverage' ? <CoveragePanel coverage={abilityOverview.coverage} disabled={panelDisabled} onRefreshCoverage={() => void sessionQuery.refetch()} onSelectCommand={onSelectCommand} /> : null}
    </section>
  );
}

interface InventoryPanelProps extends Pick<RpgLoadoutTabsProps, 'inventoryItems' | 'isApplyingLoadoutAction' | 'onApplyLoadoutAction' | 'onSelectCommand' | 'selectedSessionId'> {
  activeItemIndex: number;
  hotbarAbilities: RpgHotbarAbilityPreview[];
  itemActions: RpgItemUiAction[];
  itemMerchantEntries: RpgMerchantEntryPreview[];
  onApplyItemAction: (action: RpgItemUiAction) => void;
  onApplyItemMerchantEntry: (entry: RpgMerchantEntryPreview) => void;
  onSelectItem: (index: number) => void;
}

function InventoryPanel({
  activeItemIndex,
  inventoryItems,
  hotbarAbilities,
  isApplyingLoadoutAction,
  itemActions,
  itemMerchantEntries,
  onApplyItemAction,
  onApplyItemMerchantEntry,
  onApplyLoadoutAction,
  onSelectCommand,
  onSelectItem,
  selectedSessionId,
}: InventoryPanelProps) {
  return (
    <div aria-labelledby="rpg-inventory-loadout-tab" className="rpg-loadout-layout rpg-item-loadout-layout" id="rpg-inventory-loadout-panel" role="tabpanel">
      <div role="group" className="rpg-inventory-grid" aria-label="Inventory item slots">
        {inventoryItems.map((item, index) => (
          <button
            aria-label={item.label}
            aria-pressed={activeItemIndex === index}
            className="rpg-item-slot"
            key={`${item.label}-${index}`}
            onClick={() => onSelectItem(index)}
            type="button"
          >
            <span aria-hidden="true">{item.icon}</span>
            <small>{item.count}</small>
          </button>
        ))}
        <button
          className="rpg-item-slot rpg-empty-slot"
          disabled={isApplyingLoadoutAction}
          onClick={() => onSelectCommand('Search the area for useful supplies I can pick up and add to my inventory.')}
          title="Search for useful supplies through the next RPG turn."
          type="button"
          aria-label="Search for inventory supplies"
        >
          +
        </button>
        <div role="group" className="rpg-hotbar" aria-label="Ability hotbar">
          {hotbarAbilities.map((ability) => (
            <InventoryHotbarAbilityButton
              ability={ability}
              disabled={Boolean(isApplyingLoadoutAction)}
              key={ability.key}
              onActivate={() =>
                selectedSessionId && onApplyLoadoutAction
                  ? onApplyLoadoutAction({ action: 'hotbar', hotbar_slot: ability.key })
                  : onSelectCommand(`Use ${ability.label} from hotbar slot ${ability.key} when it is tactically useful.`)
              }
              selectedSessionId={selectedSessionId}
            />
          ))}
        </div>
      </div>

      <RpgItemPanel
        actions={itemActions}
        isPending={isApplyingLoadoutAction}
        merchantEntries={itemMerchantEntries}
        onApplyAction={onApplyItemAction}
        onApplyMerchantEntry={onApplyItemMerchantEntry}
        onSelectCommand={onSelectCommand}
      />
    </div>
  );
}

type RpgItemPanelRequest =
  | { kind: 'action'; action: RpgItemUiAction }
  | { kind: 'merchant'; entry: RpgMerchantEntryPreview };

function applyRpgItemPanelRequest(sessionId: string, request: RpgItemPanelRequest): Promise<unknown> {
  if (request.kind === 'action') {
    const loadoutRequest = loadoutRequestFromPayload(request.action.payload);
    if (loadoutRequest) {
      return rpgSessionClient.applyRpgLoadoutAction(sessionId, loadoutRequest);
    }
    if (request.action.mode === 'merchant') {
      return applyRpgItemCommand(sessionId, request.action.command);
    }
    return applyRpgItemResolve(sessionId, { command: request.action.command, input: request.action.payload });
  }

  const itemName = firstString(request.entry.payload.item_name, request.entry.payload.item_id, request.entry.label) ?? request.entry.label;
  return applyRpgItemCommand(sessionId, `${request.entry.action} ${itemName}`);
}

function InventoryHotbarAbilityButton({
  ability,
  disabled,
  onActivate,
  selectedSessionId,
}: {
  ability: RpgHotbarAbilityPreview;
  disabled: boolean;
  onActivate: () => void;
  selectedSessionId?: string | null;
}) {
  const [detailRequested, setDetailRequested] = useState(false);
  const tooltipId = `rpg-hotbar-tooltip-${ability.key}`;
  const detailQuery = useQuery({
    enabled: Boolean(selectedSessionId && detailRequested),
    queryKey: ['feature', 'rpg', 'ability-detail-v1', selectedSessionId, ability.abilityId ?? ability.label],
    queryFn: () =>
      fetchRpgAbilityDetails(selectedSessionId ?? '', {
        abilityName: ability.label,
        abilityId: ability.abilityId,
      }),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const detail = recordValue(detailQuery.data?.ability_detail);
  const description = firstString(detail?.summary, ability.description) ?? 'No ability description is available.';
  const cost = formatAbilityCost(recordValue(detail?.resource_cost));
  const cooldown = firstNumber(detail?.cooldown_turns);
  const isLoading = Boolean(selectedSessionId && detailRequested && detailQuery.isFetching);

  return (
    <button
      aria-describedby={tooltipId}
      aria-label={ability.label}
      disabled={disabled}
      onClick={onActivate}
      onFocus={() => setDetailRequested(true)}
      onMouseEnter={() => setDetailRequested(true)}
      type="button"
    >
      <small>{ability.key}</small>
      <span>{ability.icon}</span>
      <div className="rpg-hotbar-ability-tooltip" id={tooltipId} role="tooltip">
        <strong>{ability.label}</strong>
        <p>{isLoading ? 'Generating an ability description with the configured LLM.' : description}</p>
        {cost || cooldown !== undefined ? (
          <div className="rpg-hotbar-ability-meta">
            {cost ? <span>{cost}</span> : null}
            {cooldown !== undefined ? <span>{cooldown > 0 ? `${cooldown}-turn cooldown` : 'No cooldown'}</span> : null}
          </div>
        ) : null}
      </div>
    </button>
  );
}

function formatAbilityCost(cost: Record<string, unknown> | undefined): string {
  if (!cost) return '';
  return Object.entries(cost)
    .filter(([, value]) => typeof value === 'number')
    .map(([resource, value]) => `${value} ${titleCase(resource)}`)
    .join(' • ');
}

function loadoutRequestFromPayload(payload: Record<string, unknown>): RpgLoadoutActionRequest | null {
  const action = firstString(payload.action);
  if (!action || !LOADOUT_ITEM_ACTIONS.has(action)) {
    return null;
  }
  return payload as unknown as RpgLoadoutActionRequest;
}

interface AbilitiesPanelProps extends Pick<RpgLoadoutTabsProps, 'isApplyingLoadoutAction' | 'onApplyLoadoutAction' | 'onSelectCommand' | 'selectedSessionId'> {
  abilityOverview: AbilityOverview;
  activeAbility: AbilityPreview | undefined;
  activeAbilityIndex: number;
  onSelectAbility: (index: number) => void;
}

function AbilitiesPanel({ abilityOverview, activeAbility, activeAbilityIndex, isApplyingLoadoutAction, onApplyLoadoutAction, onSelectAbility, onSelectCommand, selectedSessionId }: AbilitiesPanelProps) {
  const nextSlot = nextAssignableHotbarSlot(abilityOverview.hotbarSlots, activeAbility?.id);
  const applyAbilityAction = (request: Record<string, unknown>, fallbackCommand: string) => {
    if (selectedSessionId && onApplyLoadoutAction) {
      onApplyLoadoutAction(request as unknown as RpgLoadoutActionRequest);
      return;
    }
    onSelectCommand(fallbackCommand);
  };
  const actions: LoadoutDetailAction[] = activeAbility
    ? [
        { label: 'Inspect', command: `Inspect ${activeAbility.name} and explain when I should use it.`, apply: () => onSelectCommand(`Inspect ${activeAbility.name} and explain when I should use it.`) },
        ...(activeAbility.kind === 'active' && activeAbility.isUnlocked
          ? [
              { label: 'Use', command: `Use ${activeAbility.name} on the most relevant target.`, apply: () => applyAbilityAction({ action: 'use_ability', ability_id: activeAbility.id, ability_name: activeAbility.name, target: 'the most relevant target' }, `Use ${activeAbility.name} on the most relevant target.`) },
              { label: `Assign ${nextSlot}`, command: `Assign ${activeAbility.name} to hotbar slot ${nextSlot}.`, apply: () => applyAbilityAction({ action: 'assign_hotbar', ability_id: activeAbility.id, hotbar_slot: nextSlot }, `Assign ${activeAbility.name} to hotbar slot ${nextSlot}.`) },
            ]
          : []),
        ...(!activeAbility.isUnlocked && activeAbility.isAvailable
          ? [{ label: 'Unlock', command: `Unlock ${activeAbility.name} with an ability point.`, apply: () => applyAbilityAction({ action: 'unlock_ability', ability_id: activeAbility.id }, `Unlock ${activeAbility.name} with an ability point.`) }]
          : []),
        ...(activeAbility.isUnlocked && activeAbility.rank < activeAbility.maxRank && abilityOverview.abilityPoints > 0
          ? [{ label: 'Upgrade', command: `Upgrade ${activeAbility.name} to the next rank.`, apply: () => applyAbilityAction({ action: 'upgrade_ability', ability_id: activeAbility.id }, `Upgrade ${activeAbility.name} to the next rank.`) }]
          : []),
      ]
    : [];

  return (
    <div aria-labelledby="rpg-abilities-loadout-tab" className="rpg-loadout-layout rpg-ability-tree-layout" id="rpg-abilities-loadout-panel" role="tabpanel">
      <div role="group" className="rpg-ability-tree" aria-label="Ability tree categories">
        {abilityOverview.categories.map((category) => (
          <section className="rpg-ability-category" key={category.id}>
            <header>
              <strong>{category.name}</strong>
              <span>{titleCase(category.capability)} • {category.dimensions.map(titleCase).join(', ')}</span>
            </header>
            <div className="rpg-ability-node-list">
              {category.abilities.map((ability) => {
                const index = abilityOverview.allAbilities.findIndex((candidate) => candidate.id === ability.id);
                return (
                  <button
                    aria-pressed={activeAbilityIndex === index}
                    className={`rpg-ability-node rpg-ability-node-${ability.status}`}
                    key={ability.id}
                    onClick={() => onSelectAbility(Math.max(0, index))}
                    type="button"
                  >
                    <span className="rpg-icon-tile" aria-hidden="true">{ability.icon}</span>
                    <div>
                      <strong>{ability.name}</strong>
                      <span>{ability.statusLabel}</span>
                      <small>{titleCase(ability.kind)} • {titleCase(ability.purpose)}</small>
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <LoadoutDetailCard
        eyebrow={activeAbility ? `${titleCase(activeAbility.kind)} ability` : 'Selected ability'}
        title={activeAbility?.name ?? 'No ability selected'}
        detail={activeAbility ? abilityDetail(activeAbility) : 'Select a tree node to inspect, unlock, upgrade, assign, or use it.'}
        actions={actions}
        disabled={isApplyingLoadoutAction}
        onSelectCommand={onSelectCommand}
      />
    </div>
  );
}

interface HotbarPanelProps extends Pick<RpgLoadoutTabsProps, 'isApplyingLoadoutAction' | 'onApplyLoadoutAction' | 'onSelectCommand' | 'selectedSessionId'> {
  abilityOverview: AbilityOverview;
  activeHotbarSlot: string;
  onSelectSlot: (slot: string) => void;
}

function HotbarPanel({ abilityOverview, activeHotbarSlot, isApplyingLoadoutAction, onApplyLoadoutAction, onSelectCommand, onSelectSlot, selectedSessionId }: HotbarPanelProps) {
  const selectedSlot = abilityOverview.hotbarSlots.find((slot) => slot.slot === activeHotbarSlot);
  const selectedAbility = selectedSlot?.ability;
  const applyHotbarAction = (request: Record<string, unknown>, fallbackCommand: string) => {
    if (selectedSessionId && onApplyLoadoutAction) {
      onApplyLoadoutAction(request as unknown as RpgLoadoutActionRequest);
      return;
    }
    onSelectCommand(fallbackCommand);
  };

  return (
    <div aria-labelledby="rpg-hotbar-loadout-tab" className="rpg-loadout-layout" id="rpg-hotbar-loadout-panel" role="tabpanel">
      <div role="group" className="rpg-hotbar-slot-list" aria-label="Active ability hotbar">
        {abilityOverview.hotbarSlots.map((slot) => (
          <article className="rpg-hotbar-slot-row" key={slot.slot}>
            <button
              type="button"
              aria-pressed={activeHotbarSlot === slot.slot}
              disabled={isApplyingLoadoutAction || !slot.ability}
              onClick={() => {
                onSelectSlot(slot.slot);
                if (slot.ability) {
                  applyHotbarAction({ action: 'hotbar', hotbar_slot: slot.slot }, `Use ${slot.ability.name} from hotbar slot ${slot.slot} on the best available target.`);
                }
              }}
            >
              <small>{slot.slot}</small>
              <span>{slot.ability?.icon ?? '+'}</span>
              <strong>{slot.ability?.name ?? 'Empty slot'}</strong>
            </button>
            <button
              className="rpg-mini-button"
              disabled={isApplyingLoadoutAction || !slot.ability}
              onClick={() => applyHotbarAction({ action: 'remove_hotbar', hotbar_slot: slot.slot }, `Remove hotbar slot ${slot.slot}.`)}
              type="button"
            >
              Remove
            </button>
          </article>
        ))}
      </div>
      <LoadoutDetailCard
        eyebrow="Selected hotbar slot"
        title={selectedAbility ? `${selectedSlot?.slot}: ${selectedAbility.name}` : `Slot ${activeHotbarSlot}`}
        detail={selectedAbility ? abilityDetail(selectedAbility) : 'Assign an unlocked active ability from the Abilities tab.'}
        actions={
          selectedAbility
            ? [
                { label: 'Use slot', command: `Use ${selectedAbility.name} from hotbar slot ${selectedSlot?.slot}.`, apply: () => applyHotbarAction({ action: 'hotbar', hotbar_slot: selectedSlot?.slot }, `Use ${selectedAbility.name} from hotbar slot ${selectedSlot?.slot}.`) },
                { label: 'Remove', command: `Remove hotbar slot ${selectedSlot?.slot}.`, apply: () => applyHotbarAction({ action: 'remove_hotbar', hotbar_slot: selectedSlot?.slot }, `Remove hotbar slot ${selectedSlot?.slot}.`) },
              ]
            : []
        }
        disabled={isApplyingLoadoutAction}
        onSelectCommand={onSelectCommand}
      />
    </div>
  );
}

function SkillsPanel({ disabled, onSelectCommand, skills }: { disabled: boolean; onSelectCommand: (command: string) => void; skills: SkillProgressionPreview[] }) {
  const rows = skills.length
    ? skills
    : [{ id: 'swordsmanship', label: 'Swordsmanship', rank: 1, xp: 0, source: 'starter practice' }];
  return (
    <div aria-labelledby="rpg-skills-loadout-tab" className="rpg-list-stack rpg-compact-panel" id="rpg-skills-loadout-panel" role="tabpanel">
      {rows.map((skill) => (
        <article className="rpg-list-row" key={skill.id}>
          <span className="rpg-icon-tile" aria-hidden="true">◆</span>
          <div>
            <strong>{skills.length ? skill.label : 'No skill progress yet'}</strong>
            <span>{skills.length ? `Rank ${skill.rank} • ${skill.xp} XP${skill.source ? ` • ${skill.source}` : ''}` : 'Using abilities grants deterministic skill XP by capability.'}</span>
            <div role="group" className="rpg-loadout-actions" aria-label={`${skill.label} skill actions`}>
              <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Check my ${skill.label} skill rank, XP, and recent training progress.`)} type="button">
                Check skills
              </button>
              <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Practice ${skill.label} with a careful training drill and record any skill progress.`)} type="button">
                Practice {skill.label}
              </button>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function TraitsPanel({ disabled, onSelectCommand, traits }: { disabled: boolean; onSelectCommand: (command: string) => void; traits: AbilityPreview[] }) {
  return (
    <div aria-labelledby="rpg-traits-loadout-tab" className="rpg-list-stack rpg-compact-panel" id="rpg-traits-loadout-panel" role="tabpanel">
      {traits.length ? (
        traits.map((trait) => (
          <article className="rpg-list-row" key={trait.id}>
            <span className="rpg-icon-tile" aria-hidden="true">{trait.icon}</span>
            <div>
              <strong>{trait.name}</strong>
              <span>{trait.statusLabel} • {trait.influenceTags.length ? trait.influenceTags.map(titleCase).join(', ') : 'No influence tags indexed'}</span>
              <div role="group" className="rpg-loadout-actions" aria-label={`${trait.name} trait actions`}>
                <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Inspect the ${trait.name} trait and explain how it affects the current situation.`)} type="button">
                  Inspect trait
                </button>
                <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Lean on the ${trait.name} trait to influence my next action.`)} type="button">
                  Use trait
                </button>
              </div>
            </div>
          </article>
        ))
      ) : (
        <article className="rpg-list-row">
          <span className="rpg-icon-tile">*</span>
          <div>
            <strong>No narrative traits indexed</strong>
            <span>Traits appear here once the selected session has a saved ability tree.</span>
            <div role="group" className="rpg-loadout-actions" aria-label="Trait actions">
              <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand('Inspect my current traits and personality hooks for this scene.')} type="button">
                Inspect traits
              </button>
            </div>
          </div>
        </article>
      )}
    </div>
  );
}

function EffectsPanel({ disabled, effects, onSelectCommand }: { disabled: boolean; effects: EffectPreview[]; onSelectCommand: (command: string) => void }) {
  return (
    <div aria-labelledby="rpg-effects-loadout-tab" className="rpg-list-stack rpg-compact-panel" id="rpg-effects-loadout-panel" role="tabpanel">
      {effects.length ? (
        effects.map((effect) => (
          <article className="rpg-list-row" key={effect.id}>
            <span className="rpg-icon-tile" aria-hidden="true">✦</span>
            <div>
              <strong>{effect.name}</strong>
              <span>{effect.detail}{effect.remaining !== undefined ? ` • ${effect.remaining} turn(s) remaining` : ''}</span>
              <div role="group" className="rpg-loadout-actions" aria-label={`${effect.name} effect actions`}>
                <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Inspect the active effect ${effect.name} and summarize its current modifier.`)} type="button">
                  Inspect effect
                </button>
                <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Act while taking advantage of the active effect ${effect.name}.`)} type="button">
                  Use effect
                </button>
              </div>
            </div>
          </article>
        ))
      ) : (
        <article className="rpg-list-row">
          <span className="rpg-icon-tile">✦</span>
          <div>
            <strong>No active effects</strong>
            <span>Temporary ability effects and runtime modifiers appear here after ability use.</span>
            <div role="group" className="rpg-loadout-actions" aria-label="Effect actions">
              <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand('Inspect current active effects and temporary modifiers.')} type="button">
                Inspect effects
              </button>
            </div>
          </div>
        </article>
      )}
    </div>
  );
}

function CoveragePanel({ coverage, disabled, onRefreshCoverage, onSelectCommand }: { coverage: AbilityCoveragePreview; disabled: boolean; onRefreshCoverage: () => void; onSelectCommand: (command: string) => void }) {
  const percent = Math.round(coverage.score * 100);
  const dimensionRows = REQUIRED_DIMENSIONS.map((dimension) => ({
    dimension,
    count: coverage.dimensionCounts[dimension] ?? 0,
    covered: coverage.coveredDimensions.includes(dimension),
  }));
  const sourceRows = Object.entries(coverage.sourceCounts).sort(([, left], [, right]) => right - left).slice(0, 6);
  const firstMissingDimension = coverage.missingDimensions[0] ?? REQUIRED_DIMENSIONS.find((dimension) => !coverage.coveredDimensions.includes(dimension)) ?? 'resources';

  return (
    <div aria-labelledby="rpg-coverage-loadout-tab" className="rpg-coverage-panel" id="rpg-coverage-loadout-panel" role="tabpanel">
      <section className="rpg-coverage-summary-card" aria-label="Ability coverage score">
        <div className="rpg-coverage-score">
          <strong>{percent}%</strong>
          <span>{coverage.ok ? 'All required dimensions covered' : `${coverage.missingDimensions.length} dimension${coverage.missingDimensions.length === 1 ? '' : 's'} still missing`}</span>
        </div>
        <div role="img" className="rpg-coverage-meter" aria-label={`Ability dimension coverage ${percent}%`}>
          <span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
        </div>
        <small>{coverage.totalObservations} deterministic observation{coverage.totalObservations === 1 ? '' : 's'} indexed from ability traces, world effects, passives, traits, and timeline events.</small>
        <div role="group" className="rpg-loadout-actions" aria-label="Coverage actions">
          <button className="rpg-mini-button" disabled={disabled} onClick={onRefreshCoverage} type="button">
            Refresh coverage
          </button>
          <button className="rpg-mini-button" disabled={disabled} onClick={() => onSelectCommand(`Practice or use an ability that covers the ${titleCase(firstMissingDimension)} dimension.`)} type="button">
            Practice missing dimension
          </button>
        </div>
      </section>

      <div role="group" className="rpg-coverage-grid" aria-label="Ability dimension coverage">
        {dimensionRows.map((row) => (
          <article className={row.covered ? 'rpg-coverage-dimension covered' : 'rpg-coverage-dimension missing'} key={row.dimension}>
            <strong>{titleCase(row.dimension)}</strong>
            <span>{row.covered ? `${row.count} observation${row.count === 1 ? '' : 's'}` : 'Missing'}</span>
          </article>
        ))}
      </div>

      <div className="rpg-loadout-layout">
        <section className="rpg-list-stack rpg-compact-panel" aria-label="Coverage sources">
          {sourceRows.length ? (
            sourceRows.map(([source, count]) => (
              <article className="rpg-list-row" key={source}>
                <span className="rpg-icon-tile" aria-hidden="true">◎</span>
                <div>
                  <strong>{titleCase(source)}</strong>
                  <span>{count} observation{count === 1 ? '' : 's'}</span>
                </div>
              </article>
            ))
          ) : (
            <article className="rpg-list-row"><span className="rpg-icon-tile">◎</span><div><strong>No coverage sources yet</strong><span>Use abilities, passives, traits, or world effects to populate coverage.</span></div></article>
          )}
        </section>
        <section className="rpg-list-stack rpg-compact-panel" aria-label="Coverage warnings">
          {coverage.warnings.length ? (
            coverage.warnings.slice(0, 6).map((warning) => (
              <article className="rpg-list-row" key={warning}>
                <span className="rpg-icon-tile" aria-hidden="true">!</span>
                <div>
                  <strong>Coverage warning</strong>
                  <span>{warning}</span>
                </div>
              </article>
            ))
          ) : (
            <article className="rpg-list-row"><span className="rpg-icon-tile">✓</span><div><strong>No coverage warnings</strong><span>All indexed dimensions are supported by the deterministic ability system.</span></div></article>
          )}
        </section>
      </div>
    </div>
  );
}

interface LoadoutDetailCardProps {
  eyebrow: string;
  title: string;
  detail: string;
  actions: LoadoutDetailAction[];
  disabled?: boolean;
  onSelectCommand: (command: string) => void;
}

function LoadoutDetailCard({ actions, detail, disabled, eyebrow, onSelectCommand, title }: LoadoutDetailCardProps) {
  return (
    <aside className="rpg-loadout-detail" aria-label={`${eyebrow}: ${title}`}>
      <p className="eyebrow">{eyebrow}</p>
      <strong>{title}</strong>
      <span>{detail}</span>
      <div role="group" className="rpg-loadout-actions" aria-label={`${title} actions`}>
        {actions.map((action) => (
          <button className="rpg-mini-button" disabled={disabled} key={action.label} onClick={() => (action.apply ? action.apply() : onSelectCommand(action.command))} type="button">
            {action.label}
          </button>
        ))}
      </div>
    </aside>
  );
}

function buildAbilityOverview(
  payload: RpgLaunchResponse | undefined,
  fallbackHotbar: RpgHotbarAbilityPreview[],
  hasLiveSession = false,
): AbilityOverview {
  const payloadRecord = recordValue(payload);
  const session = recordValue(payloadRecord?.session);
  const game = recordValue(payloadRecord?.game);
  const state = recordValue(session?.state) ?? game ?? session;
  const tree = recordValue(state?.ability_tree);
  const abilityState = recordValue(state?.ability_state);
  const player = recordValue(state?.player);
  const rawAbilities = firstArray(tree?.abilities);
  const coverage = buildCoveragePreview(payloadRecord, state);
  if (!state || !tree || !rawAbilities.length) {
    const previewAbilities = fallbackHotbar.map((ability) => ({
      ...previewAbilityFromHotbar(ability),
      capability: hasLiveSession ? 'session' : 'preview',
      powerSource: hasLiveSession ? 'session' : 'preview',
    }));
    return {
      className: hasLiveSession ? 'Session hotbar' : 'Preview ability kit',
      treeId: hasLiveSession ? 'session-hotbar' : 'preview-hotbar',
      abilityPoints: 0,
      playerLevel: 1,
      categories: [
        {
          id: hasLiveSession ? 'session' : 'preview',
          name: hasLiveSession ? 'Session Hotbar' : 'Preview Hotbar',
          capability: hasLiveSession ? 'session' : 'preview',
          dimensions: ['resources', 'position'],
          abilities: previewAbilities,
        },
      ],
      allAbilities: previewAbilities,
      hotbarSlots: fallbackHotbar.map((ability) => ({ slot: ability.key, ability: previewAbilities.find((candidate) => candidate.name === ability.label) })),
      hotbarAbilities: fallbackHotbar,
      skills: [],
      traits: [],
      activeEffects: [],
      coverage,
      source: hasLiveSession ? 'live' : 'preview',
    };
  }

  const unlocked = new Set(firstArray(abilityState?.unlocked).map(String));
  const ranks = recordValue(abilityState?.ranks) ?? {};
  const cooldowns = recordValue(abilityState?.cooldowns) ?? {};
  const abilityPoints = firstNumber(abilityState?.ability_points) ?? 0;
  const playerLevel = firstNumber(player?.level) ?? 1;
  const categoryLookup = buildCategoryLookup(tree);
  const abilities = rawAbilities.map((rawAbility) => buildAbilityPreview(rawAbility, categoryLookup, unlocked, ranks, cooldowns, abilityPoints, playerLevel));
  const abilityById = new Map(abilities.map((ability) => [ability.id, ability]));
  const categories = firstArray(tree.categories).map((rawCategory, index) => buildCategoryPreview(rawCategory, index, abilities, abilityById));
  const fallbackCategory = categories.length ? categories : [{ id: 'abilities', name: 'Abilities', capability: String(tree.primary_capability ?? 'custom'), dimensions: unique(abilities.flatMap((ability) => ability.dimensions)), abilities }];
  const hotbar = recordValue(state.hotbar) ?? recordValue(abilityState?.hotbar) ?? {};
  const hotbarSlots = Array.from({ length: 10 }, (_, index) => {
    const slot = String(index + 1);
    const abilityId = firstString(hotbar[slot]);
    return { slot, ability: abilityId ? abilityById.get(abilityId) : undefined };
  });
  const hotbarAbilities = hotbarSlots
    .filter((slot): slot is HotbarSlotPreview & { ability: AbilityPreview } => Boolean(slot.ability))
    .slice(0, 6)
    .map((slot) => ({
      key: slot.slot,
      icon: slot.ability.icon,
      label: slot.ability.name,
      abilityId: slot.ability.id,
      description: slot.ability.description,
    }));

  return {
    className: firstString(tree.class_name) ?? 'Ability tree',
    treeId: firstString(tree.tree_id) ?? 'ability-tree',
    abilityPoints,
    playerLevel,
    categories: fallbackCategory,
    allAbilities: abilities,
    hotbarSlots,
    hotbarAbilities,
    skills: buildSkillProgression(state),
    traits: abilities.filter((ability) => ability.kind === 'narrative_trait'),
    activeEffects: buildActiveEffects(state, abilityState),
    coverage,
    source: 'live',
  };
}

function buildCoveragePreview(payload: Record<string, unknown> | undefined, state: Record<string, unknown> | undefined): AbilityCoveragePreview {
  const mechanics = recordValue(state?.mechanics);
  const snapshots = firstArray(mechanics?.ability_coverage_snapshots);
  const source =
    recordValue(payload?.ability_coverage) ??
    recordValue(mechanics?.ability_coverage_latest) ??
    recordValue(snapshots[0]);

  if (!source) {
    return {
      ok: false,
      score: 0,
      totalObservations: 0,
      coveredDimensions: [],
      missingDimensions: REQUIRED_DIMENSIONS,
      dimensionCounts: {},
      sourceCounts: {},
      warnings: [],
    };
  }

  const coveredDimensions = firstArray(source.covered_dimensions).map(String);
  const missingDimensions = firstArray(source.missing_dimensions).map(String);
  return {
    ok: typeof source.ok === 'boolean' ? source.ok : missingDimensions.length === 0,
    score: Math.max(0, Math.min(1, firstNumber(source.coverage_score) ?? 0)),
    totalObservations: firstNumber(source.total_observations) ?? 0,
    coveredDimensions,
    missingDimensions,
    dimensionCounts: numericRecord(source.dimension_counts),
    sourceCounts: numericRecord(source.source_counts),
    warnings: firstArray(source.warnings).map(String),
  };
}

function buildAbilityPreview(rawAbility: unknown, categoryLookup: Map<string, string>, unlocked: Set<string>, ranks: Record<string, unknown>, cooldowns: Record<string, unknown>, abilityPoints: number, playerLevel: number): AbilityPreview {
  const record = recordValue(rawAbility) ?? {};
  const id = firstString(record.ability_id, record.id, record.name) ?? 'ability';
  const kind = firstString(record.kind) ?? 'active';
  const prerequisites = firstArray(record.prerequisites).map(String);
  const missingPrerequisites = prerequisites.filter((prerequisite) => !unlocked.has(prerequisite));
  const levelRequired = firstNumber(record.level_required) ?? 1;
  const isUnlocked = unlocked.has(id);
  const isAvailable = !isUnlocked && playerLevel >= levelRequired && missingPrerequisites.length === 0 && abilityPoints > 0;
  const status: AbilityStatus = isUnlocked ? 'unlocked' : isAvailable ? 'available' : 'locked';
  const rank = firstNumber(ranks[id], record.rank) ?? 1;
  const maxRank = firstNumber(record.max_rank) ?? 1;
  const cooldown = firstNumber(cooldowns[id]) ?? 0;
  return {
    id,
    name: firstString(record.name, record.label, record.ability_id) ?? titleCase(id),
    icon: firstString(record.icon) ?? iconForAbility(firstString(record.purpose), firstArray(record.dimensions).map(String)),
    description: firstString(record.description, record.summary) ?? 'Ability indexed from the selected session.',
    kind,
    capability: firstString(record.capability) ?? 'custom',
    powerSource: firstString(record.power_source) ?? 'mundane',
    purpose: firstString(record.purpose) ?? 'utility',
    dimensions: firstArray(record.dimensions).map(String),
    levelRequired,
    rank,
    maxRank,
    cooldown,
    resourceCost: numericRecord(record.resource_cost),
    prerequisites,
    influenceTags: firstArray(record.influence_tags).map(String),
    hooks: firstArray(record.hooks).map(String),
    categoryName: categoryLookup.get(id) ?? 'Abilities',
    isUnlocked,
    isAvailable,
    missingPrerequisites,
    status,
    statusLabel: abilityStatusLabel(status, levelRequired, playerLevel, missingPrerequisites, rank, maxRank, cooldown, abilityPoints),
  };
}

function buildCategoryLookup(tree: Record<string, unknown>): Map<string, string> {
  const lookup = new Map<string, string>();
  firstArray(tree.categories).forEach((rawCategory) => {
    const category = recordValue(rawCategory) ?? {};
    const name = firstString(category.name, category.category_id) ?? 'Abilities';
    firstArray(category.abilities).forEach((abilityId) => lookup.set(String(abilityId), name));
  });
  return lookup;
}

function buildCategoryPreview(rawCategory: unknown, index: number, abilities: AbilityPreview[], abilityById: Map<string, AbilityPreview>): AbilityCategoryPreview {
  const category = recordValue(rawCategory) ?? {};
  const ids = firstArray(category.abilities).map(String);
  const categoryAbilities = ids.map((id) => abilityById.get(id)).filter((ability): ability is AbilityPreview => Boolean(ability));
  return {
    id: firstString(category.category_id, category.id) ?? `category-${index + 1}`,
    name: firstString(category.name, category.label) ?? `Category ${index + 1}`,
    capability: firstString(category.capability) ?? categoryAbilities[0]?.capability ?? 'custom',
    dimensions: firstArray(category.dimensions).map(String).length ? firstArray(category.dimensions).map(String) : unique(categoryAbilities.flatMap((ability) => ability.dimensions)),
    abilities: categoryAbilities.length ? categoryAbilities : abilities,
  };
}

function buildSkillProgression(state: Record<string, unknown>): SkillProgressionPreview[] {
  const skills = recordValue(state.skill_progression) ?? {};
  return Object.entries(skills).map(([id, rawSkill]) => {
    const skill = recordValue(rawSkill) ?? {};
    return { id, label: titleCase(id), rank: firstNumber(skill.rank) ?? 1, xp: firstNumber(skill.xp) ?? 0, source: firstString(skill.last_source) ?? '' };
  });
}

function buildActiveEffects(state: Record<string, unknown>, abilityState: Record<string, unknown> | undefined): EffectPreview[] {
  const abilityEffects = firstArray(abilityState?.active_effects);
  const runtimeEffects = firstArray(recordValue(state.runtime)?.effects);
  return [...abilityEffects, ...runtimeEffects].slice(0, 8).map((rawEffect, index) => {
    const effect = recordValue(rawEffect) ?? {};
    const dimensions = firstArray(effect.dimensions).map(String).length ? firstArray(effect.dimensions).map(String) : [firstString(effect.dimension) ?? 'effect'];
    const name = firstString(effect.name, effect.source, effect.ability_name, effect.ability_id) ?? `Effect ${index + 1}`;
    const detail = firstString(effect.check, effect.purpose, effect.target) ?? dimensions.map(titleCase).join(', ');
    return { id: `${name}-${index}`, name, detail, dimensions, remaining: firstNumber(effect.remaining_turns) };
  });
}

function previewAbilityFromHotbar(ability: RpgHotbarAbilityPreview): AbilityPreview {
  return {
    id: ability.label,
    name: ability.label,
    icon: ability.icon,
    description: 'Preview hotbar ability. Select or create a session to inspect the saved ability tree.',
    kind: 'active',
    capability: 'preview',
    powerSource: 'preview',
    purpose: 'utility',
    dimensions: ['resources', 'position'],
    levelRequired: 1,
    rank: 1,
    maxRank: 1,
    cooldown: 0,
    resourceCost: {},
    prerequisites: [],
    influenceTags: [],
    hooks: [],
    categoryName: 'Preview Hotbar',
    isUnlocked: true,
    isAvailable: false,
    missingPrerequisites: [],
    status: 'unlocked',
    statusLabel: 'Preview',
  };
}

function abilityStatusLabel(status: AbilityStatus, levelRequired: number, playerLevel: number, missingPrerequisites: string[], rank: number, maxRank: number, cooldown: number, abilityPoints: number): string {
  if (status === 'unlocked') {
    return cooldown > 0 ? `Cooldown ${cooldown} turn(s) • rank ${rank}/${maxRank}` : `Unlocked • rank ${rank}/${maxRank}`;
  }
  if (playerLevel < levelRequired) {
    return `Locked • level ${levelRequired}`;
  }
  if (missingPrerequisites.length) {
    return `Locked • requires ${missingPrerequisites.map(titleCase).join(', ')}`;
  }
  return abilityPoints > 0 ? 'Available • costs 1 point' : 'Locked • needs ability point';
}

function abilityDetail(ability: AbilityPreview): string {
  const cost = Object.entries(ability.resourceCost)
    .filter(([, value]) => value > 0)
    .map(([resource, value]) => `${value} ${resource}`)
    .join(', ');
  const pieces = [
    ability.description,
    `Dimensions: ${ability.dimensions.map(titleCase).join(', ') || 'None indexed'}.`,
    `Purpose: ${titleCase(ability.purpose)}.`,
    cost ? `Cost: ${cost}.` : 'No resource cost.',
    ability.hooks.length ? `Hooks: ${ability.hooks.map(titleCase).join(', ')}.` : undefined,
    ability.influenceTags.length ? `Tags: ${ability.influenceTags.map(titleCase).join(', ')}.` : undefined,
  ];
  return pieces.filter(Boolean).join(' ');
}

function nextAssignableHotbarSlot(slots: HotbarSlotPreview[], abilityId?: string): string {
  const existing = slots.find((slot) => slot.ability?.id === abilityId);
  if (existing) {
    return existing.slot;
  }
  return slots.find((slot) => !slot.ability)?.slot ?? '1';
}

function numericRecord(value: unknown): Record<string, number> {
  const record = recordValue(value) ?? {};
  return Object.fromEntries(Object.entries(record).map(([key, amount]) => [key, firstNumber(amount) ?? 0]));
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return undefined;
}

function firstRecord(...values: unknown[]): Record<string, unknown> | undefined {
  for (const value of values) {
    const record = recordValue(value);
    if (record) {
      return record;
    }
  }
  return undefined;
}

function firstArray(...values: unknown[]): unknown[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value.replace(/,/g, ''));
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function iconForAbility(purpose: string | undefined, dimensions: string[]): string {
  const text = `${purpose ?? ''} ${dimensions.join(' ')}`.toLowerCase();
  if (text.includes('healing')) return '✚';
  if (text.includes('information')) return '⌕';
  if (text.includes('access')) return '⌁';
  if (text.includes('relationship')) return '☯';
  if (text.includes('environment')) return '✹';
  if (text.includes('damage')) return '⚔';
  if (text.includes('mobility')) return '⇥';
  return '✦';
}

function titleCase(value: string | undefined): string {
  return String(value ?? '')
    .replace(/[_-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
