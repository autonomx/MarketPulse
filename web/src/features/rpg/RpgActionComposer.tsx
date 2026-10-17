import { Button } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import type { ChangeEvent, FormEventHandler, ReactNode } from 'react';
import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import type { UseFormRegisterReturn } from 'react-hook-form';
import {
  rpgSessionClient,
  type RpgCapability,
  type RpgLaunchResponse,
  type RpgNewGameRequest,
  type RpgPowerSource,
} from './api/rpgSessionClient';
import { setRpgLauncherDialog, setRpgLauncherHomeGrid, useRpgLauncherView } from './rpgLauncherSlots';
import type { RpgQuickActionPreview, RpgSessionSummaryPreview } from './rpgUiState';
import './RpgSessionLauncher.css';

interface RpgActionComposerProps {
  campaignMenuHost?: HTMLElement | null;
  commandRegistration: UseFormRegisterReturn<'command'>;
  canSaveGame: boolean;
  hasCommandError: boolean;
  isPending: boolean;
  onQuickAction: (command: string) => void;
  onSaveGame: () => Promise<string>;
  onSubmit: FormEventHandler<HTMLFormElement>;
  quickActions: RpgQuickActionPreview[];
  renderNewCampaign: (closeLauncher: () => void) => ReactNode;
  selectedSessionId: string;
  sessionRegistration: UseFormRegisterReturn<'sessionId'>;
  sessionSummaries: RpgSessionSummaryPreview[];
}

type LauncherView = 'closed' | 'home' | 'campaign_wizard' | 'new_game' | 'load_game' | 'settings';
type RpgPlayerBuild = 'balanced_adventurer' | 'warrior' | 'ranger' | 'silver_tongue';
type RpgDifficulty = 'story' | 'normal' | 'harsh';
type RpgWorldActivity = 'quiet' | 'standard' | 'living_world';
type RpgEconomyPressure = 'relaxed' | 'normal' | 'strict';
type RpgCombatLethality = 'safe' | 'normal' | 'deadly';
type RpgGenre =
  | 'classic_fantasy'
  | 'dark_fantasy'
  | 'cyberpunk'
  | 'detective_noir'
  | 'political_intrigue'
  | 'post_apocalyptic'
  | 'space_opera'
  | 'modern_occult'
  | 'survival_horror'
  | 'sandbox';

const BUILD_OPTIONS: { value: RpgPlayerBuild; label: string; detail: string }[] = [
  { value: 'balanced_adventurer', label: 'Balanced Adventurer', detail: 'Even stats and flexible starter gear.' },
  { value: 'warrior', label: 'Warrior', detail: 'High strength and constitution.' },
  { value: 'ranger', label: 'Ranger', detail: 'High dexterity and wilderness awareness.' },
  { value: 'silver_tongue', label: 'Silver Tongue', detail: 'High charisma for social routes.' },
];

const CAMPAIGN_TEMPLATES = [
  { value: 'classic_fantasy', label: 'Classic Fantasy' },
  { value: 'tavern_mystery', label: 'Tavern Mystery' },
  { value: 'bandit_road', label: 'Bandit Road' },
  { value: 'wilderness_survival', label: 'Wilderness Survival' },
  { value: 'dungeon_delve', label: 'Dungeon Delve' },
  { value: 'sandbox', label: 'Sandbox' },
];

const GENRE_OPTIONS: { value: RpgGenre; label: string }[] = [
  { value: 'classic_fantasy', label: 'Classic Fantasy' },
  { value: 'dark_fantasy', label: 'Dark Fantasy' },
  { value: 'cyberpunk', label: 'Cyberpunk' },
  { value: 'detective_noir', label: 'Detective Noir' },
  { value: 'political_intrigue', label: 'Political Intrigue' },
  { value: 'post_apocalyptic', label: 'Post-Apocalyptic' },
  { value: 'space_opera', label: 'Space Opera' },
  { value: 'modern_occult', label: 'Modern Occult' },
  { value: 'survival_horror', label: 'Survival Horror' },
  { value: 'sandbox', label: 'Sandbox' },
];

const STARTING_LOCATIONS = [
  { value: 'rusty_flagon_tavern', label: 'Rusty Flagon Tavern' },
  { value: 'market_district', label: 'Market District' },
  { value: 'northern_road', label: 'Northern Road' },
  { value: 'glimmerdeep_pass', label: 'Glimmerdeep Pass' },
  { value: 'old_quarry', label: 'Old Quarry' },
];

