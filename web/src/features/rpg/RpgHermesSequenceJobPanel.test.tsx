import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RpgHermesSequenceJobPanel } from './RpgHermesSequenceJobPanel';
import { renderWithProviders } from '../../test/renderWithProviders';


describe('RpgHermesSequenceJobPanel', () => {
  it('renders progress and controls', () => {
    const onCancel = vi.fn();
    renderWithProviders(
      <RpgHermesSequenceJobPanel
        activeJob={{ id: 'job-1', status: 'running', stages: [{ status: 'completed' }, { status: 'queued' }] } as never}
        onCancel={onCancel}
        onPause={vi.fn()}
        onResume={vi.fn()}
        onStart={vi.fn()}
      />,
    );

    expect(screen.getByRole('region', { name: 'Hermes sequence job' })).toHaveTextContent('50%');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
