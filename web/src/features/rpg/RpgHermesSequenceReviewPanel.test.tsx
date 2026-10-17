import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RpgHermesSequenceReviewPanel } from './RpgHermesSequenceReviewPanel';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('RpgHermesSequenceReviewPanel', () => {
  it('does not render the removed Hermes sequence review section', () => {
    renderWithProviders(
      <RpgHermesSequenceReviewPanel onReview={vi.fn()} onUseFirstItem={vi.fn()} />,
    );

    expect(screen.queryByText('Hermes sequence review')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review sequence' })).not.toBeInTheDocument();
  });
});
