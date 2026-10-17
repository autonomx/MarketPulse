import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { RpgItemPanel } from './RpgItemPanel';
import type { RpgItemUiAction, RpgMerchantEntryPreview } from './rpgItemUiState';
import { createTestQueryClient } from '../../test/renderWithProviders';

const action: RpgItemUiAction = {
  id: 'use:field-kit',
  kind: 'use',
  label: 'Use',
  detail: 'Apply item effects.',
  mode: 'loadout',
  command: 'Use Field Kit',
  payload: { action: 'use', item_name: 'Field Kit' },
  item: { label: 'Field Kit', icon: '🧰', count: 1, sessionId: null },
};

const merchantEntry: RpgMerchantEntryPreview = {
  id: 'buy:torch',
  label: 'Torch',
  detail: 'Useful light source.',
  priceLabel: '1s',
  action: 'buy',
  payload: { action: 'buy', item_name: 'Torch' },
};

function renderItemPanel(element: ReactElement) {
  const queryClient = createTestQueryClient();
  return render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>);
}

describe('RpgItemPanel', () => {
  it('renders an empty state when no selected item actions are available', () => {
    renderItemPanel(<RpgItemPanel actions={[]} />);

    expect(screen.getByText('Item details and actions')).toBeInTheDocument();
    expect(screen.getByText('No item selected')).toBeInTheDocument();
    expect(screen.getByText('Select an inventory item to reveal deterministic actions.')).toBeInTheDocument();
  });

  it('renders preview details for the selected item', () => {
    renderItemPanel(<RpgItemPanel actions={[action]} />);

    const detailCard = screen.getByLabelText('Selected item details: Field Kit');
    expect(detailCard).toHaveTextContent('Preview item details');
    expect(detailCard).toHaveTextContent('Field Kit');
    expect(detailCard).toHaveTextContent('1 carried');
    expect(detailCard).toHaveTextContent('Status');
    expect(detailCard).toHaveTextContent('Preview');
    expect(detailCard).toHaveTextContent('Condition');
    expect(detailCard).toHaveTextContent('Not recorded');
    expect(detailCard).not.toHaveTextContent('Use');
    expect(detailCard).not.toHaveTextContent('Trade');
    expect(detailCard).not.toHaveTextContent('Risk');
    expect(screen.queryByRole('list', { name: 'Item action context requirements' })).not.toBeInTheDocument();
  });

  it('applies selected item actions through callbacks', () => {
    const onApplyAction = vi.fn();
    renderItemPanel(<RpgItemPanel actions={[action]} onApplyAction={onApplyAction} />);

    fireEvent.click(screen.getByRole('button', { name: 'Use' }));

    expect(onApplyAction).toHaveBeenCalledWith(action);
  });

  it('falls back to command selection when no action callback is present', () => {
    const onSelectCommand = vi.fn();
    renderItemPanel(<RpgItemPanel actions={[action]} onSelectCommand={onSelectCommand} />);

    fireEvent.click(screen.getByRole('button', { name: 'Use' }));

    expect(onSelectCommand).toHaveBeenCalledWith('Use Field Kit');
  });

  it('renders merchant entries without diagnostic cards or item objectives', () => {
    const onApplyMerchantEntry = vi.fn();
    renderItemPanel(
      <RpgItemPanel
        actions={[action]}
        merchantEntries={[merchantEntry]}
        onApplyMerchantEntry={onApplyMerchantEntry}
      />,
    );

    expect(screen.queryByLabelText('Item status cards')).not.toBeInTheDocument();
    expect(screen.queryByText('Suggested next item steps')).not.toBeInTheDocument();
    expect(screen.getByText('Merchant service')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'buy Torch' }));

    expect(onApplyMerchantEntry).toHaveBeenCalledWith(merchantEntry);
  });

  it('disables actionable rows while pending', () => {
    renderItemPanel(<RpgItemPanel actions={[action]} isPending />);

    expect(screen.getByRole('button', { name: 'Use' })).toBeDisabled();
  });
});
