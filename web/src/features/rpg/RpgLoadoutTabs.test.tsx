import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { rpgItemGatewayClient } from './rpgItemApi';
import { rpgSessionClient } from './api/rpgSessionClient';
import { RpgLoadoutTabs } from './RpgLoadoutTabs';
import { hotbarAbilities, inventoryItems } from './rpgUiState';
import { createTestQueryClient } from '../../test/renderWithProviders';

type LoadoutTabsProps = Parameters<typeof RpgLoadoutTabs>[0];

interface MockLiveItemApiOptions {
  merchantEntries?: boolean;
}

function renderLoadoutTabs(props: LoadoutTabsProps) {
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <RpgLoadoutTabs {...props} />
    </QueryClientProvider>,
  );
}

function mockLiveItemApis(sessionPayload: Record<string, unknown> = { ok: true }, options: MockLiveItemApiOptions = {}) {
  const getRpgSession = vi.spyOn(rpgSessionClient, 'getRpgSession').mockResolvedValue(sessionPayload as never);
  const applyRpgLoadoutAction = vi.spyOn(rpgSessionClient, 'applyRpgLoadoutAction').mockResolvedValue({ ok: true } as never);
  const includeMerchantEntries = options.merchantEntries ?? true;
  const post = vi.spyOn(rpgItemGatewayClient, 'post').mockImplementation(async (_path: `/api/${string}`, body: unknown) => {
    const payload = body as Record<string, unknown>;
    if (payload.action === 'item_detail') {
      return {
        ok: true,
        item_detail: {
          item_name: payload.item_name,
          summary: `LLM detail: ${payload.item_name} restores stamina without advancing the turn.`,
          status: 'Carried',
          condition: 'Good',
          source: 'llm',
          tags: ['consumable', 'recovery'],
        },
      } as never;
    }
    if (payload.action === 'ability_detail') {
      return {
        ok: true,
        ability_detail: {
          ability_id: payload.ability_id,
          name: payload.ability_name,
          summary: `LLM ability detail: ${payload.ability_name} is grounded in the current campaign.`,
          resource_cost: { stamina: 10 },
          cooldown_turns: 1,
          source: 'llm',
        },
      } as never;
    }
    if (payload.action === 'item_objectives') {
      return {
        ok: true,
        objectives: {
          objectives: [
            {
              objective_id: 'craft:torch',
              label: 'Craft torch',
              reason: 'Enough materials are available.',
              action: { action: 'craft', recipe_id: 'torch', station: 'campfire' },
            },
          ],
        },
      } as never;
    }
    if (payload.action === 'item_diagnostics') {
      return {
        ok: true,
        diagnostics: {
          summary: { issue_count: 0, warning_count: 1 },
          report: { summary: { coverage_score: 0.5, detail: '2 of 4 item pillars covered.' } },
          ...(includeMerchantEntries
            ? {
                merchant_menu: {
                  menu: [{ id: 'buy:torch', action: 'buy', item_name: 'Torch', description: 'A useful light source.', price: { copper: 4 } }],
                },
              }
            : {}),
        },
      } as never;
    }
    if (payload.action === 'item_maintenance') {
      return { ok: true, maintenance: { summary: { dropped_count: 0 } } } as never;
    }
    return { ok: true } as never;
  });
  return { applyRpgLoadoutAction, getRpgSession, post };
}