const CAPABILITY_OPTIONS: { value: RpgCapability; label: string }[] = [
  { value: 'combat', label: 'Combat' },
  { value: 'recon', label: 'Recon' },
  { value: 'influence', label: 'Influence' },
  { value: 'technical', label: 'Technical' },
  { value: 'survival', label: 'Survival' },
  { value: 'knowledge', label: 'Knowledge' },
  { value: 'support', label: 'Support' },
  { value: 'custom', label: 'Custom' },
];

const POWER_SOURCE_OPTIONS: { value: RpgPowerSource; label: string }[] = [
  { value: 'mundane', label: 'Mundane' },
  { value: 'martial', label: 'Martial' },
  { value: 'magic', label: 'Magic' },
  { value: 'technology', label: 'Technology' },
  { value: 'psionic', label: 'Psionic' },
  { value: 'divine', label: 'Divine' },
  { value: 'occult', label: 'Occult' },
  { value: 'mutation', label: 'Mutation' },
  { value: 'mythic', label: 'Mythic' },
  { value: 'social_power', label: 'Social Power' },
  { value: 'scrap', label: 'Scrap' },
  { value: 'custom', label: 'Custom' },
];

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function launchedSessionId(response: RpgLaunchResponse): string | undefined {
  if (response.session_id) {
    return response.session_id;
  }

  const game = safeRecord(response.game);
  if (typeof game.session_id === 'string') {
    return game.session_id;
  }

  const session = safeRecord(response.session);
  const manifest = safeRecord(session.manifest);
  if (typeof manifest.session_id === 'string') {
    return manifest.session_id;
  }
  if (typeof manifest.id === 'string') {
    return manifest.id;
  }

  return undefined;
}

