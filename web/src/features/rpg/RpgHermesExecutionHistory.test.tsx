import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RpgHermesExecutionHistory } from './RpgHermesExecutionHistory';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('RpgHermesExecutionHistory', () => {
  it('does not render the removed Hermes history section', () => {
    renderWithProviders(<RpgHermesExecutionHistory items={[]} />);

    expect(screen.queryByText('Hermes history')).not.toBeInTheDocument();
    expect(screen.queryByText('No Hermes execution history for this session yet.')).not.toBeInTheDocument();
  });
});
