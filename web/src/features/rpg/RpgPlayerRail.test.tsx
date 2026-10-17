import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RpgPlayerRail } from './RpgPlayerRail';
import { activeQuests, equippedGear, heroStats as previewHeroStats, partyMembers, previewHeroSummary, previewSurvival } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';


const baseRailProps = {
  activeQuests,
  equippedGear,
  heroStats: previewHeroStats,
  heroSummary: previewHeroSummary,
  partyMembers,
  survival: previewSurvival,
};

describe('RpgPlayerRail', () => {
  it('keeps player-facing RPG information and removes Hermes diagnostics', () => {
    const onSelectCommand = vi.fn();
    renderWithProviders(
      <RpgPlayerRail
        {...baseRailProps}
        hermesRouteDecision={{
          mode: 'rpg',
          hermesRole: 'suggest',
          owner: 'rpg_sim',
          reviewRequired: false,
          boundary: 'Simulation owns truth.',
        }}
        hermesSuggestionState="ready"
        hermesSuggestions={[{ id: 'look', label: 'Look around', command: 'look around' }]}
        hermesTurnReadout={{ category: 'dialogue', effectCount: 1, groundingStatus: 'valid', systems: ['dialogue'] }}
        onSelectCommand={onSelectCommand}
      />
    );

    expect(screen.getByRole('complementary', { name: 'Player, party, and quests' })).toBeInTheDocument();
    expect(screen.getByText('Alyndra')).toBeInTheDocument();
    expect(screen.getByLabelText('HP 86 / 110')).toBeInTheDocument();
    expect(screen.getByLabelText('XP 7,450 / 12,000')).toBeInTheDocument();
    expect(screen.getByText('Longbow of the Boreal Wind')).toBeInTheDocument();
    expect(screen.getByText('Thorin Ironfist')).toBeInTheDocument();
    expect(screen.getByText('The Frostbound Relic')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Survival status' })).toHaveTextContent('Hunger24 / 100');

    expect(screen.queryByRole('region', { name: 'Hermes route decision' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Hermes suggested actions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Hermes turn readout' })).not.toBeInTheDocument();
    expect(screen.queryByText('Look around')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Rest' }));
    expect(onSelectCommand).toHaveBeenCalledWith('I rest');
  });
});