function parseSeed(seed: string): number | null {
  const trimmed = seed.trim();
  if (!trimmed) {
    return null;
  }

  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export function RpgActionComposer({
  campaignMenuHost,
  canSaveGame,
  commandRegistration,
  hasCommandError,
  isPending,
  onQuickAction,
  onSaveGame,
  onSubmit,
  quickActions,
  renderNewCampaign,
  selectedSessionId,
  sessionRegistration,
  sessionSummaries,
}: RpgActionComposerProps) {
  const queryClient = useQueryClient();
  const [launchStatus, setLaunchStatus] = useState<string>();
  const [launchError, setLaunchError] = useState<string>();
  const [isLaunching, setIsLaunching] = useState(false);
  const [launcherView, setLauncherView] = useRpgLauncherView<LauncherView>('closed', 'home'); // opens on the header's request
  const [campaignTemplate, setCampaignTemplate] = useState('classic_fantasy');
  const [genre, setGenre] = useState<RpgGenre>('classic_fantasy');
  const [tone, setTone] = useState('heroic adventure');
  const [playerName, setPlayerName] = useState('Alyndra');
  const [playerPronouns, setPlayerPronouns] = useState('she/her');
  const [playerBackground, setPlayerBackground] = useState('Wanderer');
  const [playerBuild, setPlayerBuild] = useState<RpgPlayerBuild>('balanced_adventurer');
  const [primaryCapability, setPrimaryCapability] = useState<RpgCapability>('recon');
  const [secondaryCapabilities, setSecondaryCapabilities] = useState<RpgCapability[]>(['survival', 'combat']);
  const [powerSource, setPowerSource] = useState<RpgPowerSource>('mundane');
  const [startingLocation, setStartingLocation] = useState('rusty_flagon_tavern');
  const [difficulty, setDifficulty] = useState<RpgDifficulty>('normal');
  const [worldActivity, setWorldActivity] = useState<RpgWorldActivity>('standard');
  const [economyPressure, setEconomyPressure] = useState<RpgEconomyPressure>('normal');
  const [combatLethality, setCombatLethality] = useState<RpgCombatLethality>('normal');
  const [seed, setSeed] = useState('');
  const [autosaveEnabled, setAutosaveEnabled] = useState(true);
  const [companionsEnabled, setCompanionsEnabled] = useState(true);
  const [permadeathEnabled, setPermadeathEnabled] = useState(false);
  const [validatorEnabled, setValidatorEnabled] = useState(true);
  const [backgroundSoftAuditEnabled, setBackgroundSoftAuditEnabled] = useState(true);
  const [llmNarrationEnabled, setLlmNarrationEnabled] = useState(true);
  const [imageGenerationEnabled, setImageGenerationEnabled] = useState(false);
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [sttEnabled, setSttEnabled] = useState(false);

  const liveSessionSummaries = useMemo(
    () => sessionSummaries.filter((session) => session.source === 'live').sort((a, b) => b.sortRank - a.sortRank),
    [sessionSummaries],
  );
  const mostRecentLiveSession = liveSessionSummaries[0];

  const refreshRpgWorkspace = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'replay-inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'reports'] }),
    ]);
  };

  const applySelectedSessionId = (sessionId: string) => {
    void sessionRegistration.onChange({
      target: { name: sessionRegistration.name, value: sessionId },
      type: 'change',
    });
  };

  const handleSessionSelectChange = (event: ChangeEvent<HTMLSelectElement>) => {
    void sessionRegistration.onChange(event);
  };

  const handlePrimaryCapabilityChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextCapability = event.currentTarget.value as RpgCapability;
    setPrimaryCapability(nextCapability);
    setSecondaryCapabilities((current) => current.filter((capability) => capability !== nextCapability));
  };

  const handleSecondaryCapabilityChange = (capability: RpgCapability, checked: boolean) => {
    setSecondaryCapabilities((current) => {
      const withoutCapability = current.filter((value) => value !== capability && value !== primaryCapability);
      if (!checked) {
        return withoutCapability;
      }
      return [...withoutCapability, capability].slice(0, 3);
    });
  };

  const closeLauncher = () => setLauncherView('closed');

  const openNewCampaign = () => {
    setLauncherView('campaign_wizard');
  };

  const saveGame = async () => {
    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus('Saving current campaignâ€¦');
    try {
      const checkpointId = await onSaveGame();
      setLaunchStatus(`Game saved: ${checkpointId}`);
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG save failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const finishLaunch = async (response: RpgLaunchResponse, fallbackSessionId: string | undefined, readyLabel: string) => {
    if (!response.ok) {
      throw new Error(response.error ?? 'RPG session launch failed.');
    }

    const sessionId = launchedSessionId(response) ?? fallbackSessionId;
    if (sessionId) {
      applySelectedSessionId(sessionId);
    }

    await refreshRpgWorkspace();
    closeLauncher();
    setLaunchStatus(
      sessionId
        ? `${readyLabel}: ${sessionId}. The next command will use this session.`
        : `${readyLabel}. Workspace refreshed, but no session id was returned.`,
    );
  };

  const buildNewGameRequest = (): RpgNewGameRequest => ({
    campaign_template: campaignTemplate,
    genre,
    tone: tone.trim() || 'heroic adventure',
    background: playerBackground.trim() || 'Wanderer',
    starting_location: startingLocation,
    player: {
      name: playerName.trim() || 'Alyndra',
      pronouns: playerPronouns.trim() || 'they/them',
      background: playerBackground.trim() || 'Wanderer',
      build: playerBuild,
    },
    primary_capability: primaryCapability,
    secondary_capabilities: secondaryCapabilities.filter((capability) => capability !== primaryCapability),
    power_source: powerSource,
    difficulty,
    world_activity: worldActivity,
    economy_pressure: economyPressure,
    combat_lethality: combatLethality,
    companions_enabled: companionsEnabled,
    permadeath: permadeathEnabled,
    seed: parseSeed(seed),
    features: {
      autosave: autosaveEnabled,
      validator: validatorEnabled,
      background_soft_audit: backgroundSoftAuditEnabled,
      llm_narration: llmNarrationEnabled,
      image_generation: imageGenerationEnabled,
      tts: ttsEnabled,
      stt: sttEnabled,
    },
  });

  const launchNewGame = async () => {
    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus('Creating configured Level 1 campaign…');
    try {
      const response = await rpgSessionClient.createRpgNewGame(buildNewGameRequest());
      await finishLaunch(response, undefined, 'New Game ready and selected');
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG new game launch failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const launchDemoSession = async () => {
    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus('Cloning demo session…');
    try {
      const response = await rpgSessionClient.startRpgPreset('demo_glimmerdeep_pass_lvl14');
      await finishLaunch(response, undefined, 'Demo Session ready and selected');
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG demo session launch failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const continueSession = async (sessionId: string) => {
    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus(`Loading session ${sessionId}…`);
    try {
      const response = await rpgSessionClient.continueRpgSession(sessionId);
      await finishLaunch(response, sessionId, 'Session loaded and selected');
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG session load failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const renameSession = async (session: RpgSessionSummaryPreview) => {
    const nextName = window.prompt('Rename RPG save', session.title);
    if (!nextName || nextName.trim() === session.title) {
      return;
    }

    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus(`Renaming session ${session.id}…`);
    try {
      const response = await rpgSessionClient.renameRpgSession(session.id, nextName.trim());
      if (!response.ok) {
        throw new Error(response.error ?? 'RPG session rename failed.');
      }
      await refreshRpgWorkspace();
      setLaunchStatus(`Save renamed: ${nextName.trim()}.`);
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG session rename failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const deleteSession = async (session: RpgSessionSummaryPreview) => {
    const confirmed = window.confirm(`Delete save "${session.title}"? The session will be archived and removed from the launcher list.`);
    if (!confirmed) {
      return;
    }

    setIsLaunching(true);
    setLaunchError(undefined);
    setLaunchStatus(`Deleting session ${session.id}…`);
    try {
      const response = await rpgSessionClient.deleteRpgSession(session.id);
      if (!response.ok) {
        throw new Error(response.error ?? 'RPG session delete failed.');
      }
      if (selectedSessionId === session.id) {
        applySelectedSessionId('');
      }
      await refreshRpgWorkspace();
      setLaunchStatus(`Save deleted: ${session.title}.`);
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'RPG session delete failed.');
    } finally {
      setIsLaunching(false);
    }
  };

  const selectedSessionHasOption = !selectedSessionId || sessionSummaries.some((session) => session.id === selectedSessionId);
  const launcherTitle =
    launcherView === 'campaign_wizard'
      ? 'New Campaign'
      : launcherView === 'new_game'
      ? 'New Game setup'
      : launcherView === 'load_game'
        ? 'Load Game'
        : launcherView === 'settings'
          ? 'RPG Settings'
          : 'Campaign Menu';
  const campaignMenuBar = (
    <section className="rpg-session-launcher" aria-label="RPG launcher">
      <Button variant="light" type="button" disabled={isLaunching || isPending} onClick={() => setLauncherView('home')}>
        Campaign Menu
      </Button>
      <span className="rpg-session-launcher-summary">
        {mostRecentLiveSession ? `Continue: ${mostRecentLiveSession.title}` : 'Start, load, or demo a campaign'}
      </span>
      {launchStatus ? <span className="rpg-session-launcher-status" aria-live="polite">{launchStatus}</span> : null}
      {launchError ? <span className="rpg-session-launcher-error" aria-live="assertive">{launchError}</span> : null}
    </section>
  );

  return (
    <>
      {campaignMenuHost ? createPortal(campaignMenuBar, campaignMenuHost) : campaignMenuBar}

      {launcherView !== 'closed' ? (
        <div className="rpg-launcher-modal" role="dialog" aria-modal="true" aria-label={launcherTitle}>
          <button className="rpg-launcher-backdrop" type="button" aria-label="Close RPG launcher" onClick={closeLauncher} />
          <section ref={setRpgLauncherDialog} className={launcherView === 'campaign_wizard' ? 'rpg-launcher-dialog rpg-launcher-dialog-wide' : 'rpg-launcher-dialog'}>
            <div className="rpg-launcher-panel-heading">
              <div>
                <p className="eyebrow">Campaign launcher</p>
                <h3>{launcherTitle}</h3>
              </div>
              <Button variant="subtle" type="button" disabled={isLaunching} onClick={closeLauncher}>
                Close
              </Button>
            </div>

            {launcherView === 'home' ? (
              <div ref={setRpgLauncherHomeGrid} className="rpg-launcher-home-grid">
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending || !mostRecentLiveSession} onClick={() => mostRecentLiveSession && void continueSession(mostRecentLiveSession.id)}>
                  <strong>Continue</strong>
                  <span>{mostRecentLiveSession ? mostRecentLiveSession.title : 'No saved run yet'}</span>
                </button>
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending} onClick={openNewCampaign}>
                  <strong>New Campaign</strong>
                  <span>Open the full deterministic campaign setup.</span>
                </button>
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending || !canSaveGame} onClick={() => void saveGame()}>
                  <strong>Save Game</strong>
                  <span>{canSaveGame ? 'Write a replay-preserving checkpoint for the current campaign.' : 'Select or create a campaign before saving.'}</span>
                </button>
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending} onClick={() => void launchDemoSession()}>
                  <strong>Demo Session</strong>
                  <span>Clone the polished Glimmerdeep Pass showcase.</span>
                </button>
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending || !liveSessionSummaries.length} onClick={() => setLauncherView('load_game')}>
                  <strong>Load Game</strong>
                  <span>Browse, rename, or delete saved sessions.</span>
                </button>
                <button className="rpg-launcher-card" type="button" disabled={isLaunching || isPending} onClick={() => setLauncherView('settings')}>
                  <strong>Settings</strong>
                  <span>Set defaults for New Campaign sessions.</span>
                </button>
              </div>
            ) : null}

            {launcherView === 'campaign_wizard' ? renderNewCampaign(closeLauncher) : null}

            {launcherView === 'new_game' ? (
              <div role="group" className="rpg-launcher-panel" aria-label="New Game setup">
                <div className="rpg-launcher-section-heading">
                  <div>
                    <p className="eyebrow">New Game setup</p>
                    <h4>Fresh deterministic campaign</h4>
                  </div>
                  <Button variant="subtle" type="button" disabled={isLaunching} onClick={() => setLauncherView('home')}>
                    Back
                  </Button>
                </div>

                <div className="rpg-launcher-form-grid">
                  <label>
                    <span>Campaign template</span>
                    <select value={campaignTemplate} onChange={(event) => setCampaignTemplate(event.currentTarget.value)}>
                      {CAMPAIGN_TEMPLATES.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Genre</span>
                    <select value={genre} onChange={(event) => setGenre(event.currentTarget.value as RpgGenre)}>
                      {GENRE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Tone</span>
                    <input value={tone} onChange={(event) => setTone(event.currentTarget.value)} />
                  </label>
                  <label>
                    <span>Character name</span>
                    <input value={playerName} onChange={(event) => setPlayerName(event.currentTarget.value)} />
                  </label>
                  <label>
                    <span>Pronouns</span>
                    <input value={playerPronouns} onChange={(event) => setPlayerPronouns(event.currentTarget.value)} />
                  </label>
                  <label>
                    <span>Background</span>
                    <input value={playerBackground} onChange={(event) => setPlayerBackground(event.currentTarget.value)} />
                  </label>
                  <label>
                    <span>Starting build</span>
                    <select value={playerBuild} onChange={(event) => setPlayerBuild(event.currentTarget.value as RpgPlayerBuild)}>
                      {BUILD_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Primary capability</span>
                    <select value={primaryCapability} onChange={handlePrimaryCapabilityChange}>
                      {CAPABILITY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Power source</span>
                    <select value={powerSource} onChange={(event) => setPowerSource(event.currentTarget.value as RpgPowerSource)}>
                      {POWER_SOURCE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <div className="rpg-launcher-choice-panel" role="group" aria-label="Secondary capabilities">
                    <span>Secondary capabilities</span>
                    <div className="rpg-launcher-choice-grid">
                      {CAPABILITY_OPTIONS.filter((option) => option.value !== primaryCapability).map((option) => (
                        <label key={option.value}>
                          <input
                            type="checkbox"
                            checked={secondaryCapabilities.includes(option.value)}
                            onChange={(event) => handleSecondaryCapabilityChange(option.value, event.currentTarget.checked)}
                          />
                          <span>{option.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <label>
                    <span>Starting location</span>
                    <select value={startingLocation} onChange={(event) => setStartingLocation(event.currentTarget.value)}>
                      {STARTING_LOCATIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Difficulty</span>
                    <select value={difficulty} onChange={(event) => setDifficulty(event.currentTarget.value as RpgDifficulty)}>
                      <option value="story">Story</option>
                      <option value="normal">Normal</option>
                      <option value="harsh">Harsh</option>
                    </select>
                  </label>
                  <label>
                    <span>World activity</span>
                    <select value={worldActivity} onChange={(event) => setWorldActivity(event.currentTarget.value as RpgWorldActivity)}>
                      <option value="quiet">Quiet</option>
                      <option value="standard">Standard</option>
                      <option value="living_world">Living World</option>
                    </select>
                  </label>
                  <label>
                    <span>Economy pressure</span>
                    <select value={economyPressure} onChange={(event) => setEconomyPressure(event.currentTarget.value as RpgEconomyPressure)}>
                      <option value="relaxed">Relaxed</option>
                      <option value="normal">Normal</option>
                      <option value="strict">Strict</option>
                    </select>
                  </label>
                  <label>
                    <span>Combat lethality</span>
                    <select value={combatLethality} onChange={(event) => setCombatLethality(event.currentTarget.value as RpgCombatLethality)}>
                      <option value="safe">Safe</option>
                      <option value="normal">Normal</option>
                      <option value="deadly">Deadly</option>
                    </select>
                  </label>
                  <label>
                    <span>Seed</span>
                    <input inputMode="numeric" placeholder="Random visible seed" value={seed} onChange={(event) => setSeed(event.currentTarget.value)} />
                  </label>
                </div>

                <div className="rpg-launcher-build-note">
                  {BUILD_OPTIONS.find((option) => option.value === playerBuild)?.detail}
                </div>

                <div role="group" className="rpg-launcher-toggle-grid" aria-label="New Game feature toggles">
                  <label><input type="checkbox" checked={autosaveEnabled} onChange={(event) => setAutosaveEnabled(event.currentTarget.checked)} /><span>Autosave</span></label>
                  <label><input type="checkbox" checked={companionsEnabled} onChange={(event) => setCompanionsEnabled(event.currentTarget.checked)} /><span>Companions enabled</span></label>
                  <label><input type="checkbox" checked={permadeathEnabled} onChange={(event) => setPermadeathEnabled(event.currentTarget.checked)} /><span>Permadeath</span></label>
                  <label><input type="checkbox" checked={validatorEnabled} onChange={(event) => setValidatorEnabled(event.currentTarget.checked)} /><span>Grounding validator</span></label>
                  <label><input type="checkbox" checked={backgroundSoftAuditEnabled} onChange={(event) => setBackgroundSoftAuditEnabled(event.currentTarget.checked)} /><span>Background soft audit</span></label>
                  <label><input type="checkbox" checked={llmNarrationEnabled} onChange={(event) => setLlmNarrationEnabled(event.currentTarget.checked)} /><span>LLM narration</span></label>
                  <label><input type="checkbox" checked={imageGenerationEnabled} onChange={(event) => setImageGenerationEnabled(event.currentTarget.checked)} /><span>Image generation</span></label>
                  <label><input type="checkbox" checked={ttsEnabled} onChange={(event) => setTtsEnabled(event.currentTarget.checked)} /><span>TTS</span></label>
                  <label><input type="checkbox" checked={sttEnabled} onChange={(event) => setSttEnabled(event.currentTarget.checked)} /><span>STT</span></label>
                </div>

                <div className="rpg-launcher-panel-actions">
                  <Button type="button" disabled={isLaunching || isPending} loading={isLaunching && launchStatus?.includes('Level 1')} onClick={() => void launchNewGame()}>
                    Start New Game
                  </Button>
                </div>
              </div>
            ) : null}

            {launcherView === 'settings' ? (
              <div role="group" className="rpg-launcher-panel" aria-label="RPG launcher settings">
                <div className="rpg-launcher-section-heading">
                  <div>
                    <p className="eyebrow">RPG settings</p>
                    <h4>New Campaign defaults</h4>
                  </div>
                  <Button variant="subtle" type="button" disabled={isLaunching} onClick={() => setLauncherView('home')}>
                    Back
                  </Button>
                </div>
                <p className="rpg-settings-note">
                  These defaults are copied into the next New Campaign setup. Demo Session keeps its curated showcase defaults.
                </p>
                <div role="group" className="rpg-launcher-toggle-grid" aria-label="RPG feature toggles">
                  <label><input type="checkbox" checked={autosaveEnabled} onChange={(event) => setAutosaveEnabled(event.currentTarget.checked)} /><span>Autosave</span></label>
                  <label><input type="checkbox" checked={validatorEnabled} onChange={(event) => setValidatorEnabled(event.currentTarget.checked)} /><span>Grounding validator</span></label>
                  <label><input type="checkbox" checked={backgroundSoftAuditEnabled} onChange={(event) => setBackgroundSoftAuditEnabled(event.currentTarget.checked)} /><span>Background soft audit</span></label>
                  <label><input type="checkbox" checked={llmNarrationEnabled} onChange={(event) => setLlmNarrationEnabled(event.currentTarget.checked)} /><span>LLM narration</span></label>
                  <label><input type="checkbox" checked={imageGenerationEnabled} onChange={(event) => setImageGenerationEnabled(event.currentTarget.checked)} /><span>Image generation</span></label>
                  <label><input type="checkbox" checked={ttsEnabled} onChange={(event) => setTtsEnabled(event.currentTarget.checked)} /><span>TTS</span></label>
                  <label><input type="checkbox" checked={sttEnabled} onChange={(event) => setSttEnabled(event.currentTarget.checked)} /><span>STT</span></label>
                  <label><input type="checkbox" checked={companionsEnabled} onChange={(event) => setCompanionsEnabled(event.currentTarget.checked)} /><span>Companions enabled</span></label>
                  <label><input type="checkbox" checked={permadeathEnabled} onChange={(event) => setPermadeathEnabled(event.currentTarget.checked)} /><span>Permadeath</span></label>
                </div>
              </div>
            ) : null}

            {launcherView === 'load_game' ? (
              <div role="group" className="rpg-launcher-panel" aria-label="Load Game browser">
                <div className="rpg-launcher-section-heading">
                  <div>
                    <p className="eyebrow">Load Game</p>
                    <h4>Saved sessions</h4>
                  </div>
                  <Button variant="subtle" type="button" disabled={isLaunching} onClick={() => setLauncherView('home')}>
                    Back
                  </Button>
                </div>
                <div className="rpg-load-game-list">
                  {liveSessionSummaries.map((session) => (
                    <div key={session.id} className="rpg-load-game-card">
                      <button className="rpg-load-game-main" type="button" disabled={isLaunching || isPending} onClick={() => void continueSession(session.id)}>
                        <strong>{session.title}</strong>
                        <span>{session.location} • {session.turnLabel} • {session.updatedAt}</span>
                        <small>{session.id}</small>
                      </button>
                      <div className="rpg-load-game-actions">
                        <Button size="xs" variant="subtle" type="button" disabled={isLaunching || isPending} onClick={() => void renameSession(session)}>
                          Rename
                        </Button>
                        <Button size="xs" variant="outline" type="button" disabled={isLaunching || isPending} onClick={() => void deleteSession(session)}>
                          Delete
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      ) : null}

      <form className="rpg-action-composer" aria-labelledby="rpg-turn-request-title" onSubmit={onSubmit}>
        <div className="rpg-action-composer-heading">
          <h3 id="rpg-turn-request-title">Turn request</h3>
          <p>Queue replay-preserving RPG commands into the deterministic turn pipeline.</p>
        </div>
        <label className="rpg-session-field" title="Session">
          <span className="rpg-field-label">Session</span>
          <select {...sessionRegistration} value={selectedSessionId} onChange={handleSessionSelectChange}>
            <option value="">New or current session</option>
            {!selectedSessionHasOption ? <option value={selectedSessionId}>Launching session — {selectedSessionId}</option> : null}
            {sessionSummaries.map((session) => (
              <option key={session.id} value={session.id}>
                {session.title === session.id ? session.id : `${session.title} — ${session.id}`}
              </option>
            ))}
          </select>
        </label>
        <label className="rpg-command-field">
          <span className="rpg-field-label">Command</span>
          <textarea rows={1} aria-invalid={hasCommandError} placeholder="What do you want to do?" {...commandRegistration} />
        </label>
        <Button
          aria-label={isPending ? 'Queueing RPG turn' : 'Queue RPG turn'}
          className="rpg-submit-button"
          type="submit"
          disabled={isPending || isLaunching}
          loading={isPending}
        >
          {isPending ? 'Queueing...' : 'Submit'}
        </Button>
      </form>
      <div role="group" className="rpg-quick-actions" aria-label="Quick RPG actions">
        {quickActions.map((action) => (
          <button key={`${action.label}:${action.command}`} type="button" onClick={() => onQuickAction(action.command)}>
            <span className="rpg-quick-action-icon" aria-hidden="true">{action.icon}</span>
            <span className="rpg-quick-action-label">{action.label}</span>
          </button>
        ))}
      </div>
    </>
  );
}
