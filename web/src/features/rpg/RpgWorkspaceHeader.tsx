import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import type { OmnixModuleDefinition } from '../../app/modules';
import { rpgCampaignLoreClient } from './api/rpgCampaignLoreClient';
import { rpgWorldLibraryClient } from './api/rpgWorldLibraryClient';
import { RpgLorePanel } from './RpgLorePanel';
import { RpgStarterBubblePromotionPanel } from './RpgStarterBubblePromotionPanel';
import { RpgWorldAuthoringWorkspace } from './RpgWorldAuthoringWorkspace';
import { RpgWorldBundleTransfer } from './RpgWorldBundleTransfer';
import { pushWorldEditorRoute } from './RpgWorldCompletionModels';
import { RpgWorldEditorShell } from './RpgWorldEditorShell';
import { requestRpgLauncherHome, useRpgLauncherSlots } from './rpgLauncherSlots';
import type { RpgSessionSummaryPreview } from './rpgUiState';
import './RpgLoreOverlay.css';
import './RpgPlayFocus.css';
import './RpgWorldLibraryOverlay.css';

interface RpgWorkspaceHeaderProps {
  isLiveDataExpanded: boolean;
  isPlayerRailCollapsed: boolean;
  isWorldRailCollapsed: boolean;
  module: OmnixModuleDefinition;
  onToggleLiveData: () => void;
  onTogglePlayerRail: () => void;
  onToggleWorldRail: () => void;
  selectedSessionSummary: RpgSessionSummaryPreview;
  submitStatus: string;
}

const RPG_PLAY_FOCUS_CLASS = 'rpg-play-focus-mode';
const RPG_SELECTED_SESSION_STORAGE_KEY = 'omnix:rpg:selected-session-id';
const RPG_WORLD_LIBRARY_DIALOG_CLASS = 'rpg-launcher-dialog-world-library';

