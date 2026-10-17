import { useEffect, useMemo, useRef, useState } from 'react';
import type { RpgLaunchResponse, RpgNewGameRequest } from './api/rpgSessionClient';
import {
  BASE_STAT,
  MAX_STAT,
  STAT_POOL,
  backgrounds,
  buildRpgNewGameRequest,
  buildTemplates,
  capabilityLabels,
  creationStages,
  initialStats,
  locations,
  openingHooks,
  openingPaces,
  powerSources,
  primaryCapabilities,
  relationshipPresets,
  statDefinitions,
  type BuildKey,
  type Capability,
  type CreationStageState,
} from './rpgCreateCampaignState';
import './RpgCreateCampaignWizard.css';

interface RpgCreateCampaignWizardProps {
  onCreateCampaign?: (
    request: RpgNewGameRequest,
    onProgress?: (response: RpgLaunchResponse) => void,
  ) => Promise<RpgLaunchResponse>;
  onEnterWorld?: () => void;
  publishedWorld?: {
    genre: string;
    location: string;
    scenarioDescription?: string;
    scenarioTitle: string;
    tone: string;
    worldTitle: string;
  };
}

interface BackendCreationStage {
  detail?: string;
  label?: string;
  progress?: number;
  status?: string;
}

interface BackendCreationProgress {
  current_stage_index?: number;
  error?: string;
  progress?: number;
  stage_label?: string;
  stages?: BackendCreationStage[];
  status?: string;
}

interface BackendCreationJob {
  error?: string;
  progress?: number;
  status?: string;
}

type LaunchResponseWithProgress = RpgLaunchResponse & {
  creation_job?: BackendCreationJob;
  creation_progress?: BackendCreationProgress;
};

const FALLBACK_PROGRESS_STEPS = [8, 18, 31, 44, 56, 68, 78, 88, 96, 100];
const PENDING_PROGRESS_STEPS = FALLBACK_PROGRESS_STEPS.slice(1, -2);
const MOTIVATION_OPTIONS = ['survival', 'knowledge', 'freedom', 'family', 'justice', 'renown'];
const PROFILE_CHALLENGE_OPTIONS = ['cautious', 'restless', 'proud', 'guarded', 'naive', 'impulsive'];

