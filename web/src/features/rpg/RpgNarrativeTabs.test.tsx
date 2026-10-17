import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgNarrativeTabs } from './RpgNarrativeTabs';
import type { RpgJournalDetailPreview, RpgJournalEntryPreview } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';

const journalDetail: RpgJournalDetailPreview = { title: 'Journal', detail: 'Daily accounts', bullets: [], tags: [] };
const days = (count: number): RpgJournalEntryPreview[] =>
  Array.from({ length: count }, (_, index) => ({ time: `Day ${count - index}`, title: `Entry ${count - index}`, detail: `Account of day ${count - index}` }));

beforeEach(() => {
  // jsdom has no layout: the journal list is 384px tall and every entry 48px.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('rpg-journal-list') ? 384 : 48;
  });
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(400);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('RpgNarrativeTabs journal', () => {
  it('renders a bounded window of a thousand-day journal and still selects an entry', async () => {
    renderWithProviders(<RpgNarrativeTabs journalDetail={journalDetail} journalEntries={days(1_000)} logEntries={[]} recentEvents={[]} />);
    const panel = screen.getByRole('tabpanel');

    await waitFor(() => expect(within(panel).getAllByRole('button').length).toBeGreaterThan(0));
    expect(within(panel).getAllByRole('button').length).toBeLessThan(60);
    expect(within(panel).getByRole('heading', { name: 'Entry 1000' })).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole('button', { name: /Day 999/ }));
    expect(within(panel).getByRole('heading', { name: 'Entry 999' })).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: /Day 999/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders every entry of a short journal', () => {
    renderWithProviders(<RpgNarrativeTabs journalDetail={journalDetail} journalEntries={days(12)} logEntries={[]} recentEvents={[]} />);
    expect(within(screen.getByRole('tabpanel')).getAllByRole('button')).toHaveLength(12);
  });
});
