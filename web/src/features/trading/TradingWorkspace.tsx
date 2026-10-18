/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useInstalledAppCommandKeys } from './installedApp';
import { useWindowTabs } from './tradingWindowSets';
import { TradingWindowRestore } from './TradingWindowRestore';
import { useAdvancedViewRequests } from './advancedView';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useTradingInstrumentLink } from './useTradingInstrumentLink';
import { useTradingCommandDispatcher } from './commands/useTradingCommands';
import { TradingKeyboardLayer } from './commands/TradingKeyboardLayer';
import type { OmnixModuleDefinition } from '../../app/modules';
import { TradingChartGrid } from './TradingChartGrid';
import { TradingIndicatorManager } from './TradingIndicatorManager';
import { TradingSidePanel } from './TradingSidePanel';
import { TradingSideRail } from './TradingSideRail';
import type { TradingSideTab } from './TradingSidePanel';
import type { CoreIndicatorId } from './indicators/coreIndicators';
import { TradingSymbolSearch, type TradingFormulaSearchPreview } from './TradingSymbolSearch';
import { TradingAlertToastLayer } from './TradingAlertToastLayer';
import { TradingOrderToastLayer } from './TradingOrderToastLayer';
import { PaperOrderNotificationsWatch } from './PaperOrderNotificationsWatch';
import { TradingDrawingTools } from './TradingDrawingTools';
import { TradingSessionTabs } from './TradingSessionTabs';
import { TradingDesktopTitleBar } from './TradingDesktopTitleBar';
import { desktopBridge } from '../../shared/desktopBridge';
import { openTradingWindow, useTradingWindowPresence } from './windowPresence';
import { tradingApi } from './tradingApi';
import type { DrawingSnapMode } from './drawings/drawingCommands';
import { TradingChartTypeMenu } from './TradingChartTypeMenu';
import { TradingChartLayoutPicker } from './TradingChartLayoutPicker';
import { useTradingWorkspacePersistence } from './persistence/useTradingWorkspacePersistence';
import { duplicateTradingWorkspace } from './persistence/duplicateWorkspace';
import { buildTradingWorkspaceExport, downloadTradingWorkspaceExport } from './tradingExport';
import { WorkspaceImportButton } from './persistence/WorkspaceImportButton';
import { preferredCryptoInstrument } from './cryptoInstrumentDefaults';
import { isIntervalAvailable } from './tradingIntervals';
import { TradingIntervalMenu } from './TradingIntervalMenu';
import {
  MAX_TRADING_CHARTS,
  MAX_TRADING_TABS,
  MIN_TRADING_CHARTS,
  useTradingStore,
  type TradingLayout,
} from './tradingStore';
import type { CanonicalInstrument, ProviderBinding, TradingAlert } from './tradingTypes';
import { encodeTradingFormula, parseTradingFormula, tradingFormulaDisplaySymbol } from './tradingFormula';
import './TradingWorkspace.css';
import './TradingAdvanced.css';
import './TradingFlexibleLayout.css';
import './TradingCompactHeader.css';
import './TradingIndicatorMenuOverlay.css';
import './TradingToolbarAlignment.css';
import './TradingChartEnhancements.css';
import './TradingIntervalMenu.css';
import './TradingSideRail.css';
import './TradingLayoutConstraints.css';
import './TradingLightTheme.css';
import './TradingTerminalLook.css';
import './TradingChartPan.css';
import './TradingChartChrome.css';
import './TradingTypography.css';
import './TradingToolFullscreen.css';
import { usePaperTicketRequests } from './paperTicketRequests';
import './TradingSessionTabs.css';
import './TradingChartLayoutPicker.css';

const TradingReplayPanel = lazy(() => import('./TradingReplayPanel').then((module) => ({ default: module.TradingReplayPanel })));
const TradingScannerPanel = lazy(() => import('./TradingScannerPanel').then((module) => ({ default: module.TradingScannerPanel })));
const TradingStrategiesPanel = lazy(() => import('./TradingStrategiesPanel').then((module) => ({ default: module.TradingStrategiesPanel })));
const TradingEconomicCalendar = lazy(() => import('./TradingEconomicCalendar').then((module) => ({ default: module.TradingEconomicCalendar })));
const TradingAdvancedView = lazy(() => import('./TradingAdvancedView').then((module) => ({ default: module.TradingAdvancedView })));
const TradingOptions = lazy(() => import('./TradingOptions').then((module) => ({ default: module.TradingOptions })));
const TradingYieldCurve = lazy(() => import('./TradingYieldCurve').then((module) => ({ default: module.TradingYieldCurve })));
const TradingScriptScreener = lazy(() => import('./TradingScriptScreener').then((module) => ({ default: module.TradingScriptScreener })));
const TradingEventsCalendar = lazy(() => import('./TradingEventsCalendar').then((module) => ({ default: module.TradingEventsCalendar })));
const TradingFinancials = lazy(() => import('./TradingFinancials').then((module) => ({ default: module.TradingFinancials })));
const TradingHeatmap = lazy(() => import('./TradingHeatmap').then((module) => ({ default: module.TradingHeatmap })));
const TradingSeasonals = lazy(() => import('./TradingSeasonals').then((module) => ({ default: module.TradingSeasonals })));
const TradingStrategyTester = lazy(() => import('./scripts/TradingStrategyTester').then((module) => ({ default: module.TradingStrategyTester })));
const TradingTerminalDock = lazy(() => import('./TradingTerminalDock').then((module) => ({ default: module.TradingTerminalDock })));

