import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RpgCombatSurface } from './RpgCombatSurface';
import { createRpgCombatSurfaceState } from './rpgCombatState';
import { partyMembers, previewEncounter, previewHeroSummary } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('RpgCombatSurface', () => {
  it('renders inactive combat affordances without enabling combat actions', () => {
    const combat = createRpgCombatSurfaceState({ encounter: previewEncounter, heroSummary: previewHeroSummary, partyMembers });

    renderWithProviders(<RpgCombatSurface combat={combat} onSelectCommand={vi.fn()} />);

    expect(screen.getByRole('region', { name: 'Combat surface' })).toBeInTheDocument();
    expect(screen.getByText('Tactical combat')).toBeInTheDocument();
    expect(screen.getByText('Exploration mode')).toBeInTheDocument();
    expect(screen.getByText('No initiative queue yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Attack/i })).toBeDisabled();
  });

  it('renders live initiative, enemy cards, deltas, and command insertion', () => {
    const onSelectCommand = vi.fn();
    const combat = createRpgCombatSurfaceState({
      encounter: {
        icon: '⚔',
        title: 'Bandit ambush',
        detail: 'Combatants: Road bandit, Lookout',
        source: 'live',
      },
      heroSummary: { ...previewHeroSummary, name: 'Mira Vale' },
      partyMembers,
    });

    renderWithProviders(<RpgCombatSurface combat={combat} onSelectCommand={onSelectCommand} />);

    expect(screen.getByText('Combat turn gate active')).toBeInTheDocument();
    expect(screen.getByText('Mira Vale')).toBeInTheDocument();
    expect(screen.getAllByText('Road bandit')).toHaveLength(2);
    expect(screen.getByLabelText('Road bandit health')).toBeInTheDocument();
    expect(screen.getByText('Combatants: Road bandit, Lookout')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Attack/i }));

    expect(onSelectCommand).toHaveBeenCalledWith('Attack the most immediate threat in Bandit ambush.');
  });
});