export function RpgWorkspaceHeader(props: RpgWorkspaceHeaderProps) {
  const {
    isLiveDataExpanded,
    isPlayerRailCollapsed,
    isWorldRailCollapsed,
    onToggleLiveData,
    onTogglePlayerRail,
    onToggleWorldRail,
    selectedSessionSummary,
  } = props;
  const [isHidden, setIsHidden] = useState(false);
  const [isLoreOpen, setIsLoreOpen] = useState(false);
  const [isWorldLibraryOpen, setIsWorldLibraryOpen] = useState(false);
  const [worldLibraryRequested, setWorldLibraryRequested] = useState(false);
  const { dialog: campaignLauncherDialog, homeGrid: campaignLauncherHomeGrid } = useRpgLauncherSlots();
  const worldLoreQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-lore', selectedSessionSummary.worldId],
    queryFn: () => rpgWorldLibraryClient.list(),
    enabled: isLoreOpen && Boolean(selectedSessionSummary.worldId),
    staleTime: 30_000,
  });
  const publishedWorld = worldLoreQuery.data?.worlds.find(
    (world) => world.id === selectedSessionSummary.worldId,
  );
  const campaignLoreQuery = useQuery({
    queryKey: ['feature', 'rpg', 'campaign-world-lore', selectedSessionSummary.id],
    queryFn: () => rpgCampaignLoreClient.read(selectedSessionSummary.id),
    enabled: isLoreOpen
      && Boolean(selectedSessionSummary.worldId)
      && Boolean(selectedSessionSummary.id),
    refetchInterval: isLoreOpen ? 5000 : false,
    staleTime: 2000,
  });
  const runtimeLoreCards = Object.values(
    campaignLoreQuery.data?.topic_cards ?? {},
  ).flat().filter((card) => card.metadata.lore_origin === 'gameplay');

  useEffect(() => {
    document.documentElement.classList.toggle(RPG_PLAY_FOCUS_CLASS, isHidden);
    return () => document.documentElement.classList.remove(RPG_PLAY_FOCUS_CLASS);
  }, [isHidden]);

  useEffect(() => {
    if (!isLoreOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsLoreOpen(false);
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [isLoreOpen]);

  useEffect(() => {
    if (!campaignLauncherDialog) setIsWorldLibraryOpen(false);
  }, [campaignLauncherDialog]);

  useEffect(() => {
    if (worldLibraryRequested && campaignLauncherDialog) {
      setIsWorldLibraryOpen(true);
      setWorldLibraryRequested(false);
    }
  }, [campaignLauncherDialog, worldLibraryRequested]);

  useEffect(() => {
    campaignLauncherDialog?.classList.toggle(RPG_WORLD_LIBRARY_DIALOG_CLASS, isWorldLibraryOpen);
    return () => campaignLauncherDialog?.classList.remove(RPG_WORLD_LIBRARY_DIALOG_CLASS);
  }, [campaignLauncherDialog, isWorldLibraryOpen]);

  const openWorldLibrary = () => {
    if (campaignLauncherDialog) {
      setIsWorldLibraryOpen(true);
      return;
    }
    setWorldLibraryRequested(true);
    requestRpgLauncherHome();
  };

  const selectLaunchedSession = (sessionId: string) => {
    try {
      window.localStorage.setItem(RPG_SELECTED_SESSION_STORAGE_KEY, sessionId);
    } catch {
      // Reload still refreshes the session list when storage is unavailable.
    }
    setIsWorldLibraryOpen(false);
    window.location.reload();
  };
  const closeWorldLore = () => {
    pushWorldEditorRoute(null, true);
    setIsLoreOpen(false);
  };

  return (
    <>
      <div className="rpg-workspace-header-content">
        <button
          className="rpg-secondary-button rpg-lore-entry-control"
          type="button"
          onClick={() => setIsLoreOpen(true)}
        >
          World Lore
        </button>
        <button
          className="rpg-secondary-button rpg-world-library-entry-control"
          type="button"
          onClick={openWorldLibrary}
        >
          Worlds &amp; Campaigns
        </button>
        <div role="group" className="rpg-unified-header-controls" aria-label="Workspace layout controls">
          <button className="rpg-secondary-button rpg-header-toggle" type="button" onClick={() => setIsHidden((value) => !value)}>
            {isHidden ? 'Show header' : 'Hide header'}
          </button>
          <button
            className="rpg-secondary-button"
            type="button"
            aria-pressed={isPlayerRailCollapsed}
            onClick={onTogglePlayerRail}
          >
            {isPlayerRailCollapsed ? 'Show player rail' : 'Hide player rail'}
          </button>
          <button
            className="rpg-secondary-button"
            type="button"
            aria-pressed={isWorldRailCollapsed}
            onClick={onToggleWorldRail}
          >
            {isWorldRailCollapsed ? 'Show world rail' : 'Hide world rail'}
          </button>
          <button
            className="rpg-secondary-button rpg-live-data-toggle"
            type="button"
            aria-controls="rpg-live-data-status-details"
            aria-expanded={isLiveDataExpanded}
            onClick={onToggleLiveData}
          >
            {isLiveDataExpanded ? 'Collapse live data' : 'Expand live data'}
          </button>
        </div>
      </div>

      {campaignLauncherHomeGrid ? createPortal(
        <button
          className="rpg-launcher-card"
          type="button"
          onClick={openWorldLibrary}
        >
          <strong>Worlds &amp; Campaigns</strong>
          <span>Create or import worlds, manage maps and images, and launch published scenarios.</span>
        </button>,
        campaignLauncherHomeGrid,
      ) : null}

      {campaignLauncherDialog && isWorldLibraryOpen ? createPortal(
        <div className="rpg-launcher-world-library-view" role="region" aria-label="Worlds and Campaigns view">
          <RpgWorldAuthoringWorkspace
            onBack={() => setIsWorldLibraryOpen(false)}
            onSessionLaunched={selectLaunchedSession}
          />
          <RpgWorldBundleTransfer />
          <RpgStarterBubblePromotionPanel />
        </div>,
        campaignLauncherDialog,
      ) : null}

      {isLoreOpen && typeof document !== 'undefined' ? createPortal(
        <div className="rpg-lore-overlay" role="dialog" aria-modal="true" aria-labelledby="rpg-lore-overlay-title">
          <div className={`rpg-lore-overlay-shell${publishedWorld ? ' is-world-catalog' : ''}`}>
            {publishedWorld ? (
              <>
                <h2 className="rpg-visually-hidden" id="rpg-lore-overlay-title">
                  {publishedWorld.title} world lore
                </h2>
                <RpgWorldEditorShell
                  backLabel="Back to Play"
                  onBack={closeWorldLore}
                  onPlay={closeWorldLore}
                  runtimeLoreCards={runtimeLoreCards}
                  world={publishedWorld}
                  worldId={publishedWorld.id}
                />
              </>
            ) : (
              <>
                <header className="rpg-lore-overlay-heading">
              <div>
                <p className="eyebrow">Campaign bible</p>
                <h2 id="rpg-lore-overlay-title">{selectedSessionSummary.title}</h2>
                <p>{selectedSessionSummary.location} · known world lore and discovered dossiers</p>
              </div>
              <button className="rpg-secondary-button" type="button" onClick={closeWorldLore}>
                Back to Play
              </button>
            </header>
                <div className="rpg-lore-overlay-content">
              {worldLoreQuery.isPending && selectedSessionSummary.worldId ? (
                <div className="rpg-world-lore-loading" role="status">
                  Loading published world lore…
                </div>
              ) : null}
              {worldLoreQuery.isError && selectedSessionSummary.worldId ? (
                <div className="rpg-world-catalog-error" role="alert">
                  Published world lore could not be loaded. Showing discovered Campaign Bible pages.
                </div>
              ) : null}
              <RpgLorePanel
                labelledById="rpg-lore-overlay-title"
                panelId="rpg-lore-overlay-panel"
                role="region"
              />
                </div>
              </>
            )}
          </div>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
