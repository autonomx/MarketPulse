import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentRunLimitsSection } from './AgentRunLimitsSection';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';

const dispatch = vi.fn();

vi.mock('./SettingsProfileContext', () => ({
  useSettingsProfileContext: () => ({
    state: { draft: { ...DEFAULT_SETTINGS_DOCUMENT, agentRuns: { defaultMaxOutputTokens: null, defaultMaxCostUsd: 2, providerPrices: { openrouter: { inputUsdPerMillion: 1, outputUsdPerMillion: 4 } } } } },
    dispatch,
  }),
}));

describe('agent run limits', () => {
  it('shows empty fields as no limit and saves numbers or null', () => {
    dispatch.mockClear();
    render(<AgentRunLimitsSection />);

    const tokens = screen.getByLabelText('Default output token limit') as HTMLInputElement;
    expect(tokens.value).toBe('');
    fireEvent.change(tokens, { target: { value: '50000' } });
    expect(dispatch).toHaveBeenLastCalledWith({ type: 'update', path: 'agentRuns.defaultMaxOutputTokens', value: 50000 });

    fireEvent.change(screen.getByLabelText('Default cost limit (USD)'), { target: { value: '' } });
    expect(dispatch).toHaveBeenLastCalledWith({ type: 'update', path: 'agentRuns.defaultMaxCostUsd', value: null });
  });

  it('edits one provider price and keeps the other field', () => {
    dispatch.mockClear();
    render(<AgentRunLimitsSection />);

    fireEvent.change(screen.getByLabelText('OpenRouter output price'), { target: { value: '6' } });
    expect(dispatch).toHaveBeenLastCalledWith({
      type: 'update', path: 'agentRuns.providerPrices', value: { openrouter: { inputUsdPerMillion: 1, outputUsdPerMillion: 6 } },
    });
    fireEvent.change(screen.getByLabelText('Cerebras input price'), { target: { value: '0.5' } });
    expect(dispatch).toHaveBeenLastCalledWith({
      type: 'update', path: 'agentRuns.providerPrices',
      value: { openrouter: { inputUsdPerMillion: 1, outputUsdPerMillion: 4 }, cerebras: { inputUsdPerMillion: 0.5, outputUsdPerMillion: 0 } },
    });
  });
});
