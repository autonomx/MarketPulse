import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RpgStoryScene } from './RpgStoryScene';
import { previewHeroSummary, previewRecentEvents, previewSessionSummary } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('RpgStoryScene', () => {
  it('renders scene context, dialogue, recent events, and child controls', () => {
    renderWithProviders(
      <RpgStoryScene
        heroSummary={previewHeroSummary}
        recentEvents={previewRecentEvents}
        selectedSessionSummary={previewSessionSummary}
      >
        <button type="button">Queue RPG turn</button>
      </RpgStoryScene>
    );

    expect(screen.getByRole('region', { name: /Glimmerdeep Pass/ })).toBeInTheDocument();
    expect(screen.getByText('Story / scene')).toBeInTheDocument();
    expect(screen.getByText('Preview campaign')).toBeInTheDocument();
    expect(screen.getByText('Turn 12')).toBeInTheDocument();
    expect(screen.getByLabelText('Glimmerdeep Pass scene preview')).toBeInTheDocument();
    expect(screen.getByText('Alyndra (You)')).toBeInTheDocument();
    expect(screen.getByText('Omnix (Narrator)')).toBeInTheDocument();
    expect(screen.getByLabelText('Conversation')).toHaveClass('rpg-dialogue-stack');
    expect(screen.getByText('You arrived at Glimmerdeep Pass.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Queue RPG turn' })).toBeInTheDocument();
    const childControls = screen.getByRole('button', { name: 'Queue RPG turn' });
    const recentEventsToggle = screen.getByRole('button', { name: 'Recent events' });
    expect(childControls.compareDocumentPosition(recentEventsToggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    expect(recentEventsToggle).toHaveAttribute('aria-expanded', 'false');
    expect(recentEventsToggle.closest('.rpg-event-strip')).toHaveClass('is-collapsed');

    fireEvent.click(recentEventsToggle);
    expect(recentEventsToggle).toHaveAttribute('aria-expanded', 'true');
    expect(recentEventsToggle.closest('.rpg-event-strip')).toHaveClass('is-expanded');
  });

  it('renders the bounded durable transcript without dropping messages after the oldest ten', () => {
    const storyMessages = Array.from({ length: 12 }, (_, index) => ({
      id: `message:${index + 1}`,
      interactionId: `interaction:${index + 1}`,
      avatar: 'A',
      speaker: 'Alyndra (You)',
      text: `Durable message ${index + 1}`,
      tone: 'player' as const,
    }));

    renderWithProviders(
      <RpgStoryScene
        heroSummary={previewHeroSummary}
        recentEvents={[]}
        selectedSessionSummary={{ ...previewSessionSummary, id: 'live-session', source: 'live' }}
        storyMessages={storyMessages}
      >
        <button type="button">Queue RPG turn</button>
      </RpgStoryScene>
    );

    expect(screen.getByText('Durable message 1')).toBeInTheDocument();
    expect(screen.getByText('Durable message 12')).toBeInTheDocument();
    expect(screen.getByLabelText('Conversation').querySelectorAll('article')).toHaveLength(12);
  });
});
