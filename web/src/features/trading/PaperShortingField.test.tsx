import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const paperApi = vi.hoisted(() => ({ updateAccountSettings: vi.fn() }));
vi.mock('./tradingPaperApi', () => ({ tradingPaperApi: paperApi }));

import { PaperMarginCallNotifyField, PaperShortingField } from './PaperShortingField';
import { isRiskEntry } from './paperTicketRequests';
import type { PaperAccount } from './paperTypes';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const account = { account_id: 'paper-1', name: 'Paper', revision: 3, allow_short: false } as PaperAccount;

describe('paper shorting (TVP-7.2a)', () => {
  it('is part of the create form, on by default', () => {
    const onDraftChange = vi.fn();
    render(<PaperShortingField mode="create" draftValue onDraftChange={onDraftChange} account={null} onSaved={vi.fn()} />);
    const box = screen.getByRole('checkbox', { name: 'Allow short positions' }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(onDraftChange).toHaveBeenCalledWith(false);
    expect(paperApi.updateAccountSettings).not.toHaveBeenCalled();
  });

  it('saves an account setting at its revision, and says when the account changed elsewhere', async () => {
    const onSaved = vi.fn();
    paperApi.updateAccountSettings.mockResolvedValueOnce({ account: { ...account, allow_short: true, revision: 4 } });
    const view = render(<PaperShortingField mode="settings" draftValue={false} onDraftChange={vi.fn()} account={account} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow short positions' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(paperApi.updateAccountSettings).toHaveBeenCalledWith(account, { allow_short: true });
    paperApi.updateAccountSettings.mockRejectedValueOnce(new Error('Paper Trading request failed (409): revision'));
    view.rerender(<PaperShortingField mode="settings" draftValue={false} onDraftChange={vi.fn()} account={account} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow short positions' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
  });

  it('makes a sell a risk-sized short entry only where shorting is on and nothing long is held', () => {
    expect(isRiskEntry('buy', account, null)).toBe(true);
    expect(isRiskEntry('sell', account, null)).toBe(false);
    expect(isRiskEntry('sell', { allow_short: true }, null)).toBe(true);
    expect(isRiskEntry('sell', { allow_short: true }, { quantity: '5' })).toBe(false);
    expect(isRiskEntry('sell', { allow_short: true }, { quantity: '-5' })).toBe(true);
  });
});

describe('margin-call notifications (TVP-7.2b)', () => {
  it('is off unless asked for, and saves at the account revision', async () => {
    const onSaved = vi.fn();
    paperApi.updateAccountSettings.mockResolvedValueOnce({ account: { ...account, notify_margin_calls: true, revision: 4 } });
    render(<PaperMarginCallNotifyField mode="settings" draftValue={false} onDraftChange={vi.fn()} account={account} onSaved={onSaved} />);
    const box = screen.getByRole('checkbox', { name: 'Send margin calls by email and push' }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(paperApi.updateAccountSettings).toHaveBeenCalledWith(account, { notify_margin_calls: true });
  });
});
