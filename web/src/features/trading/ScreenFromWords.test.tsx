import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const tradingScannerApi = vi.hoisted(() => ({ propose: vi.fn() }));
vi.mock('./tradingScannerApi', () => ({ tradingScannerApi }));

const { ScreenFromWords } = await import('./ScreenFromWords');

describe('a screen from words (TVP-9.4)', () => {
  it('puts the proposed rules in the editor and says what it could not cover', async () => {
    tradingScannerApi.propose.mockResolvedValue({
      interval: '1h', provider: 'fake', notes: 'Hourly movers.', unsupported: ['above the 200-day average'],
      rules: [{ rule_id: 'proposed-1', metric: 'percent_change', operator: 'gt', threshold: '5', period: 14, lookback_bars: 1, role: 'filter', source: null }],
    });
    const onProposal = vi.fn();
    render(<ScreenFromWords onProposal={onProposal} />);
    expect(screen.getByRole('button', { name: 'Propose rules' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Screen description' }), { target: { value: 'hourly movers above 5%' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Propose rules' })));
    expect(tradingScannerApi.propose).toHaveBeenCalledWith('hourly movers above 5%');
    expect(onProposal).toHaveBeenCalledWith([expect.objectContaining({ metric: 'percent_change', operator: 'gt', role: 'filter' })], '1h');
    expect(screen.getByRole('status')).toHaveTextContent('Proposed 1 rule on 1h: check them below, then save or run. Hourly movers. Not covered: above the 200-day average.');
  });

  it('shows why nothing was proposed', async () => {
    tradingScannerApi.propose.mockRejectedValue(new Error("that description didn't give any screener filters"));
    render(<ScreenFromWords onProposal={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Screen description' }), { target: { value: 'something' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Propose rules' })));
    expect(screen.getByRole('alert')).toHaveTextContent("didn't give any screener filters");
  });
});
