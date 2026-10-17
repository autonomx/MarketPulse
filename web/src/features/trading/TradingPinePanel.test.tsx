import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CoreIndicatorInstance } from './indicators/coreIndicators';

const tradingApi = vi.hoisted(() => ({
  allDocuments: vi.fn(async () => [] as unknown[]),
  document: vi.fn(),
  createDocument: vi.fn(async (_kind: string, recordId: string, payload: Record<string, unknown>) => ({ record_id: recordId, revision: 1, payload, status: 'active' })),
  updateDocument: vi.fn(async (_kind: string, record: { record_id: string; revision: number }, payload: Record<string, unknown>) => ({ record_id: record.record_id, revision: record.revision + 1, payload, status: 'active' })),
}));
const scriptsApi = vi.hoisted(() => ({
  check: vi.fn(async (source: string) => ({
    diagnostics: source.includes('broken') ? [{ kind: 'syntax', message: 'unexpected end', line: 2, column: 1 }] : [],
    declaration: { kind: 'indicator', overlay: !source.includes('overlay=false') }, inputs: [],
  })),
  reference: vi.fn(async () => ({ functions: ['ta.sma'], constants: [], variables: ['close'], keywords: ['if'], signatures: { 'ta.sma': 'source, length' } })),
  run: vi.fn(),
  versions: vi.fn(async () => [] as unknown[]),
  version: vi.fn(),
}));
vi.mock('./tradingApi', () => ({ tradingApi }));
vi.mock('./scripts/scriptsApi', () => ({ scriptsApi }));
// CodeMirror measures layout jsdom doesn't have; the editor is a textarea here (its own pieces are tested apart).
vi.mock('./scripts/ScriptCodeEditor', () => ({
  ScriptCodeEditor: ({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) => (
    <textarea aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

const { TradingPinePanel } = await import('./TradingPinePanel');

const sma: CoreIndicatorInstance = { id: 'sma', period: 20, enabled: true };

function renderPanel(overrides: Partial<Parameters<typeof TradingPinePanel>[0]> = {}) {
  const onSetIndicators = vi.fn();
  const view = render(
    <TradingPinePanel
      indicators={[sma]}
      activeIndicatorId={null}
      onActiveIndicatorChange={vi.fn()}
      instrumentId="equity:NASDAQ:AAPL"
      bindingId={null}
      interval="1h"
      onSetIndicators={onSetIndicators}
      {...overrides}
    />,
  );
  return { ...view, onSetIndicators };
}

const source = () => screen.getByRole('textbox', { name: 'Script source (Pine-compatible)' });
const text = (name: string) => (screen.getByRole('textbox', { name }) as HTMLTextAreaElement).value;

describe('TradingPinePanel (Omnix Scripts editor)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('keeps the draft when the panel closes and opens again', async () => {
    const first = renderPanel({ activeIndicatorId: 'sma' });
    fireEvent.change(source(), { target: { value: 'plot(hl2)' } });
    first.unmount();
    renderPanel({ activeIndicatorId: 'sma' });
    expect(source()).toHaveValue('plot(hl2)');
    // The legend request it already handled doesn't ask again.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('opens a built-in indicator source as an editable, unsaved copy', async () => {
    renderPanel({ activeIndicatorId: 'sma' });
    expect(screen.getByRole('group', { name: 'Omnix Scripts editor' })).toBeInTheDocument();
    expect(text('Script source (Pine-compatible)')).toContain('//@version=6');
    expect(text('Script name')).toContain('(copy)');
    expect(screen.getByRole('status')).toHaveTextContent('Not saved');
    expect(screen.queryByText(/Pine Editor|Pine Script/)).not.toBeInTheDocument();
  });

  it('saves a new script and adds it to the chart as a script indicator', async () => {
    const { onSetIndicators } = renderPanel();
    await waitFor(() => expect(scriptsApi.check).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Add to chart' }));
    await waitFor(() => expect(onSetIndicators).toHaveBeenCalled());
    const [, recordId, payload] = tradingApi.createDocument.mock.calls[0];
    expect(recordId).toMatch(/^s[0-9a-f]{32}$/);
    expect(payload).toEqual({ name: 'My script', source: expect.stringContaining('indicator("My script", overlay=true)') });
    const added = onSetIndicators.mock.calls[0][0] as CoreIndicatorInstance[];
    expect(added.at(-1)).toMatchObject({ id: `script-${recordId}`, enabled: true, params: { name: 'My script', overlay: 1, revision: 1 } });
    expect(screen.getByRole('status')).toHaveTextContent('Saved');
  });

  it('shows the server problems and keeps a broken script off the chart', async () => {
    renderPanel();
    fireEvent.change(source(), { target: { value: '//@version=6\nbroken(' } });
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('1 problem'));
    expect(screen.getByRole('button', { name: 'Add to chart' })).toBeDisabled();
    expect(screen.getByRole('log', { name: 'Script console' })).toHaveTextContent('Line 2: unexpected end');
  });

  it('runs the script on the chart and shows its logs and profile', async () => {
    scriptsApi.run.mockResolvedValue({
      times: [], error: null,
      result: { bars: 500, seconds: 0.012, logs: [{ time: 1, bar: 7, level: 'warning', message: 'watch out' }], profile: [{ line: 3, seconds: 0.01 }, { line: 4, seconds: 0.002 }] },
    });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(screen.getByRole('log', { name: 'Script console' })).toHaveTextContent('watch out'));
    expect(scriptsApi.run).toHaveBeenCalledWith(expect.objectContaining({ instrumentId: 'equity:NASDAQ:AAPL', interval: '1h', profile: false }));
    fireEvent.click(screen.getByRole('tab', { name: 'Profiler' }));
    fireEvent.click(screen.getByRole('button', { name: 'Run with profiler' }));
    await waitFor(() => expect(screen.getByRole('table', { name: 'Time per line' })).toHaveTextContent('83%'));
  });

  it('asks before an open replaces unsaved changes', async () => {
    const { rerender } = renderPanel();
    fireEvent.change(source(), { target: { value: 'plot(close)' } });
    expect(screen.getByRole('status')).toHaveTextContent('Not saved');
    rerender(
      <TradingPinePanel indicators={[sma]} activeIndicatorId="sma" onActiveIndicatorChange={vi.fn()} instrumentId="equity:NASDAQ:AAPL" bindingId={null} interval="1h" onSetIndicators={vi.fn()} />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Unsaved changes');
    expect(source()).toHaveValue('plot(close)');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Discard and open' })));
    expect(text('Script source (Pine-compatible)')).toContain('//@version=6');
  });

  it('saves a copy under a new name and restores a version from the history', async () => {
    tradingApi.allDocuments.mockResolvedValue([{ record_id: 'sabc', revision: 3, status: 'active', payload: { name: 'Mine', source: 'plot(open)\n' } }]);
    scriptsApi.versions.mockResolvedValue([
      { revision: 3, name: 'Mine', saved_at: '2026-10-09T10:00:00Z', characters: 11, lines: 2 },
      { revision: 1, name: 'Mine', saved_at: '2026-10-08T10:00:00Z', characters: 12, lines: 2 },
    ]);
    scriptsApi.version.mockImplementation(async (_id: string, revision: number) => ({ revision, name: 'Mine', saved_at: '', source: revision === 1 ? 'plot(close)\n' : 'plot(open)\n' }));
    renderPanel();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Mine' })).toBeInTheDocument());
    fireEvent.change(screen.getByRole('combobox', { name: 'Open script' }), { target: { value: 'sabc' } });
    expect(source()).toHaveValue('plot(open)\n');

    fireEvent.click(screen.getByRole('tab', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: /Version 1/ }));
    await waitFor(() => expect(screen.getByRole('table', { name: 'Changes' })).toHaveTextContent('plot(close)'));
    fireEvent.click(screen.getByRole('button', { name: 'Restore this version' }));
    expect(source()).toHaveValue('plot(close)\n');
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes');

    fireEvent.click(screen.getByRole('button', { name: 'Save as…' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'New script name' }), { target: { value: 'Other' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save copy' }));
    await waitFor(() => expect(tradingApi.createDocument).toHaveBeenCalledWith('scripts', expect.any(String), { name: 'Other', source: 'plot(close)\n' }));
    expect(tradingApi.updateDocument).not.toHaveBeenCalled();
  });
});