const gridOptions: Array<{ id: TradingLayout; label: string }> = [
  { id: 'auto', label: 'Auto grid' },
  { id: 'columns-1', label: '1 column' },
  { id: 'columns-2', label: '2 columns' },
  { id: 'columns-3', label: '3 columns' },
  { id: 'columns-4', label: '4 columns' },
];

type ToolPanel = 'scanner' | 'replay' | 'strategies' | 'tester' | 'seasonals' | 'heatmap' | 'financials' | 'calendar' | 'events' | 'overview' | 'script-screener' | 'yield-curve' | 'options';

const TOOL_PANEL_TITLES: Record<ToolPanel, string> = {
  scanner: 'Market scanner', replay: 'Replay & backtest', strategies: 'Automated strategies', tester: 'Strategy tester', seasonals: 'Seasonals',
  heatmap: 'Heatmap', financials: 'Financials', calendar: 'Economic calendar', events: 'Earnings & dividends', overview: 'Advanced view',
  'script-screener': 'Script screener', 'yield-curve': 'US Treasury yield curve', options: 'Options',
};

/** The tool drawer's title; tools on one symbol name it. */
function toolPanelTitle(panel: ToolPanel, instrumentId: string): string {
  return panel === 'seasonals' || panel === 'financials' || panel === 'options' ? `${TOOL_PANEL_TITLES[panel]} · ${instrumentId}` : TOOL_PANEL_TITLES[panel];
}

type FormulaResolution = TradingFormulaSearchPreview & { operands: Record<string, string> };

// TradingSidePanel mounts TradingPaperPanel in the dedicated Trade tab.

function preferredInterval(binding: ProviderBinding, current: string): string {
  if (isIntervalAvailable(current, binding.supported_intervals)) return current;
  for (const candidate of ['1h', '2h', '4h', '1d', '15m', '5m', '1m', '1w']) {
    if (binding.supported_intervals.includes(candidate)) return candidate;
  }
  return binding.supported_intervals[0] ?? current;
}

function preferredInstrument(
  instrument: CanonicalInstrument,
  instruments: readonly CanonicalInstrument[],
): CanonicalInstrument {
  return preferredCryptoInstrument(instrument, instruments);
}

