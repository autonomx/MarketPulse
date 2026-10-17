import { fireEvent, screen } from '@testing-library/react';
import type { FormEvent } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgActionComposer } from './RpgActionComposer';
import { previewSessionSummary, quickActions } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';

function registration<TName extends 'sessionId' | 'command'>(name: TName): UseFormRegisterReturn<TName> {
  return {
    name,
    onBlur: vi.fn(),
    onChange: vi.fn(),
    ref: vi.fn(),
  };
}


describe('RpgActionComposer', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders replay-preserving turn controls and quick actions', () => {
    const onQuickAction = vi.fn();
    const onSubmit = vi.fn((event: FormEvent<HTMLFormElement>) => event.preventDefault());

    renderWithProviders(
      <RpgActionComposer
        canSaveGame={false}
        commandRegistration={registration('command')}
        hasCommandError={false}
        isPending={false}
        onQuickAction={onQuickAction}
        onSaveGame={async () => 'checkpoint:test'}
        onSubmit={onSubmit}
        quickActions={quickActions}
        renderNewCampaign={() => null}
        selectedSessionId=""
        sessionRegistration={registration('sessionId')}
        sessionSummaries={[previewSessionSummary]}
      />
    );

    expect(screen.getByRole('heading', { name: 'Turn request' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Queue RPG turn' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Session' })).toHaveTextContent('Preview campaign — preview-session');
    expect(screen.getByRole('textbox', { name: 'Command' })).toHaveAttribute('aria-invalid', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'Investigate' }));

    expect(onQuickAction).toHaveBeenCalledWith('Investigate the clawed tracks and torn Northern Watch banner.');
  });

  it('renders pending and invalid states for queued turn submission', () => {
    renderWithProviders(
      <RpgActionComposer
        canSaveGame={false}
        commandRegistration={registration('command')}
        hasCommandError
        isPending
        onQuickAction={vi.fn()}
        onSaveGame={async () => 'checkpoint:test'}
        onSubmit={vi.fn()}
        quickActions={quickActions}
        renderNewCampaign={() => null}
        selectedSessionId=""
        sessionRegistration={registration('sessionId')}
        sessionSummaries={[]}
      />
    );

    expect(screen.getByRole('button', { name: /Queueing/ })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Command' })).toHaveAttribute('aria-invalid', 'true');
  });

  it('routes New Campaign from the launcher into the full campaign wizard', () => {
    renderWithProviders(
      <RpgActionComposer
        canSaveGame={false}
        commandRegistration={registration('command')}
        hasCommandError={false}
        isPending={false}
        onQuickAction={vi.fn()}
        onSaveGame={async () => 'checkpoint:test'}
        onSubmit={vi.fn()}
        quickActions={quickActions}
        renderNewCampaign={() => <div>Full campaign options</div>}
        selectedSessionId=""
        sessionRegistration={registration('sessionId')}
        sessionSummaries={[]}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Campaign Menu' }));
    expect(screen.queryByRole('button', { name: /^New Game/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^New Campaign/ }));

    expect(screen.getByRole('dialog', { name: 'New Campaign' })).toBeInTheDocument();
    expect(screen.getByText('Full campaign options')).toBeInTheDocument();
  });

  it('saves the selected campaign from the Campaign Menu', async () => {
    const onSaveGame = vi.fn().mockResolvedValue('checkpoint:manual-save');
    renderWithProviders(
      <RpgActionComposer
        canSaveGame
        commandRegistration={registration('command')}
        hasCommandError={false}
        isPending={false}
        onQuickAction={vi.fn()}
        onSaveGame={onSaveGame}
        onSubmit={vi.fn()}
        quickActions={quickActions}
        renderNewCampaign={() => null}
        selectedSessionId="session-live"
        sessionRegistration={registration('sessionId')}
        sessionSummaries={[]}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Campaign Menu' }));
    fireEvent.click(screen.getByRole('button', { name: /^Save Game/ }));

    await vi.waitFor(() => expect(onSaveGame).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Game saved: checkpoint:manual-save')).toBeInTheDocument();
  });
});
