import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { tradingApi } from '../tradingApi';
import { isIntervalAvailable, TRADING_VIEW_INTERVAL_GROUPS } from '../tradingIntervals';
import { useTradingStore, type TradingTabState } from '../tradingStore';
import type { TradingWorkspacePersistence } from '../persistence/useTradingWorkspacePersistence';
import { formatCommandKeys } from './hotkeyLabels';
import { loadStoredKeyOverrides } from './keyOverridesStorage';
import { TRADING_COMMANDS, commandKeys, tradingCommandDefinition, type TradingCommandDefinition } from './tradingCommands';
import { TradingCommandPalette, type TradingPaletteItem } from './TradingCommandPalette';
import { TradingIntervalInputBox } from './TradingIntervalInputBox';
import { TradingShortcutDialog } from './TradingShortcutDialog';
import { useTradingTabCommands } from './useTradingTabCommands';
import {
  canRunTradingCommand,
  runTradingCommand,
  setTradingCommandKeyOverrides,
  tradingCommandAvailability,
  tradingCommandKeyOverrides,
  useTradingCommand,
  useTradingCommandKeyOverrides,
} from './useTradingCommands';

type PaletteMode = 'all' | 'layouts';

const SHORTCUTS_COMMAND = tradingCommandDefinition('workspace.shortcuts')!;

/**
 * Workspace keyboard commands and the surfaces they open: the command
 * palette (Ctrl+K), the layout list (.) and the interval box (digits), plus
 * the tab and chart-switching commands.
 */
export function TradingKeyboardLayer({
  persistence,
  supportedIntervals,
  onOpenSymbolSearch,
  onCloseTab,
}: {
  persistence: Pick<TradingWorkspacePersistence, 'status' | 'workspaces' | 'activeWorkspaceId' | 'selectWorkspace' | 'saveNow'>;
  supportedIntervals: readonly string[];
  onOpenSymbolSearch: (typed?: string) => void;
  /** The workspace's close-tab action, which asks before closing. */
  onCloseTab: (tab: TradingTabState) => void;
}) {
  const [paletteMode, setPaletteMode] = useState<PaletteMode | null>(null);
  const [intervalText, setIntervalText] = useState<string | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const overrides = useTradingCommandKeyOverrides();
  const [saveNotice, setSaveNotice] = useState('');
  const instruments = useQuery({ queryKey: ['trading', 'instruments'], queryFn: () => tradingApi.instruments() });
  const updateChart = useTradingStore((state) => state.updateChart);
  const activeChartId = useTradingStore((state) => state.activeChartId);
  const workspaceReady = persistence.status !== 'loading';

  const openPalette = (mode: PaletteMode) => setPaletteMode(mode);
  const openShortcuts = () => setShortcutsOpen(true);
  // Ctrl+S also works in fields and dialogs, so the browser's Save dialog never opens.
  const saveLayout = () => {
    if (persistence.status === 'conflict') {
      setSaveNotice('Layout not saved: this workspace changed elsewhere. Choose Reload server or Overwrite server first.');
      return;
    }
    setSaveNotice('');
    if (workspaceReady) void persistence.saveNow();
  };

  // Rebound keys from earlier visits (TVP-2.4).
  useEffect(() => {
    setTradingCommandKeyOverrides(loadStoredKeyOverrides());
  }, []);

  useTradingCommand('chart.symbolSearch', (event) => onOpenSymbolSearch(event?.key));
  useTradingCommand('chart.intervalInput', (event) => setIntervalText(event && event.key !== ',' ? event.key : ''));
  useTradingCommand('workspace.commandPalette', () => openPalette('all'));
  useTradingCommand('workspace.shortcuts', openShortcuts);
  useTradingCommand('layout.load', () => openPalette('layouts'), () => workspaceReady);
  useTradingCommand('layout.save', saveLayout);
  useTradingTabCommands(onCloseTab);

  const layoutItems = useMemo<TradingPaletteItem[]>(() => persistence.workspaces.map((workspace) => ({
    id: `layout:${workspace.workspaceId}`,
    label: workspace.workspaceId === persistence.activeWorkspaceId ? `${workspace.name} (open)` : workspace.name,
    group: 'Layout',
    run: () => void persistence.selectWorkspace(workspace.workspaceId),
  })), [persistence]);

  const paletteItems = useMemo<TradingPaletteItem[]>(() => {
    if (paletteMode !== 'all') return [];
    const currentOverrides = tradingCommandKeyOverrides();
    const availability = tradingCommandAvailability();
    const commands = (TRADING_COMMANDS as readonly TradingCommandDefinition[])
      .filter((definition) => definition.id !== 'workspace.commandPalette')
      .map((definition) => ({
        id: `command:${definition.id}`,
        label: definition.label,
        group: definition.group,
        keys: formatCommandKeys(definition, commandKeys(definition, currentOverrides, availability)),
        disabled: !canRunTradingCommand(definition.id),
        unavailableText: definition.planned ? 'not available yet' : undefined,
        run: () => { runTradingCommand(definition.id); },
      }));
    const intervals = TRADING_VIEW_INTERVAL_GROUPS.flatMap((group) => group.options)
      .filter((option) => isIntervalAvailable(option.value, supportedIntervals))
      .map((option) => ({
        id: `interval:${option.value}`,
        label: `Interval ${option.label}`,
        group: 'Interval',
        run: () => updateChart(activeChartId, { interval: option.value }),
      }));
    const symbols = (instruments.data ?? [])
      .filter((instrument) => instrument.asset_class !== 'crypto' || instrument.venue === 'BINANCE')
      .map((instrument) => ({
        id: `symbol:${instrument.instrument_id}`,
        label: `${instrument.display_symbol} · ${instrument.venue}`,
        group: 'Symbol',
        run: () => updateChart(activeChartId, { instrumentId: instrument.instrument_id, bindingId: null }),
      }));
    return [...commands, ...layoutItems.map((item) => ({ ...item, label: `Load layout ${item.label}` })), ...intervals, ...symbols];
  }, [activeChartId, instruments.data, layoutItems, paletteMode, supportedIntervals, updateChart]);

  const shortcutKeys = formatCommandKeys(SHORTCUTS_COMMAND, commandKeys(SHORTCUTS_COMMAND, overrides));

  return (
    <>
      <button type="button" aria-haspopup="dialog" aria-expanded={shortcutsOpen} title={shortcutKeys.length ? `Keyboard shortcuts (${shortcutKeys.join(', ')})` : 'Keyboard shortcuts'} onClick={openShortcuts}>
        Shortcuts
      </button>
      <span className="trading-save-notice" role="status">{saveNotice}</span>
      <TradingShortcutDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      <TradingCommandPalette
        open={paletteMode !== null}
        title={paletteMode === 'layouts' ? 'Load layout' : 'Command palette'}
        placeholder={paletteMode === 'layouts' ? 'Search layouts' : 'Search commands, symbols, intervals and layouts'}
        items={paletteMode === 'layouts' ? layoutItems : paletteItems}
        onClose={() => setPaletteMode(null)}
      />
      <TradingIntervalInputBox
        initialText={intervalText}
        supportedIntervals={supportedIntervals}
        onApply={(interval) => updateChart(activeChartId, { interval })}
        onClose={() => setIntervalText(null)}
      />
    </>
  );
}