export function TradingWorkspace({ module }: { module: OmnixModuleDefinition }) {
  useTradingWindowPresence();
  useTradingCommandDispatcher();
  useInstalledAppCommandKeys();
  const navigate = useNavigate();
  const [focusMode, setFocusMode] = useState(false);
  const desktop = desktopBridge();
  const [symbolQuery, setSymbolQuery] = useState('');
  const [symbolSearchResults, setSymbolSearchResults] = useState<CanonicalInstrument[]>([]);
  const [symbolSearchOpen, setSymbolSearchOpen] = useState(false);
  const [symbolSearchTyped, setSymbolSearchTyped] = useState(false);
  const [symbolSearchChartId, setSymbolSearchChartId] = useState<string | null>(null);
  const [symbolSearchLoading, setSymbolSearchLoading] = useState(false);
  const [formulaResolution, setFormulaResolution] = useState<FormulaResolution | null>(null);
  const [toolPanel, setToolPanel] = useState<ToolPanel | null>(null);
  const [toolPanelFullscreen, setToolPanelFullscreen] = useState(false);
  const [advancedWatchlistId, setAdvancedWatchlistId] = useState<string | null>(null);
  useAdvancedViewRequests((watchlistId) => { setAdvancedWatchlistId(watchlistId); setToolPanel('overview'); setToolPanelFullscreen(true); });
  const [sidePanelTab, setSidePanelTab] = useState<TradingSideTab>('watchlist');
  const [pineIndicatorId, setPineIndicatorId] = useState<CoreIndicatorId | null>(null);
  const [paperAccountId, setPaperAccountId] = useState<string | null>(null);
  const persistence = useTradingWorkspacePersistence();
  const workspaceHydrated = persistence.status !== 'loading';
  const providers = useQuery({ queryKey: ['trading', 'providers'], queryFn: tradingApi.providers });
  const instruments = useQuery({ queryKey: ['trading', 'instruments'], queryFn: () => tradingApi.instruments() });
  const layout = useTradingStore((state) => state.layout);
  const tabs = useTradingStore((state) => state.tabs);
  const activeTabId = useTradingStore((state) => state.activeTabId);
  const charts = useTradingStore((state) => state.charts);
  const activeChartId = useTradingStore((state) => state.activeChartId);
  const replayMode = useTradingStore((state) => state.replayMode);
  const drawingTool = useTradingStore((state) => state.drawingTool);
  const drawingSnapMode = useTradingStore((state) => state.drawingSnapMode);
  const links = useTradingStore((state) => state.links);
  const panels = useTradingStore((state) => state.panels);
  const favoriteInstrumentIds = useTradingStore((state) => state.favoriteInstrumentIds);
  const setLayout = useTradingStore((state) => state.setLayout);
  const setActiveTab = useTradingStore((state) => state.setActiveTab);
  const windowTabs = useWindowTabs({ workspaceId: persistence.activeWorkspaceId, tabs, activeTabId, setActiveTab, ready: persistence.status !== 'loading' });
  const removeTab = useTradingStore((state) => state.removeTab);
  const setChartCount = useTradingStore((state) => state.setChartCount);
  const addChart = useTradingStore((state) => state.addChart);
  const removeChart = useTradingStore((state) => state.removeChart);
  const setActiveChart = useTradingStore((state) => state.setActiveChart);
  const setReplayMode = useTradingStore((state) => state.setReplayMode);
  const setDrawingTool = useTradingStore((state) => state.setDrawingTool);
  const setDrawingSnapMode = useTradingStore((state) => state.setDrawingSnapMode);
  const updateChart = useTradingStore((state) => state.updateChart);
  const toggleIndicator = useTradingStore((state) => state.toggleIndicator);
  const updateIndicator = useTradingStore((state) => state.updateIndicator);
  const setIndicators = useTradingStore((state) => state.setIndicators);
  const setLink = useTradingStore((state) => state.setLink);
  const setPanel = useTradingStore((state) => state.setPanel);
  const toggleFavoriteInstrument = useTradingStore((state) => state.toggleFavoriteInstrument);
  const activeChart = charts.find((chart) => chart.chartId === activeChartId) ?? charts[0];
  const providerBindings = useMemo(
    () => {
      const bindingsById = new Map<string, ProviderBinding>();
      for (const binding of (providers.data ?? []).flatMap((provider) => provider.bindings ?? [])) {
        if (!bindingsById.has(binding.binding_id)) bindingsById.set(binding.binding_id, binding);
      }
      return [...bindingsById.values()];
    },
    [providers.data],
  );
  const availableBindings = useMemo(
    () => providerBindings.filter((binding) => binding.instrument_id === activeChart.instrumentId),
    [activeChart.instrumentId, providerBindings],
  );
  const selectedBinding = availableBindings.find((binding) => binding.binding_id === activeChart.bindingId)
    ?? availableBindings[0];
  const visibleInstruments = useMemo(() => (instruments.data ?? []).filter((instrument) => (
    instrument.asset_class !== 'crypto' || instrument.venue === 'BINANCE'
  )), [instruments.data]);
  const activeInstrument = useMemo(
    () => (instruments.data ?? []).find((instrument) => instrument.instrument_id === activeChart.instrumentId),
    [activeChart.instrumentId, instruments.data],
  );
  const supportedIntervals = selectedBinding?.supported_intervals ?? [];
  const favorite = favoriteInstrumentIds.includes(activeChart.instrumentId);

  useEffect(() => {
    if (!activeInstrument) return;
    const preferred = preferredInstrument(activeInstrument, instruments.data ?? []);
    if (preferred.instrument_id === activeChart.instrumentId) return;
    updateChart(activeChartId, { instrumentId: preferred.instrument_id, bindingId: null });
  }, [activeChart.instrumentId, activeChartId, activeInstrument, instruments.data, updateChart]);

  useTradingInstrumentLink({
    workspaceHydrated,
    instruments: instruments.data,
    activeInstrumentId: activeChart.instrumentId,
    showInstrument: (instrumentId) => updateChart(activeChartId, { instrumentId, bindingId: null }),
  });

  useEffect(() => {
    if (!selectedBinding || isIntervalAvailable(activeChart.interval, selectedBinding.supported_intervals)) return;
    updateChart(activeChartId, { interval: preferredInterval(selectedBinding, activeChart.interval) });
  }, [activeChart.interval, activeChartId, selectedBinding?.binding_id, updateChart]);

  const exportWorkspace = () => downloadTradingWorkspaceExport(buildTradingWorkspaceExport({ layout, activeChartId, charts, links }));

  useEffect(() => {
    const query = symbolQuery.trim();
    if (!symbolSearchOpen || !query) {
      setSymbolSearchResults([]);
      setSymbolSearchLoading(false);
      setFormulaResolution(null);
      return;
    }

    const formulaHints = (instruments.data ?? []).flatMap((instrument) => [
      instrument.display_symbol,
      instrument.venue_symbol,
      instrument.instrument_id,
    ]);
    const formula = parseTradingFormula(query, { symbolHints: formulaHints });
    let cancelled = false;
    setSymbolSearchLoading(true);
    if (formula) {
      setFormulaResolution({ formula, operands: {}, unresolvedSymbols: formula.symbols, loading: true });
      const resolve = async () => {
        const matches = await Promise.all(formula.symbols.map((symbol) => tradingApi.instruments(symbol)));
        if (cancelled) return;
        const candidates = matches.flat();
        const operands: Record<string, string> = {};
        const unresolvedSymbols: string[] = [];
        for (const symbol of formula.symbols) {
          const normalized = symbol.toUpperCase();
          const symbolCandidates = [...(instruments.data ?? []), ...candidates]
            .filter((instrument, index, all) => all.findIndex((item) => item.instrument_id === instrument.instrument_id) === index)
            .filter((instrument) => [instrument.display_symbol, instrument.venue_symbol, instrument.instrument_id]
              .some((value) => value.toUpperCase() === normalized || value.toUpperCase().replace(/[-:_/]/g, '') === normalized.replace(/[-:_/]/g, '')))
            .sort((left, right) => left.display_symbol.localeCompare(right.display_symbol) || left.venue.localeCompare(right.venue));
          const match = symbolCandidates[0];
          if (!match) unresolvedSymbols.push(symbol);
          else operands[symbol] = preferredInstrument(match, symbolCandidates).instrument_id;
        }
        setSymbolSearchResults(candidates);
        setFormulaResolution({ formula, operands, unresolvedSymbols, loading: false });
        setSymbolSearchLoading(false);
      };
      void resolve().catch(() => {
        if (!cancelled) {
          setFormulaResolution({ formula, operands: {}, unresolvedSymbols: formula.symbols, loading: false });
          setSymbolSearchResults([]);
          setSymbolSearchLoading(false);
        }
      });
      return () => {
        cancelled = true;
      };
    }
    setFormulaResolution(null);
    const timer = window.setTimeout(() => {
      void tradingApi.instruments(query).then((matches) => {
        if (!cancelled) {
          setSymbolSearchResults(matches);
          setSymbolSearchLoading(false);
        }
      }).catch(() => {
        if (!cancelled) {
          setSymbolSearchResults([]);
          setSymbolSearchLoading(false);
        }
      });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [instruments.data, symbolQuery, symbolSearchOpen]);

  const applySymbolMatch = (match: CanonicalInstrument, candidates: readonly CanonicalInstrument[]) => {
    const next = preferredInstrument(match, [...(instruments.data ?? []), ...candidates]);
    const targetChartId = symbolSearchChartId ?? activeChartId;
    updateChart(targetChartId, { instrumentId: next.instrument_id, bindingId: null });
    setActiveChart(targetChartId);
    setSymbolQuery(next.display_symbol);
    setSymbolSearchResults([]);
    setSymbolSearchOpen(false);
    setSymbolSearchChartId(null);
    void instruments.refetch();
    void providers.refetch();
  };

  const applyFormulaResolution = (resolution: FormulaResolution | null) => {
    if (!resolution || resolution.loading) return;
    const targetChartId = symbolSearchChartId ?? activeChartId;
    const operands = Object.fromEntries(
      resolution.formula.symbols.map((symbol) => [symbol, resolution.operands[symbol] ?? symbol]),
    );
    const instrumentId = encodeTradingFormula(resolution.formula.expression, operands);
    updateChart(targetChartId, { instrumentId, bindingId: null });
    setActiveChart(targetChartId);
    setSymbolQuery(resolution.formula.expression);
    setSymbolSearchResults([]);
    setFormulaResolution(null);
    setSymbolSearchOpen(false);
    setSymbolSearchChartId(null);
  };

  const selectBinding = (bindingId: string) => {
    const binding = availableBindings.find((candidate) => candidate.binding_id === bindingId);
    if (!binding) return;
    updateChart(activeChartId, {
      bindingId: binding.binding_id,
      interval: preferredInterval(binding, activeChart.interval),
    });
  };

  const navigateToAlert = (alert: TradingAlert) => {
    const alertInterval = alert.evaluation_policy.interval;
    const activateAlertIndicator = (chartId: string) => {
      const indicatorId = alert.parameters.indicator_id;
      if (!indicatorId) return;

      const indicator = charts
        .find((chart) => chart.chartId === chartId)
        ?.indicators.find((candidate) => candidate.id === indicatorId);
      const period = alert.parameters.period;

      if (!indicator) {
        toggleIndicator(chartId, indicatorId, period);
        return;
      }
      if (!indicator.enabled) {
        toggleIndicator(chartId, indicatorId, period);
        return;
      }
      if (indicator.period !== period) updateIndicator(chartId, indicatorId, { period });
    };
    const matchingChart = charts.find((chart) => (
      chart.instrumentId === alert.instrument_id
      && Boolean(alert.binding_id)
      && chart.bindingId === alert.binding_id
    )) ?? charts.find((chart) => chart.instrumentId === alert.instrument_id);

    if (matchingChart) {
      updateChart(matchingChart.chartId, {
        interval: alertInterval,
        ...(alert.binding_id ? { bindingId: alert.binding_id } : {}),
      });
      setActiveChart(matchingChart.chartId);
      activateAlertIndicator(matchingChart.chartId);
      return;
    }

    const instrument = (instruments.data ?? []).find((item) => item.instrument_id === alert.instrument_id);
    const preferred = instrument ? preferredInstrument(instrument, instruments.data ?? []) : null;
    updateChart(activeChartId, {
      instrumentId: preferred?.instrument_id ?? alert.instrument_id,
      bindingId: alert.binding_id ?? null,
      interval: alertInterval,
    });
    setActiveChart(activeChartId);
    activateAlertIndicator(activeChartId);
  };

  // A scanner result or heatmap tile on the active chart, through the catalog's preferred listing.
  const showInstrumentOnChart = (instrumentId: string) => {
    const instrument = (instruments.data ?? []).find((item) => item.instrument_id === instrumentId);
    updateChart(activeChartId, { instrumentId: (instrument ? preferredInstrument(instrument, instruments.data ?? []) : null)?.instrument_id ?? instrumentId, bindingId: null });
  };
  const toggleToolPanel = (panel: ToolPanel) => {
    setToolPanelFullscreen(false);
    setToolPanel((current) => current === panel ? null : panel);
  };

  const openPaperTrading = () => {
    setSidePanelTab('paper');
    setPanel('right', true);
    setToolPanelFullscreen(false);
    setToolPanel(null);
  };
  usePaperTicketRequests(openPaperTrading);

  const openResearchPanel = () => {
    setSidePanelTab('research');
    setPanel('right', true);
    setToolPanelFullscreen(false);
    setToolPanel(null);
  };

  useEffect(() => {
    if (!toolPanelFullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) setToolPanelFullscreen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [toolPanelFullscreen]);

  const createWorkspace = () => {
    const name = window.prompt('Workspace name', `Workspace ${persistence.workspaces.length + 1}`);
    if (name) void persistence.createWorkspace(name);
  };

  const renameWorkspace = () => {
    const name = window.prompt('Rename workspace', persistence.activeWorkspaceName);
    if (name) void persistence.renameWorkspace(name);
  };

  const duplicateWorkspace = () => {
    const name = window.prompt('Duplicate layout as', `${persistence.activeWorkspaceName} copy`);
    if (!name?.trim()) return;
    void duplicateTradingWorkspace(persistence, name).catch(() => {
      window.alert('The layout was duplicated, but its drawings could not be copied.');
    });
  };

  const deleteWorkspace = () => {
    if (persistence.workspaces.length <= 1) return;
    if (window.confirm(`Delete ${persistence.activeWorkspaceName}?`)) void persistence.deleteWorkspace();
  };

  const sessionTabLabel = (tab: (typeof tabs)[number]) => {
    const chart = tab.charts.find((item) => item.chartId === tab.activeChartId) ?? tab.charts[0];
    if (!chart) return tab.name;
    const instrument = (instruments.data ?? []).find((item) => item.instrument_id === chart.instrumentId);
    return instrument?.display_symbol
      ?? tradingFormulaDisplaySymbol(chart.instrumentId)
      ?? chart.instrumentId.split(':').at(-1)?.replace('-', '/')
      ?? tab.name;
  };

  const closeTabSession = (tab: (typeof tabs)[number]) => {
    if (tabs.length <= 1) return;
    if (window.confirm(`Close ${sessionTabLabel(tab)}?`)) removeTab(tab.tabId);
  };

  const openSymbolSearch = (chartId = activeChartId, typed?: string) => {
    const targetChart = charts.find((chart) => chart.chartId === chartId) ?? activeChart;
    const targetInstrument = (instruments.data ?? []).find((instrument) => instrument.instrument_id === targetChart.instrumentId);
    setActiveChart(targetChart.chartId);
    setSymbolSearchChartId(targetChart.chartId);
    setSymbolSearchTyped(typed !== undefined);
    setSymbolQuery(typed ?? targetInstrument?.display_symbol ?? tradingFormulaDisplaySymbol(targetChart.instrumentId) ?? targetChart.instrumentId.split(':').at(-1)?.replace('-', '/') ?? '');
    setSymbolSearchResults([]);
    setFormulaResolution(null);
    setSymbolSearchOpen(true);
  };

  const openPineEditor = (id: CoreIndicatorId) => {
    setPineIndicatorId(id);
    setSidePanelTab('pine');
    setPanel('right', true);
    setToolPanelFullscreen(false);
    setToolPanel(null);
  };

  const closeSymbolSearch = () => {
    setSymbolSearchOpen(false);
    setSymbolSearchChartId(null);
    setFormulaResolution(null);
  };

  const symbolSearchTarget = charts.find((chart) => chart.chartId === symbolSearchChartId) ?? activeChart;
  const symbolSearchTargetInstrument = (instruments.data ?? []).find(
    (instrument) => instrument.instrument_id === symbolSearchTarget.instrumentId,
  );

  const activeSymbolLabel = activeInstrument?.display_symbol
    ?? tradingFormulaDisplaySymbol(activeChart.instrumentId)
    ?? activeChart.instrumentId.split(':').at(-1)?.replace('-', '/')
    ?? 'Select symbol';

  const sessionTabs = (
    <TradingSessionTabs
      tabs={windowTabs.visibleTabs}
      activeTabId={activeTabId}
      canAdd={tabs.length < MAX_TRADING_TABS}
      onPopOut={windowTabs.popOut} onMoveToMain={windowTabs.moveToMain}
      getTabLabel={sessionTabLabel}
      onSelect={setActiveTab}
      onClose={closeTabSession}
      workspaceId={persistence.activeWorkspaceId} workspaces={persistence.workspaces} onSelectWorkspace={(id) => void persistence.selectWorkspace(id)}
      onOpenTool={(tool) => (tool === 'paper' ? openPaperTrading() : (setToolPanelFullscreen(false), setToolPanel(tool)))}
    />
  );

  return (
    <main className={`trading-workspace${focusMode ? ' trading-focus-mode' : ''}`} aria-labelledby="trading-title">
      {/* In the desktop app the session tabs live in the window's title bar (MarketPulse W-4). */}
      {desktop ? (
        <TradingDesktopTitleBar bridge={desktop} onOpenSettings={() => { void navigate({ to: '/settings' }); }}>
          {sessionTabs}
        </TradingDesktopTitleBar>
      ) : null}
      <header className="trading-terminal-header">
        <div className="trading-terminal-brand">
          <span className="trading-terminal-logo" aria-hidden="true" />
          <div><strong id="trading-title">{module.label}</strong><small>Research &amp; paper terminal</small></div>
        </div>

        <div className="trading-workspace-switcher">
          <select
            aria-label="Saved Trading workspace"
            value={persistence.activeWorkspaceId}
            onChange={(event) => void persistence.selectWorkspace(event.target.value)}
            disabled={!workspaceHydrated}
          >
            {persistence.workspaces.map((workspace) => (
              <option key={workspace.workspaceId} value={workspace.workspaceId}>{workspace.name}</option>
            ))}
          </select>
          <button type="button" aria-label="Create workspace" onClick={createWorkspace} disabled={!workspaceHydrated}>+</button>
          <button type="button" aria-label="Rename workspace" onClick={renameWorkspace} disabled={!workspaceHydrated}>Rename</button>
          <button type="button" aria-label="Duplicate workspace" onClick={duplicateWorkspace} disabled={!workspaceHydrated}>Duplicate</button>
          <button type="button" aria-label="Open workspace in a new window" title="Open in a new window" onClick={() => openTradingWindow(persistence.activeWorkspaceId)} disabled={!workspaceHydrated}>⧉</button>
          <button type="button" aria-label="Delete workspace" onClick={deleteWorkspace} disabled={!workspaceHydrated || persistence.workspaces.length <= 1}>Delete</button>
        </div>

        <div className="trading-layout-switcher" role="group" aria-label="Chart count and grid">
          <button type="button" onClick={() => removeChart()} disabled={charts.length <= MIN_TRADING_CHARTS} aria-label="Remove active chart">−</button>
          <TradingChartLayoutPicker
            chartCount={charts.length}
            maximumChartCount={MAX_TRADING_CHARTS}
            layout={layout}
            links={links}
            onSetChartCount={setChartCount}
            onSetLayout={setLayout}
            onSetLink={setLink}
          />
          <button type="button" onClick={addChart} disabled={charts.length >= MAX_TRADING_CHARTS} aria-label="Add chart">+</button>
          <select aria-label="Grid columns" value={gridOptions.some((option) => option.id === layout) ? layout : 'auto'} onChange={(event) => setLayout(event.target.value as TradingLayout)}>
            {gridOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
        </div>

        <div className="trading-header-actions">
          <span className={`workspace-${persistence.status}`}>Workspace {persistence.status}</span>
          {persistence.hasConflict ? (
            <>
              <button type="button" onClick={() => void persistence.resolveConflict('reload')}>Reload server</button>
              <button type="button" onClick={() => void persistence.resolveConflict('overwrite')}>Overwrite server</button>
            </>
          ) : null}
          <button type="button" aria-pressed={panels.right} onClick={() => setPanel('right', !panels.right)} disabled={!workspaceHydrated}>Right panel</button>
          <button type="button" aria-pressed={panels.bottom} onClick={() => setPanel('bottom', !panels.bottom)} disabled={!workspaceHydrated}>Bottom dock</button>
          <button type="button" onClick={exportWorkspace}>Export</button>
          <WorkspaceImportButton disabled={!workspaceHydrated} onImport={persistence.importWorkspace} />
          <TradingKeyboardLayer persistence={persistence} supportedIntervals={supportedIntervals} onOpenSymbolSearch={(typed) => openSymbolSearch(activeChartId, typed)} onCloseTab={closeTabSession} />
          <button type="button" onClick={() => setFocusMode((value) => !value)} aria-pressed={focusMode}>{focusMode ? 'Exit focus' : 'Focus'}</button>
      </div>
      <section className="trading-command-bar" aria-label="Trading command bar">
        <div className="trading-chart-controls" role="group" aria-label="Chart controls">
        <div role="group" className="trading-chart-symbol-options" aria-label="Chart symbol options">
        <button type="button" className="trading-symbol-trigger" aria-label="Open symbol search" onClick={() => openSymbolSearch()}>
          <span className="trading-symbol-trigger-icon" aria-hidden="true" />
          <span className="trading-symbol-trigger-copy">
            <strong>{activeSymbolLabel}</strong>
            <small>{activeInstrument?.venue ?? 'Symbol search'}</small>
          </span>
          <span className="trading-symbol-trigger-caret" aria-hidden="true">⌄</span>
        </button>
        {/* <div className="trading-symbol-search">
          <span aria-hidden="true">⌕</span>
          <input
            list="trading-instrument-options"
            aria-label="Search Trading symbol"
            placeholder="Search symbol…"
            value={symbolQuery}
            onChange={(event) => {
              const nextQuery = event.target.value;
              setSymbolQuery(nextQuery);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setSymbolSearchOpen(true);
            }}
          />
          <datalist id="trading-instrument-options">
            {(instruments.data ?? []).map((instrument) => <option key={instrument.instrument_id} value={instrument.display_symbol}>{instrument.venue}</option>)}
          </datalist>
        </div>
        */}
        <button
          type="button"
          className="trading-favorite"
          aria-label={favorite ? 'Remove active instrument from favorites' : 'Favorite active instrument'}
          aria-pressed={favorite}
          onClick={() => toggleFavoriteInstrument(activeChart.instrumentId)}
        >
          {favorite ? '★' : '☆'}
        </button>

        <select aria-label="Active Trading chart" value={activeChartId} onChange={(event) => setActiveChart(event.target.value)}>
          {charts.map((chart, index) => <option key={chart.chartId} value={chart.chartId}>Chart {index + 1}</option>)}
        </select>
        </div>

          <TradingIntervalMenu
            interval={activeChart.interval}
            supportedIntervals={supportedIntervals}
            feedName={selectedBinding?.provider}
            onSelect={(interval) => updateChart(activeChartId, { interval })}
          />

          <TradingChartTypeMenu value={activeChart.chartType} onChange={(chartType) => updateChart(activeChartId, { chartType })} />

          <TradingIndicatorManager indicators={activeChart.indicators} onToggle={(id) => toggleIndicator(activeChartId, id)} />
          <button
            type="button"
            className="trading-replay-trigger trading-topbar-replay"
            aria-label={replayMode ? 'Exit replay mode' : 'Replay'}
            aria-pressed={replayMode}
            title={replayMode ? 'Exit replay mode' : 'Replay chart from a selected bar'}
            onClick={() => setReplayMode(!replayMode)}
          >
            <span className="trading-replay-trigger-icon" aria-hidden="true">◀◀</span>
            <span>Replay</span>
          </button>
        </div>

        <select aria-label="Trading data feed" value={selectedBinding?.binding_id ?? ''} onChange={(event) => selectBinding(event.target.value)}>
          {availableBindings.map((binding) => <option key={binding.binding_id} value={binding.binding_id}>{binding.provider}{binding.is_official_api ? '' : ' · unofficial'}</option>)}
        </select>

        <div className="trading-tool-shortcuts" role="group" aria-label="Trading tools">
          <button type="button" aria-pressed={toolPanel === 'scanner'} onClick={() => toggleToolPanel('scanner')}>Scanner</button>
          <button type="button" aria-pressed={toolPanel === 'replay'} onClick={() => toggleToolPanel('replay')}>Backtest</button>
          <button type="button" aria-pressed={toolPanel === 'strategies'} onClick={() => toggleToolPanel('strategies')}>Strategies</button>
          <button type="button" aria-pressed={toolPanel === 'tester'} onClick={() => toggleToolPanel('tester')}>Strategy Tester</button>
          <button type="button" aria-pressed={toolPanel === 'seasonals'} onClick={() => toggleToolPanel('seasonals')}>Seasonals</button>
          <button type="button" aria-pressed={toolPanel === 'heatmap'} onClick={() => toggleToolPanel('heatmap')}>Heatmap</button>
          <button type="button" aria-pressed={toolPanel === 'financials'} onClick={() => toggleToolPanel('financials')}>Financials</button>
          <button type="button" aria-pressed={toolPanel === 'calendar'} onClick={() => toggleToolPanel('calendar')}>Calendar</button>
          <button type="button" aria-pressed={toolPanel === 'events'} onClick={() => toggleToolPanel('events')}>Earnings</button>
          <button type="button" aria-pressed={toolPanel === 'overview'} onClick={() => toggleToolPanel('overview')}>Advanced view</button>
          <button type="button" aria-pressed={toolPanel === 'script-screener'} onClick={() => toggleToolPanel('script-screener')}>Script screener</button>
          <button type="button" aria-pressed={toolPanel === 'yield-curve'} onClick={() => toggleToolPanel('yield-curve')}>Yield curve</button>
          <button type="button" aria-pressed={toolPanel === 'options'} onClick={() => toggleToolPanel('options')}>Options</button>
          <button type="button" aria-pressed={sidePanelTab === 'paper' && panels.right} onClick={openPaperTrading}>Trade</button>
          <button type="button" aria-pressed={sidePanelTab === 'research' && panels.right} onClick={openResearchPanel}>AI Research</button>
        </div>

        <span className="trading-provider-status" data-status={selectedBinding ? 'ready' : 'unavailable'} title={selectedBinding ? `${selectedBinding.provider} · ${selectedBinding.usage_scope} · ${selectedBinding.is_official_api ? 'official' : 'unofficial'}` : 'No compatible feed'}>
          <i aria-hidden="true" />{selectedBinding ? selectedBinding.provider : 'No feed'}
        </span>
      </section>
      </header>

      <TradingWindowRestore windows={windowTabs.goneWindows} tabs={tabs} tabLabel={sessionTabLabel} onReopen={windowTabs.reopen} onBringBack={windowTabs.bringBack} />
      {desktop ? null : sessionTabs}

      <TradingSymbolSearch
        open={symbolSearchOpen}
        query={symbolQuery}
        instruments={[...(instruments.data ?? []), ...symbolSearchResults]}
        activeInstrumentId={symbolSearchTargetInstrument?.instrument_id ?? symbolSearchTarget.instrumentId}
        loading={symbolSearchLoading}
        formulaPreview={formulaResolution}
        onQueryChange={setSymbolQuery}
        onSelect={(match) => applySymbolMatch(match, [match])}
        onSelectFormula={() => applyFormulaResolution(formulaResolution)}
        onClose={closeSymbolSearch}
        selectQueryOnOpen={!symbolSearchTyped}
      />
      <TradingAlertToastLayer />
      <TradingOrderToastLayer />
      <PaperOrderNotificationsWatch accountId={paperAccountId} />

      <div className="trading-body">
        <TradingDrawingTools selectedTool={drawingTool} onSelect={setDrawingTool} />
        <div className="trading-chart-column">
          <section className="trading-chart-shell" aria-label="Trading chart workspace" tabIndex={0}>
            <TradingChartGrid
              paperAccountId={paperAccountId}
              onOpenSymbolSearch={openSymbolSearch}
              onOpenPineScript={openPineEditor}
              onOpenMarketDataSettings={() => { void navigate({ to: '/settings', search: { category: 'trading-market-data' } }); }}
            />
          </section>
          {workspaceHydrated && panels.bottom && !toolPanel ? (
            <Suspense fallback={<p role="status">Loading trading terminal…</p>}>
            <TradingTerminalDock
              instrumentId={activeChart.instrumentId}
              bindingId={selectedBinding?.binding_id ?? activeChart.bindingId}
              preferredAccountId={paperAccountId}
              onAccountChange={setPaperAccountId}
              onSelectAlert={navigateToAlert}
            />
            </Suspense>
          ) : null}
          {toolPanel ? (
            <section className={`trading-tool-drawer${toolPanelFullscreen ? ' is-fullscreen' : ''}`} aria-label="Trading analysis tool">
              <header>
                <strong>{toolPanelTitle(toolPanel, activeChart.instrumentId)}</strong>
                <div className="trading-tool-drawer-actions">
                  <button type="button" onClick={() => setToolPanelFullscreen((value) => !value)} aria-pressed={toolPanelFullscreen} aria-label={toolPanelFullscreen ? 'Restore analysis tool' : 'Fullscreen analysis tool'}>{toolPanelFullscreen ? 'Restore' : 'Fullscreen'}</button>
                  <button type="button" onClick={() => { setToolPanelFullscreen(false); setToolPanel(null); }} aria-label="Close analysis tool">×</button>
                </div>
              </header>
              <div>
                <Suspense fallback={<p role="status">Loading analysis tool…</p>}>
                {toolPanel === 'scanner' ? <TradingScannerPanel instruments={instruments.data ?? []} onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'heatmap' ? <TradingHeatmap onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'financials' ? <TradingFinancials instrumentId={activeChart.instrumentId} /> : null}
                {toolPanel === 'calendar' ? <TradingEconomicCalendar onOpenSettings={() => { void navigate({ to: '/settings', search: { category: 'trading-market-data' } }); }} /> : null}
                {toolPanel === 'overview' ? <TradingAdvancedView key={advancedWatchlistId ?? 'charts'} chartInstrumentIds={charts.map((chart) => chart.instrumentId)} initialWatchlistId={advancedWatchlistId} onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'options' ? <TradingOptions instrumentId={activeChart.instrumentId} /> : null}
                {toolPanel === 'yield-curve' ? <TradingYieldCurve onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'script-screener' ? <TradingScriptScreener chartInstrumentIds={charts.map((chart) => chart.instrumentId)} onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'events' ? <TradingEventsCalendar chartInstrumentIds={charts.map((chart) => chart.instrumentId)} onShowInstrument={showInstrumentOnChart} /> : null}
                {toolPanel === 'replay' ? (
                  <TradingReplayPanel instrumentId={activeChart.instrumentId} bindingId={selectedBinding?.binding_id ?? activeChart.bindingId} interval={activeChart.interval} />
                ) : null}
                {toolPanel === 'strategies' ? <TradingStrategiesPanel /> : null}
                {toolPanel === 'seasonals' ? <TradingSeasonals instrumentId={activeChart.instrumentId} bindingId={selectedBinding?.binding_id ?? activeChart.bindingId} /> : null}
                {toolPanel === 'tester' ? <TradingStrategyTester indicators={activeChart.indicators} instrumentId={activeChart.instrumentId} bindingId={selectedBinding?.binding_id ?? activeChart.bindingId} interval={activeChart.interval} /> : null}
                </Suspense>
              </div>
            </section>
          ) : null}
        </div>
        {workspaceHydrated ? (
          <div className={`trading-right-dock ${panels.right ? 'is-expanded' : 'is-collapsed'}`}>
            {panels.right ? (
              <TradingSidePanel
                sessionId={activeTabId}
                instruments={visibleInstruments}
                activeInstrumentId={activeChart.instrumentId}
                bindingId={selectedBinding?.binding_id ?? activeChart.bindingId}
                providerBindings={providerBindings}
                interval={activeChart.interval}
                selectedTab={sidePanelTab}
                onTabChange={setSidePanelTab}
                paperAccountId={paperAccountId}
                onPaperAccountChange={setPaperAccountId}
                indicators={activeChart.indicators}
                pineIndicatorId={pineIndicatorId}
                onPineIndicatorChange={setPineIndicatorId}
                layout={layout}
                chartCount={charts.length}
                minimumChartCount={MIN_TRADING_CHARTS}
                maximumChartCount={MAX_TRADING_CHARTS}
                links={links}
                snapMode={drawingSnapMode}
                onSelectInstrument={(instrumentId) => {
                  const instrument = (instruments.data ?? []).find((item) => item.instrument_id === instrumentId);
                  const next = instrument ? preferredInstrument(instrument, instruments.data ?? []) : null;
                  updateChart(activeChartId, { instrumentId: next?.instrument_id ?? instrumentId, bindingId: null });
                }}
                onSelectAlert={navigateToAlert}
                onSetIndicators={(next) => setIndicators(activeChartId, next)}
                onOpenPineScript={openPineEditor}
                onSetLayout={setLayout}
                onSetChartCount={setChartCount}
                onAddChart={addChart}
                onRemoveChart={() => removeChart()}
                onSetLink={setLink}
                onSetSnapMode={(mode: DrawingSnapMode) => setDrawingSnapMode(mode)}
                onDuplicateLayout={workspaceHydrated ? duplicateWorkspace : undefined}
              />
            ) : null}
            <TradingSideRail
              activeTab={sidePanelTab}
              collapsed={!panels.right}
              onSelectTab={setSidePanelTab}
              onToggle={() => setPanel('right', !panels.right)}
            />
          </div>
        ) : null}
      </div>

    </main>
  );
}