export function RpgCreateCampaignWizard({ onCreateCampaign, onEnterWorld, publishedWorld }: RpgCreateCampaignWizardProps) {
  const progressTimers = useRef<Array<ReturnType<typeof window.setTimeout>>>([]);
  const [isExpanded, setIsExpanded] = useState(true);
  const [characterName, setCharacterName] = useState('Elara');
  const [pronouns, setPronouns] = useState('she/her');
  const [background, setBackground] = useState('wanderer');
  const [buildKey, setBuildKey] = useState<BuildKey>('balanced');
  const [primaryCapability, setPrimaryCapability] = useState('recon');
  const [powerSource, setPowerSource] = useState('mundane');
  const [origin, setOrigin] = useState(publishedWorld ? '' : 'frontier_village');
  const [motivationPrimary, setMotivationPrimary] = useState('survival');
  const [motivationTarget, setMotivationTarget] = useState('');
  const [flaw, setFlaw] = useState('cautious');
  const [values, setValues] = useState('agency, loyalty');
  const [startingLocation, setStartingLocation] = useState('rusty-flagons');
  const [difficulty, setDifficulty] = useState('normal');
  const [worldActivity, setWorldActivity] = useState('standard');
  const [economyPressure, setEconomyPressure] = useState('normal');
  const [combatLethality, setCombatLethality] = useState('normal');
  const [openingHook, setOpeningHook] = useState('tavern-rumor');
  const [openingPace, setOpeningPace] = useState('balanced');
  const [relationshipPreset, setRelationshipPreset] = useState('unknown-outsider');
  const [seed, setSeed] = useState('482193');
  const [stats, setStats] = useState<Record<string, number>>(initialStats);
  const [capabilities, setCapabilities] = useState<Record<Capability, boolean>>({
    combat: true,
    influence: false,
    technical: false,
    survival: true,
    knowledge: false,
    support: false,
  });
  const [systems, setSystems] = useState({
    autosave: true,
    companions: true,
    permadeath: false,
    grounding: true,
    softAudit: true,
    narration: true,
    images: false,
    tts: true,
    stt: true,
  });
  const [progress, setProgress] = useState(0);
  const [isCreating, setIsCreating] = useState(false);
  const [isCreated, setIsCreated] = useState(false);
  const [creationError, setCreationError] = useState<string | null>(null);
  const [launchResponse, setLaunchResponse] = useState<RpgLaunchResponse | null>(null);

  const selectedBuild = buildTemplates.find((template) => template.key === buildKey) ?? buildTemplates[0];
  const spentPoints = Object.values(stats).reduce((total, value) => total + Math.max(0, value - BASE_STAT), 0);
  const remainingPoints = STAT_POOL - spentPoints;
  const activeCapabilities = (Object.entries(capabilities) as Array<[Capability, boolean]>)
    .filter(([, enabled]) => enabled)
    .map(([capability]) => capabilityLabels[capability]);
  const creationProgress = getCreationProgress(launchResponse);
  const progressPercent = clampProgress(progress);
  const activeStageIndex = normalizeStageIndex(creationProgress?.current_stage_index, progressPercent);
  const selectedBackground = backgrounds.find((option) => option.value === background) ?? backgrounds[0];
  const selectedLocation = locations.find((option) => option.value === startingLocation) ?? locations[0];
  const selectedOpeningHook = openingHooks.find((option) => option.value === openingHook) ?? openingHooks[0];
  const selectedOpeningPace = openingPaces.find((option) => option.value === openingPace) ?? openingPaces[1];
  const selectedPower = powerSources.find((option) => option.value === powerSource) ?? powerSources[0];
  const selectedPrimary = primaryCapabilities.find((option) => option.value === primaryCapability) ?? primaryCapabilities[0];
  const selectedRelationship = relationshipPresets.find((option) => option.value === relationshipPreset) ?? relationshipPresets[0];

  const derivedStats = useMemo(() => {
    const merged = { ...stats };
    Object.entries(selectedBuild.boosts).forEach(([key, boost]) => {
      merged[key] = Math.min(MAX_STAT + 2, (merged[key] ?? BASE_STAT) + boost);
    });
    return merged;
  }, [selectedBuild, stats]);

  const campaignRequest = useMemo(
    () =>
      buildRpgNewGameRequest({
        background,
        buildKey,
        capabilities,
        characterName,
        combatLethality,
        difficulty,
        economyPressure,
        flaw,
        motivationPrimary,
        motivationTarget,
        openingHook,
        openingPace,
        origin,
        powerSource,
        primaryCapability,
        pronouns,
        relationshipPreset,
        seed,
        startingLocation,
        stats,
        systems,
        values,
        worldActivity,
      }),
    [
      background,
      buildKey,
      capabilities,
      characterName,
      combatLethality,
      difficulty,
      economyPressure,
      flaw,
      motivationPrimary,
      motivationTarget,
      openingHook,
      openingPace,
      origin,
      powerSource,
      primaryCapability,
      pronouns,
      relationshipPreset,
      seed,
      startingLocation,
      stats,
      systems,
      values,
      worldActivity,
    ],
  );

  const canCreate = remainingPoints >= 0 && characterName.trim().length > 0 && activeCapabilities.length > 0;

  const clearProgressTimers = () => {
    progressTimers.current.forEach((timerId) => window.clearTimeout(timerId));
    progressTimers.current = [];
  };

  useEffect(() => clearProgressTimers, []);

  const scheduleProgress = (values: number[], markCreated = false) => {
    clearProgressTimers();
    values.forEach((value, index) => {
      const timerId = window.setTimeout(() => {
        setProgress((current) => Math.max(current, value));
        if (markCreated && value === 100) {
          setIsCreated(true);
        }
      }, 260 + index * 360);
      progressTimers.current.push(timerId);
    });
  };

  const adjustStat = (key: string, delta: number) => {
    setStats((current) => {
      const nextValue = Math.max(BASE_STAT, Math.min(MAX_STAT, (current[key] ?? BASE_STAT) + delta));
      const next = { ...current, [key]: nextValue };
      const nextSpent = Object.values(next).reduce((total, value) => total + Math.max(0, value - BASE_STAT), 0);
      return nextSpent > STAT_POOL ? current : next;
    });
  };

  const toggleCapability = (capability: Capability) => {
    setCapabilities((current) => ({ ...current, [capability]: !current[capability] }));
  };

  const toggleSystem = (key: keyof typeof systems) => {
    setSystems((current) => ({ ...current, [key]: !current[key] }));
  };

  const startCreation = async () => {
    if (!canCreate) {
      return;
    }
    setIsCreating(true);
    setIsCreated(false);
    setCreationError(null);
    setLaunchResponse(null);
    setProgress(FALLBACK_PROGRESS_STEPS[0]);

    if (!onCreateCampaign) {
      scheduleProgress(FALLBACK_PROGRESS_STEPS.slice(1), true);
      return;
    }

    scheduleProgress(PENDING_PROGRESS_STEPS);
    try {
      const result = await onCreateCampaign(campaignRequest, (nextResponse) => {
        const progressAwareResponse = nextResponse as LaunchResponseWithProgress;
        setLaunchResponse(progressAwareResponse);
        setProgress(getCreationProgressValue(progressAwareResponse, 0));
      });
      const progressAwareResult = result as LaunchResponseWithProgress;
      const backendStatus = getCreationStatus(progressAwareResult);
      const backendProgressValue = getCreationProgressValue(progressAwareResult, result.ok === false ? 68 : 100);
      const backendError = getCreationError(progressAwareResult);

      clearProgressTimers();
      setLaunchResponse(progressAwareResult);
      setProgress(backendProgressValue);
      if (result.ok === false || backendStatus === 'failed') {
        setCreationError(backendError || 'Campaign creation failed before a session was returned.');
        setIsCreated(false);
        return;
      }
      setIsCreated(true);
    } catch (error) {
      clearProgressTimers();
      setCreationError(error instanceof Error ? error.message : 'Campaign creation failed before a session was returned.');
      // Preserve the last stage the browser actually reached. A transport error
      // has no authoritative backend progress and must not be mislabeled as the
      // NPC/services stage by forcing the old 68% fallback.
      setProgress((current) => current);
      setIsCreated(false);
    }
  };

  const closeProgress = () => {
    clearProgressTimers();
    setIsCreating(false);
  };

  const enterWorld = () => {
    setIsCreating(false);
    setIsExpanded(false);
    onEnterWorld?.();
  };

  const stageState = (index: number): CreationStageState => {
    const backendState = creationProgress?.stages?.[index]?.status;
    if (backendState === 'done' || backendState === 'active' || backendState === 'pending') {
      return backendState;
    }
    if (backendState === 'failed') {
      return 'active';
    }
    if (progressPercent >= 100 || index < activeStageIndex) {
      return 'done';
    }
    return index === activeStageIndex ? 'active' : 'pending';
  };

  if (!isExpanded) {
    return (
      <section className="rpg-create-campaign-card rpg-create-campaign-card-collapsed" aria-label="Create Campaign">
        <div>
          <p className="eyebrow">Campaign setup</p>
          <h3>Create Campaign</h3>
          <p>Open the deeper RPG setup flow with point-buy stats, starter gear, story hooks, world rules, and creation progress.</p>
        </div>
        <button className="rpg-primary-button" type="button" onClick={() => setIsExpanded(true)}>
          New Campaign
        </button>
      </section>
    );
  }

  return (
    <section className="rpg-create-campaign-card" aria-label="Create Campaign">
      <header className="rpg-create-campaign-header">
        <div>
          <p className="eyebrow">New RPG campaign</p>
          <h3>Create Campaign</h3>
          <p>Build a deterministic starting state from supported RPG systems before entering the first turn.</p>
        </div>
        <div className="rpg-create-campaign-actions">
          <button className="rpg-secondary-button" type="button" onClick={() => setIsExpanded(false)}>
            Collapse setup
          </button>
          <button className="rpg-primary-button" type="button" onClick={startCreation} disabled={!canCreate}>
            Create Campaign
          </button>
        </div>
      </header>

      <div className="rpg-create-campaign-grid">
        <div className="rpg-create-section rpg-create-section-identity">
          <h4>Identity</h4>
          <div className="rpg-create-field-grid">
            <label>
              <span>Name</span>
              <input value={characterName} onChange={(event) => setCharacterName(event.target.value)} />
            </label>
            <label>
              <span>Pronouns</span>
              <input value={pronouns} onChange={(event) => setPronouns(event.target.value)} />
            </label>
            <label>
              <span>Origin</span>
              <input value={origin} onChange={(event) => setOrigin(event.target.value)} placeholder="frontier_village" />
              <small>Where the character came from; different from the starting scene.</small>
            </label>
            <OptionSelect label="Background" value={background} onChange={setBackground} options={backgrounds} detail={selectedBackground.detail} />
            <OptionSelect label="Power source" value={powerSource} onChange={setPowerSource} options={powerSources} detail={selectedPower.detail} />
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-profile">
          <h4>Profile hooks</h4>
          <div className="rpg-create-field-grid">
            <BasicSelect label="Primary motivation" value={motivationPrimary} onChange={setMotivationPrimary} options={MOTIVATION_OPTIONS} />
            <label>
              <span>Motivation target</span>
              <input value={motivationTarget} onChange={(event) => setMotivationTarget(event.target.value)} placeholder="family, guild, village..." />
            </label>
            <BasicSelect label="Complication / flaw" value={flaw} onChange={setFlaw} options={PROFILE_CHALLENGE_OPTIONS} />
            <label>
              <span>Values</span>
              <input value={values} onChange={(event) => setValues(event.target.value)} placeholder="agency, loyalty" />
            </label>
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-build">
          <h4>Build</h4>
          <div className="rpg-template-grid">
            {buildTemplates.map((template) => (
              <button key={template.key} className={template.key === buildKey ? 'rpg-template-card rpg-template-card-selected' : 'rpg-template-card'} type="button" onClick={() => setBuildKey(template.key)}>
                <strong>{template.label}</strong>
                <small>{template.detail}</small>
              </button>
            ))}
          </div>
          <div className="rpg-create-field-grid">
            <OptionSelect label="Primary focus" value={primaryCapability} onChange={setPrimaryCapability} options={primaryCapabilities} detail={selectedPrimary.detail} />
          </div>
          <div role="group" className="rpg-capability-grid" aria-label="Secondary capabilities">
            {(Object.keys(capabilities) as Capability[]).map((capability) => (
              <label key={capability} className="rpg-create-check-row">
                <input type="checkbox" checked={capabilities[capability]} onChange={() => toggleCapability(capability)} />
                <span>{capabilityLabels[capability]}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-stats">
          <h4>Point-buy stats</h4>
          <p>{remainingPoints} point{remainingPoints === 1 ? '' : 's'} left</p>
          <div className="rpg-stat-editor-grid">
            {statDefinitions.map((stat) => (
              <div className="rpg-stat-editor-row" key={stat.key}>
                <div>
                  <strong>{stat.label}</strong>
                  <small>{stat.detail}</small>
                </div>
                <div className="rpg-stat-stepper">
                  <button aria-label={`Decrease ${stat.label}`} type="button" onClick={() => adjustStat(stat.key, -1)}>-</button>
                  <span>{stats[stat.key]}</span>
                  <button aria-label={`Increase ${stat.label}`} type="button" onClick={() => adjustStat(stat.key, 1)}>+</button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-story">
          <h4>Opening story</h4>
          <div className="rpg-create-field-grid">
            {publishedWorld ? (
              <div>
                <strong>{publishedWorld.scenarioTitle}</strong>
                <small>{publishedWorld.scenarioDescription || `Published opening for ${publishedWorld.worldTitle}.`}</small>
              </div>
            ) : (
              <OptionSelect label="Opening hook" value={openingHook} onChange={setOpeningHook} options={openingHooks} detail={selectedOpeningHook.detail} />
            )}
            <OptionSelect label="Opening pace" value={openingPace} onChange={setOpeningPace} options={openingPaces} detail={selectedOpeningPace.detail} />
            <OptionSelect label="Relationship preset" value={relationshipPreset} onChange={setRelationshipPreset} options={relationshipPresets} detail={selectedRelationship.detail} />
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-world">
          <h4>World and rules</h4>
          <div className="rpg-create-field-grid">
            {publishedWorld ? (
              <div>
                <strong>Starting location</strong>
                <small>{publishedWorld.location}</small>
              </div>
            ) : (
              <OptionSelect label="Starting location" value={startingLocation} onChange={setStartingLocation} options={locations} detail={selectedLocation.detail} />
            )}
            <BasicSelect label="Difficulty" value={difficulty} onChange={setDifficulty} options={['story', 'normal', 'hard']} />
            <BasicSelect label="World activity" value={worldActivity} onChange={setWorldActivity} options={['quiet', 'standard', 'busy']} />
            <BasicSelect label="Economy pressure" value={economyPressure} onChange={setEconomyPressure} options={['low', 'normal', 'tight']} />
            <BasicSelect label="Combat lethality" value={combatLethality} onChange={setCombatLethality} options={['forgiving', 'normal', 'deadly']} />
            <label>
              <span>Seed</span>
              <input value={seed} onChange={(event) => setSeed(event.target.value)} placeholder="Random visible seed" />
            </label>
          </div>
        </div>

        <div className="rpg-create-section rpg-create-section-systems">
          <h4>Supported systems</h4>
          <div className="rpg-system-toggle-grid">
            {(
              [
                ['autosave', 'Autosave'],
                ['companions', 'Companions enabled'],
                ['permadeath', 'Permadeath'],
                ['grounding', 'Grounding validator'],
                ['softAudit', 'Background soft audit'],
                ['narration', 'LLM narration'],
                ['images', 'Image generation'],
                ['tts', 'TTS'],
                ['stt', 'STT'],
              ] as Array<[keyof typeof systems, string]>
            ).map(([key, label]) => (
              <label key={key} className="rpg-create-check-row rpg-create-system-row">
                <input type="checkbox" checked={systems[key]} onChange={() => toggleSystem(key)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </div>

        <aside className="rpg-create-summary" aria-label="Campaign setup summary">
          <div>
            <p className="eyebrow">Launch preview</p>
            <h4>{characterName || 'Unnamed'} · {selectedBuild.label}</h4>
            <p>{pronouns} · {selectedBackground.label} · {selectedPower.label}</p>
          </div>
          <dl>
            <SummaryRow label="Origin" value={origin || selectedBackground.label} />
            <SummaryRow label="Driver" value={`${motivationPrimary}${motivationTarget ? ` → ${motivationTarget}` : ''} · ${flaw}`} />
            <SummaryRow label="Values" value={values || 'agency'} />
            {publishedWorld ? <SummaryRow label="World" value={`${publishedWorld.worldTitle} · ${publishedWorld.genre} · ${publishedWorld.tone}`} /> : null}
            <SummaryRow label="Location" value={publishedWorld?.location ?? selectedLocation.label} />
            <SummaryRow label="Opening" value={publishedWorld ? `${publishedWorld.scenarioTitle} · ${selectedOpeningPace.label}` : `${selectedOpeningHook.label} · ${selectedOpeningPace.label}`} />
            <SummaryRow label="Relationship" value={selectedRelationship.label} />
            <SummaryRow label="Focus" value={`${selectedPrimary.label} + ${activeCapabilities.join(' + ') || 'None selected'}`} />
            <SummaryRow label="Rules" value={`${difficulty} · ${worldActivity} · ${economyPressure} economy · ${combatLethality} combat`} />
            <SummaryRow label="Seed" value={seed || 'Random visible seed'} />
          </dl>
          <div className="rpg-starter-kit">
            <strong>Starter gear</strong>
            <ul>
              {selectedBuild.starterGear.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div role="group" className="rpg-derived-stats" aria-label="Derived stat preview">
            {statDefinitions.slice(0, 6).map((stat) => (
              <span key={stat.key}>{stat.label}: {derivedStats[stat.key]}</span>
            ))}
          </div>
        </aside>
      </div>

      {!canCreate ? <p className="rpg-create-warning">Name, at least one capability, and a legal point-buy allocation are required.</p> : null}

      {isCreating ? (
        <ProgressModal
          activeCapabilities={activeCapabilities}
          activeStageIndex={activeStageIndex}
          characterName={characterName}
          closeProgress={closeProgress}
          creationError={creationError}
          creationProgress={creationProgress}
          enterWorld={enterWorld}
          isCreated={isCreated}
          progressPercent={progressPercent}
          seed={seed}
          selectedBackground={selectedBackground.label}
          selectedBuild={selectedBuild.label}
          selectedLocation={selectedLocation.label}
          selectedOpeningHook={selectedOpeningHook.label}
          selectedPrimary={selectedPrimary.label}
          sessionId={launchResponse?.session_id}
          stageState={stageState}
        />
      ) : null}
    </section>
  );
}

function getCreationProgress(response: RpgLaunchResponse | null): BackendCreationProgress | undefined {
  return (response as LaunchResponseWithProgress | null)?.creation_progress;
}

function getCreationStatus(response: LaunchResponseWithProgress): string | undefined {
  return response.creation_progress?.status ?? response.creation_job?.status ?? response.status;
}

function getCreationError(response: LaunchResponseWithProgress): string | undefined {
  return response.error ?? response.creation_progress?.error ?? response.creation_job?.error;
}

function getCreationProgressValue(response: LaunchResponseWithProgress, fallback: number): number {
  return clampProgress(response.creation_progress?.progress ?? response.creation_job?.progress ?? fallback);
}

function clampProgress(value: unknown): number {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return Math.max(0, Math.min(100, Math.round(numeric)));
}

function normalizeStageIndex(value: unknown, progressPercent: number): number {
  if (typeof value === 'number' && Number.isInteger(value)) {
    return Math.max(0, Math.min(creationStages.length - 1, value));
  }
  const nextStageIndex = FALLBACK_PROGRESS_STEPS.findIndex((stageProgress) => stageProgress > progressPercent);
  return nextStageIndex < 0 ? creationStages.length - 1 : Math.min(creationStages.length - 1, nextStageIndex);
}

interface OptionSelectProps {
  detail?: string;
  label: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  value: string;
}

function OptionSelect({ detail, label, onChange, options, value }: OptionSelectProps) {
  return (
    <label>
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {detail ? <small>{detail}</small> : null}
    </label>
  );
}

function BasicSelect({ label, onChange, options, value }: Omit<OptionSelectProps, 'detail' | 'options'> & { options: string[] }) {
  return (
    <label>
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option.replace('-', ' ')}
          </option>
        ))}
      </select>
    </label>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

interface ProgressModalProps {
  activeCapabilities: string[];
  activeStageIndex: number;
  characterName: string;
  closeProgress: () => void;
  creationError: string | null;
  creationProgress?: BackendCreationProgress;
  enterWorld: () => void;
  isCreated: boolean;
  progressPercent: number;
  seed: string;
  selectedBackground: string;
  selectedBuild: string;
  selectedLocation: string;
  selectedOpeningHook: string;
  selectedPrimary: string;
  sessionId?: string;
  stageState: (index: number) => CreationStageState;
}

function ProgressModal({
  activeCapabilities,
  activeStageIndex,
  characterName,
  closeProgress,
  creationError,
  creationProgress,
  enterWorld,
  isCreated,
  progressPercent,
  seed,
  selectedBackground,
  selectedBuild,
  selectedLocation,
  selectedOpeningHook,
  selectedPrimary,
  sessionId,
  stageState,
}: ProgressModalProps) {
  const title = creationError ? 'Campaign Creation Failed' : isCreated ? 'Campaign Ready' : 'Creating Campaign';
  const activeStage = creationProgress?.stages?.[activeStageIndex] ?? creationStages[activeStageIndex];
  const displayedStages = creationStages.map((stage, index) => ({
    detail: creationProgress?.stages?.[index]?.detail ?? stage.detail,
    label: creationProgress?.stages?.[index]?.label ?? stage.label,
    state: stageState(index),
  }));

  return (
    <div className="rpg-create-progress-overlay" role="dialog" aria-modal="true" aria-labelledby="rpg-create-progress-title">
      <div className="rpg-create-progress-modal">
        <header>
          <div>
            <p className="eyebrow">Campaign creation</p>
            <h3 id="rpg-create-progress-title">{title}</h3>
            <p>Building a deterministic world from your setup.</p>
          </div>
          <strong>{progressPercent}%</strong>
        </header>
        <div className="rpg-create-progress-track" aria-label="Campaign creation progress" aria-valuenow={progressPercent} aria-valuemin={0} aria-valuemax={100} role="progressbar">
          <span style={{ width: `${progressPercent}%` }} />
        </div>
        {creationError ? (
          <p className="rpg-create-warning">Failed at {activeStage.label}: {creationError}</p>
        ) : (
          <p className="rpg-create-current-stage">{activeStage.label}: {activeStage.detail}</p>
        )}
        <div className="rpg-create-stage-list">
          {displayedStages.map((stage, index) => (
            <div className={`rpg-create-stage-row rpg-create-stage-${stage.state}`} key={`${stage.label}-${index}`}>
              <span aria-hidden="true">{stage.state === 'done' ? '✓' : stage.state === 'active' ? '•' : '○'}</span>
              <div>
                <strong>{stage.label}</strong>
                <small>{stage.detail}</small>
              </div>
            </div>
          ))}
        </div>
        <div className="rpg-create-modal-summary">
          <span>{characterName}</span>
          <span>{selectedBackground}</span>
          <span>{selectedLocation}</span>
          <span>{selectedBuild}</span>
          <span>{selectedOpeningHook}</span>
          <span>{selectedPrimary}</span>
          <span>{activeCapabilities.join(' + ')}</span>
          <span>Seed {seed || 'random'}</span>
          {sessionId ? <span>Session {sessionId}</span> : null}
        </div>
        <p className="rpg-create-modal-note">Optional opening narration, TTS, STT, or image generation can continue after the campaign is ready.</p>
        <footer>
          <button className="rpg-secondary-button" type="button" onClick={closeProgress}>
            {creationError ? 'Back to setup' : 'Cancel'}
          </button>
          <button className="rpg-primary-button" type="button" disabled={!isCreated || Boolean(creationError)} onClick={enterWorld}>
            Enter World
          </button>
        </footer>
      </div>
    </div>
  );
}