function liveAbilitySession() {
  return {
    ok: true,
    session: {
      state: {
        player: { level: 2 },
        skill_progression: {
          swordsmanship: { rank: 2, xp: 35, last_source: 'training' },
        },
        ability_tree: {
          tree_id: 'warden-tree',
          class_name: 'Warden',
          categories: [
            { category_id: 'traits', name: 'Traits', capability: 'resolve', dimensions: ['relationships'], abilities: ['steady_hand'] },
          ],
          abilities: [
            {
              ability_id: 'steady_hand',
              name: 'Steady Hand',
              kind: 'narrative_trait',
              purpose: 'support',
              dimensions: ['relationships'],
              influence_tags: ['calm'],
              icon: '◇',
              description: 'Keeps the party steady under pressure.',
            },
          ],
        },
        ability_state: {
          unlocked: ['steady_hand'],
          ranks: { steady_hand: 1 },
          active_effects: [{ name: 'Calm Focus', purpose: 'steady aim', dimensions: ['resources'], remaining_turns: 2 }],
        },
        mechanics: {
          ability_coverage_latest: {
            ok: false,
            coverage_score: 0.25,
            total_observations: 3,
            covered_dimensions: ['relationships'],
            missing_dimensions: ['resources'],
            dimension_counts: { relationships: 3 },
            source_counts: { trait: 3 },
            warnings: ['resources missing'],
          },
        },
      },
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('RpgLoadoutTabs', () => {
  it('renders inventory by default and switches to abilities and hotbar panels', () => {
    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand: vi.fn() });

    expect(screen.getByRole('tab', { name: 'Inventory' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Inventory' })).toHaveTextContent('12');
    expect(screen.getByRole('button', { name: 'Healing potion' })).toBeInTheDocument();
    expect(screen.getByLabelText('Selected item details: Healing potion')).toHaveTextContent('Preview item details');
    expect(screen.getByRole('region', { name: 'Item actions and coverage' })).toHaveTextContent('Item details and actions');
    expect(screen.getByRole('button', { name: 'Use' })).toBeDisabled();

    fireEvent.click(screen.getByRole('tab', { name: 'Abilities' }));

    expect(screen.getByRole('tab', { name: 'Abilities' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Abilities' })).toHaveTextContent('Aimed Shot');
    expect(screen.getByRole('complementary', { name: 'Active ability: Aimed Shot' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Hotbar' }));

    expect(screen.getByRole('tab', { name: 'Hotbar' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Hotbar' })).toHaveTextContent('Frost Arrow');
    expect(screen.getByRole('button', { name: /Aimed Shot/ })).toBeInTheDocument();
  });

  it('turns ability interactions into replay-preserving commands without a live session', () => {
    const onSelectCommand = vi.fn();
    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand });

    fireEvent.click(screen.getByRole('tab', { name: 'Abilities' }));
    const activeAbility = screen.getByRole('complementary', { name: 'Active ability: Aimed Shot' });
    fireEvent.click(within(activeAbility).getByRole('button', { name: 'Use' }));

    expect(onSelectCommand).toHaveBeenLastCalledWith('Use Aimed Shot on the most relevant target.');

    fireEvent.click(screen.getByRole('tab', { name: 'Hotbar' }));
    fireEvent.click(screen.getByRole('button', { name: /Frost Arrow/ }));

    expect(onSelectCommand).toHaveBeenLastCalledWith('Use Frost Arrow from hotbar slot 2 on the best available target.');
  });

  it('wires the empty inventory slot to a pickup/search command', () => {
    const onSelectCommand = vi.fn();
    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand });

    fireEvent.click(screen.getByRole('button', { name: 'Search for inventory supplies' }));

    expect(onSelectCommand).toHaveBeenLastCalledWith('Search the area for useful supplies I can pick up and add to my inventory.');
  });

  it('shows item details and merchant entries without diagnostics or item objectives', async () => {
    const { post } = mockLiveItemApis();

    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand: vi.fn(), selectedSessionId: 'session-live' });

    await screen.findByText('LLM detail: Healing potion restores stamina without advancing the turn.');
    const itemDetails = screen.getByLabelText('Selected item details: Healing potion');
    expect(itemDetails).toHaveTextContent('LLM item details');
    expect(itemDetails).toHaveTextContent('StatusCarried');
    expect(itemDetails).toHaveTextContent('ConditionGood');
    expect(itemDetails).not.toHaveTextContent('Trade value depends');
    expect(screen.getByRole('button', { name: 'Use' })).toHaveAttribute('title', expect.stringContaining('Apply item effects'));
    expect(screen.queryByText('Suggested next item steps')).not.toBeInTheDocument();
    expect(screen.queryByText('Item diagnostics')).not.toBeInTheDocument();
    expect(screen.queryByText('Item maintenance')).not.toBeInTheDocument();
    expect(screen.queryByText('Item coverage')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'buy Torch' })).toBeInTheDocument();
    expect(post.mock.calls.some(([, body]) => (body as Record<string, unknown>).action === 'item_objectives')).toBe(false);

    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Aimed Shot' }));
    const abilityDescription = await screen.findByText('LLM ability detail: Aimed Shot is grounded in the current campaign.');
    const abilityTooltip = abilityDescription.closest('[role="tooltip"]');
    expect(abilityTooltip).toHaveTextContent('10 Stamina');
    expect(abilityTooltip).toHaveTextContent('1-turn cooldown');
  });

  it('routes panel item actions through loadout and item command APIs', async () => {
    const { applyRpgLoadoutAction, post } = mockLiveItemApis();

    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand: vi.fn(), selectedSessionId: 'session-live' });
    await screen.findByText('LLM detail: Healing potion restores stamina without advancing the turn.');

    fireEvent.click(screen.getByRole('button', { name: 'Use' }));
    await waitFor(() => {
      expect(applyRpgLoadoutAction).toHaveBeenCalledWith('session-live', { action: 'use', item_name: 'Healing potion' });
    });

    post.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Sell' }));
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith('/api/rpg/session/get', {
        action: 'item_command',
        session_id: 'session-live',
        command: 'sell Healing potion',
      });
    });
  });

  it('disables merchant-only item actions until merchant context exists', async () => {
    mockLiveItemApis({ ok: true }, { merchantEntries: false });

    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand: vi.fn(), selectedSessionId: 'session-live' });
    await screen.findByText('LLM detail: Healing potion restores stamina without advancing the turn.');

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Use' })).not.toBeDisabled();
    });
    expect(screen.getByRole('button', { name: 'Sell' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sell' })).toHaveAttribute(
      'title',
      'Sell: Start a merchant conversation or open a merchant service before selling.',
    );
  });

  it('wires skills, traits, effects, and coverage panels to commands or live refresh', async () => {
    const onSelectCommand = vi.fn();
    const { getRpgSession } = mockLiveItemApis(liveAbilitySession());

    renderLoadoutTabs({ hotbarAbilities, inventoryItems, onSelectCommand, selectedSessionId: 'session-live' });

    fireEvent.click(screen.getByRole('tab', { name: 'Skills' }));
    expect(await screen.findByText('Swordsmanship')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Practice Swordsmanship' }));
    expect(onSelectCommand).toHaveBeenLastCalledWith('Practice Swordsmanship with a careful training drill and record any skill progress.');

    fireEvent.click(screen.getByRole('tab', { name: 'Traits' }));
    expect(await screen.findByText('Steady Hand')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use trait' }));
    expect(onSelectCommand).toHaveBeenLastCalledWith('Lean on the Steady Hand trait to influence my next action.');

    fireEvent.click(screen.getByRole('tab', { name: 'Effects' }));
    expect(await screen.findByText('Calm Focus')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use effect' }));
    expect(onSelectCommand).toHaveBeenLastCalledWith('Act while taking advantage of the active effect Calm Focus.');

    fireEvent.click(screen.getByRole('tab', { name: 'Coverage' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Practice missing dimension' }));
    expect(onSelectCommand).toHaveBeenLastCalledWith('Practice or use an ability that covers the Resources dimension.');

    const previousRefreshCalls = getRpgSession.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh coverage' }));
    await waitFor(() => {
      expect(getRpgSession.mock.calls.length).toBeGreaterThan(previousRefreshCalls);
    });
  });
});
