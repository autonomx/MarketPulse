import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HermesSequencePreview } from './HermesSequencePreview';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('HermesSequencePreview', () => {
  it('renders an empty preview state', () => {
    renderWithProviders(<HermesSequencePreview sequence={null} />);

    expect(screen.getByText('No Hermes sequence is ready for review.')).toBeInTheDocument();
  });

  it('renders a safe reviewed sequence', () => {
    const onUseFirstItem = vi.fn();
    renderWithProviders(
      <HermesSequencePreview
        onUseFirstItem={onUseFirstItem}
        sequence={{
          sequence_id: 'seq-1',
          objective: 'Review the current location',
          domain: 'rpg',
          state_owner: 'rpg_sim',
          risk: 'low',
          status: 'ready',
          review_status: 'ready',
          validation_status: 'valid',
          gate_status: 'ready',
          first_usable_command: 'look around',
          items: [
            { item_id: 'look', statement: 'look around', expected_effect: 'Current room details refresh.', gate_allowed: true, user_gate: false },
          ],
        }}
      />,
    );

    expect(screen.getByRole('region', { name: 'Hermes sequence preview' })).toHaveTextContent('Review the current location');
    expect(screen.getByText('valid')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Hermes sequence preview' })).toHaveTextContent('ready');
    expect(screen.getAllByText('look around')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Use first item' })).not.toBeDisabled();
  });

  it('renders a blocked sequence reason', () => {
    renderWithProviders(
      <HermesSequencePreview
        sequence={{
          objective: 'Spend coins at the market',
          review_status: 'blocked',
          validation_status: 'valid',
          gate_status: '1 blocked',
          blocked_reason: 'stateful_statement',
          items: [
            { item_id: 'buy', statement: 'buy rope', expected_effect: 'Currency changes.', gate_allowed: false, gate_reason: 'stateful_statement' },
          ],
        }}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('stateful_statement');
    expect(screen.getAllByText('stateful_statement')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Use first item' })).toBeDisabled();
  });

  it('renders an empty reviewed sequence', () => {
    renderWithProviders(
      <HermesSequencePreview
        sequence={{
          objective: 'Review nothing',
          review_status: 'empty',
          validation_status: '1 issue',
          validation_errors: ['missing_items'],
          gate_status: 'not checked',
          items: [],
        }}
      />,
    );

    expect(screen.getByText('This reviewed sequence has no items.')).toBeInTheDocument();
    expect(screen.getByText('missing_items')).toBeInTheDocument();
  });

  it('renders invalid validation issues', () => {
    renderWithProviders(
      <HermesSequencePreview
        sequence={{
          review_status: 'invalid',
          validation_status: '2 issues',
          validation_errors: ['missing_objective', 'invalid_state_owner'],
          gate_status: 'not checked',
          blocked_reason: 'missing_objective',
          items: [{ item_id: 'step', statement: 'look around' }],
        }}
      />,
    );

    expect(screen.getByText('Untitled objective')).toBeInTheDocument();
    expect(screen.getByLabelText('Hermes sequence validation issues')).toHaveTextContent('invalid_state_owner');
  });
});
