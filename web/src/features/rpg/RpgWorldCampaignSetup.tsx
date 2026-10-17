import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rpgSessionClient } from './api/rpgSessionClient';
import {
  rpgWorldLibraryClient,
  type RpgScenarioRevision,
  type RpgWorldRelease,
} from './api/rpgWorldLibraryClient';
import './RpgWorldCampaignSetup.css';

interface RpgWorldCampaignSetupProps {
  onBack: () => void;
  onEditWorld: () => void;
  onReviewGeneration?: () => void;
  onSessionLaunched: (sessionId: string) => void;
  worldId: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function humanize(value: string): string {
  const tail = value.split(':').pop() ?? value;
  return tail.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function latestScenarioRevision(revisions: RpgScenarioRevision[]): RpgScenarioRevision | undefined {
  return [...revisions].sort((left, right) => right.revision - left.revision)[0];
}

function matchingRelease(
  releases: RpgWorldRelease[],
  revision: RpgScenarioRevision | undefined,
): RpgWorldRelease | undefined {
  if (!revision) return undefined;
  const compatibleRelease = number(record(revision.document).compatible_release);
  return releases.find((release) => (
    release.world_revision === revision.world_revision
    && (!compatibleRelease || release.release === compatibleRelease)
  ));
}

function protagonistOptionLabel(option: Record<string, unknown>, index: number): string {
  return text(option.name)
    || text(option.title)
    || text(option.label)
    || `Protagonist option ${index + 1}`;
}

export function RpgWorldCampaignSetup({
  onBack,
  onEditWorld,
  onReviewGeneration = onEditWorld,
  onSessionLaunched,
  worldId,
}: RpgWorldCampaignSetupProps) {
  const queryClient = useQueryClient();
  const [scenarioId, setScenarioId] = useState('');
  const [protagonistOption, setProtagonistOption] = useState('custom');
  const [playerName, setPlayerName] = useState('Alyndra');
  const [pronouns, setPronouns] = useState('they/them');
  const [background, setBackground] = useState('World Traveler');
  const [build, setBuild] = useState('balanced_adventurer');
  const [difficulty, setDifficulty] = useState('normal');
  const [worldActivity, setWorldActivity] = useState('living_world');
  const [economyPressure, setEconomyPressure] = useState('normal');
  const [combatLethality, setCombatLethality] = useState('normal');
  const [companionsEnabled, setCompanionsEnabled] = useState(true);
  const [permadeath, setPermadeath] = useState(false);
  const [features, setFeatures] = useState({
    autosave: true,
    validator: true,
    background_soft_audit: true,
    llm_narration: true,
    image_generation: true,
    tts: false,
    stt: false,
  });
  const [feedback, setFeedback] = useState('');
  const autoPreparedRunId = useRef('');

  const libraryQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'campaign-setup'],
    queryFn: () => rpgWorldLibraryClient.list(),
  });
  const detailQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'campaign-setup', worldId],
    queryFn: () => rpgWorldLibraryClient.detail(worldId),
    enabled: Boolean(worldId),
    refetchInterval: (query) => ['planned', 'running'].includes(
      text(query.state.data?.generation_runs?.[0]?.status),
    ) ? 2_000 : false,
  });
  const world = detailQuery.data?.world
    ?? libraryQuery.data?.worlds.find((candidate) => candidate.id === worldId);
  const campaigns = useMemo(
    () => (libraryQuery.data?.campaigns ?? []).filter((campaign) => (
      campaign.world_id === worldId && campaign.status !== 'archived'
    )),
    [libraryQuery.data?.campaigns, worldId],
  );
  const scenarios = useMemo(
    () => (detailQuery.data?.scenarios ?? []).filter((scenario) => (
      scenario.status === 'published'
      && Boolean(detailQuery.data?.scenario_revisions[scenario.id]?.length)
    )),
    [detailQuery.data],
  );
  const availableOpeningCount = useMemo(() => (
    detailQuery.data?.topics
      .filter((topic) => topic.topic_id === 'opening_scenarios')
      .flatMap((topic) => Array.isArray(topic.content.entities) ? topic.content.entities : [])
      .length ?? 0
  ), [detailQuery.data]);
  const generationInProgress = ['planned', 'running'].includes(
    text(detailQuery.data?.generation_runs?.[0]?.status),
  );
  const generationRun = detailQuery.data?.generation_runs?.[0];
  const generationProgress = record(generationRun?.progress);
  const generationTargets = stringList(
    generationProgress.target_topic_ids ?? record(generationRun?.plan).topic_ids,
  );
  const completedTopics = stringList(generationProgress.accepted_topic_ids);
  const reviewTopics = stringList(generationProgress.flagged_topic_ids);
  const failedTopics = stringList(generationProgress.failed_topic_ids);
  const blockedTopics = stringList(generationProgress.blocked_topic_ids);
  const activeTopics = stringList(generationProgress.active_topic_ids);
  const explicitPercent = number(generationProgress.percent, -1);
  const generationPercent = text(generationRun?.status) === 'ready' ? 100 : Math.max(0, Math.min(100, explicitPercent >= 0
    ? explicitPercent
    : generationTargets.length ? Math.round((completedTopics.length / generationTargets.length) * 100) : 0));
  const importedWorld = world?.source_mode === 'imported';
  const reviewRequired = text(generationRun?.status) === 'review' && !importedWorld;
  const generationMessage = reviewRequired
    ? `${reviewTopics.length} topic${reviewTopics.length === 1 ? '' : 's'} need review before this world can be published.`
    : text(generationProgress.message)
    || (activeTopics.length ? `Generating ${activeTopics.join(', ')}` : 'Waiting for the next World Forge task');

  useEffect(() => {
    if (!scenarios.some((scenario) => scenario.id === scenarioId)) {
      setScenarioId(scenarios[0]?.id ?? '');
    }
  }, [scenarioId, scenarios]);

  const selectedScenario = scenarios.find((scenario) => scenario.id === scenarioId);
  const selectedRevision = latestScenarioRevision(
    detailQuery.data?.scenario_revisions[scenarioId] ?? [],
  );
  const selectedRelease = matchingRelease(detailQuery.data?.releases ?? [], selectedRevision);
  const certification = record(record(selectedRelease?.document).certification);
  const launchReady = Boolean(selectedScenario && selectedRevision && selectedRelease && certification.launch_ready);
  const protagonistOptions = (record(selectedRevision?.document).protagonist_options ?? []) as unknown[];
  const selectedOptionIndex = protagonistOption === 'custom' ? -1 : Number(protagonistOption);
  const selectedOption = selectedOptionIndex >= 0
    ? record(protagonistOptions[selectedOptionIndex])
    : {};

  const applyProtagonistOption = (value: string) => {
    setProtagonistOption(value);
    if (value === 'custom') return;
    const option = record(protagonistOptions[Number(value)]);
    const player = record(option.player ?? option.protagonist ?? option);
    setPlayerName(text(player.name, playerName));
    setPronouns(text(player.pronouns, pronouns));
    setBackground(text(player.background, text(option.background, background)));
    setBuild(text(player.build, build));
  };

  const continueCampaign = useMutation({
    mutationFn: (campaignId: string) => rpgSessionClient.continueRpgSession(campaignId),
    onSuccess: (result) => {
      if (!result.ok || !result.session_id) {
        throw new Error(result.error ?? 'Campaign could not be continued.');
      }
      onSessionLaunched(result.session_id);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Campaign could not be continued.'),
  });

  const prepareOpenings = useMutation({
    mutationFn: () => rpgWorldLibraryClient.prepareOpeningScenariosForLaunch(worldId),
    onSuccess: async (result) => {
      setFeedback(result.status === 'generating'
        ? 'World Forge is preparing this imported world for launch. This page will update when it is ready.'
        : result.status === 'review_required'
          ? 'World Forge has finished generation and needs review before the world can be published.'
        : `Prepared ${result.prepared.length} opening scenario${result.prepared.length === 1 ? '' : 's'} for launch.`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-library', 'campaign-setup'] }),
        queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-library', 'campaign-setup', worldId] }),
      ]);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Opening scenarios could not be prepared for launch.'),
  });

  useEffect(() => {
    const run = detailQuery.data?.generation_runs?.[0];
    const runId = text(run?.run_id);
    if (
      !runId
      || (text(run?.status) !== 'ready' && !importedWorld)
      || !availableOpeningCount
      || scenarios.length
      || prepareOpenings.isPending
      || autoPreparedRunId.current === runId
    ) return;
    autoPreparedRunId.current = runId;
    prepareOpenings.mutate();
  }, [availableOpeningCount, detailQuery.data?.generation_runs, importedWorld, prepareOpenings, scenarios.length]);

  const launch = useMutation({
    mutationFn: async () => {
      if (!world || !selectedScenario || !selectedRevision || !selectedRelease) {
        throw new Error('Select a published opening with a compatible world release.');
      }
      if (!certification.launch_ready) {
        throw new Error('The selected world release is not certified as launch ready.');
      }
      return rpgWorldLibraryClient.launchScenario(
        selectedScenario.id,
        selectedRevision.revision,
        {
          world_id: world.id,
          world_revision: selectedRevision.world_revision,
          world_release: selectedRelease.release,
          player: {
            ...selectedOption,
            name: playerName.trim() || 'Alyndra',
            pronouns: pronouns.trim() || 'they/them',
            background: background.trim() || 'World Traveler',
            build,
          },
          gameplay: {
            campaign_template: text(world.metadata.campaign_template, world.genre),
            genre: world.genre,
            tone: world.tone,
            difficulty,
            world_activity: worldActivity,
            economy_pressure: economyPressure,
            combat_lethality: combatLethality,
            companions_enabled: companionsEnabled,
            permadeath,
          },
          features,
        },
      );
    },
    onSuccess: (result) => {
      if (!result.ok || !result.session_id) {
        throw new Error(result.error ?? 'Campaign launch did not return a session.');
      }
      setFeedback(`Campaign launched: ${result.session_id}`);
      onSessionLaunched(result.session_id);
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Campaign could not be launched.'),
  });

  const toggleFeature = (name: keyof typeof features, checked: boolean) => {
    setFeatures((current) => ({ ...current, [name]: checked }));
  };

  return (
    <section className="rpg-authoring-campaign-setup" aria-label="World campaign setup">
      <header className="rpg-authoring-heading">
        <div>
          <p className="eyebrow">Campaign setup</p>
          <h2>{world?.title ?? 'World'}</h2>
          <p>Continue an existing campaign or configure a new protagonist and runtime.</p>
        </div>
        <button className="rpg-secondary-button" type="button" onClick={onBack}>Back to Worlds</button>
      </header>
      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
      {detailQuery.isPending || libraryQuery.isPending ? <p>Loading campaign options…</p> : null}
      {detailQuery.isError || libraryQuery.isError ? <p className="rpg-world-catalog-error">Unable to load campaign setup.</p> : null}

      <div className="rpg-campaign-setup-layout">
        <aside className="rpg-campaign-continue-panel">
          <h3>Continue Campaign</h3>
          {campaigns.map((campaign) => (
            <article key={campaign.campaign_id}>
              <div><strong>{campaign.title}</strong><p>{campaign.status} · world r{campaign.world_revision}</p></div>
              <button type="button" disabled={continueCampaign.isPending} onClick={() => continueCampaign.mutate(campaign.campaign_id)}>Continue</button>
            </article>
          ))}
          {!campaigns.length ? <p>No campaigns have started in this world.</p> : null}
        </aside>

        <main className="rpg-campaign-new-panel">
          <div className="rpg-campaign-setup-section-heading">
            <div><p className="eyebrow">New campaign</p><h3>Opening and protagonist</h3></div>
            <span>{launchReady ? 'Launch ready' : 'Setup incomplete'}</span>
          </div>
          {!scenarios.length ? (
            <div className="rpg-authoring-empty">
              <h3>No published opening</h3>
              {generationRun ? (
                <section className="rpg-campaign-generation-progress" aria-label="World Forge progress">
                  <div><strong>{reviewRequired ? 'World Forge generation complete' : `World Forge: ${generationPercent}%`}</strong><span>{text(generationRun.status, 'pending')}</span></div>
                  <progress value={generationPercent} max="100">{generationPercent}%</progress>
                  <p>{generationTargets.length ? `${completedTopics.length} accepted, ${reviewTopics.length} awaiting review, ${failedTopics.length + blockedTopics.length} failed or blocked. ` : ''}{generationMessage}</p>
                </section>
              ) : null}
              <p>{availableOpeningCount ? `${availableOpeningCount} authored opening scenario${availableOpeningCount === 1 ? ' is' : 's are'} available and ready to prepare for launch.` : 'Publish a launch-ready scenario before starting a campaign.'}</p>
              {reviewRequired ? <button type="button" onClick={onReviewGeneration}>Review World Forge Results</button> : availableOpeningCount ? <button type="button" disabled={prepareOpenings.isPending || generationInProgress} onClick={() => prepareOpenings.mutate()}>{prepareOpenings.isPending || generationInProgress ? 'Preparing world…' : `Prepare ${availableOpeningCount} Opening Scenario${availableOpeningCount === 1 ? '' : 's'}`}</button> : <button type="button" onClick={onEditWorld}>Review World Setup</button>}
            </div>
          ) : (
            <form onSubmit={(event) => { event.preventDefault(); launch.mutate(); }}>
              <div className="rpg-campaign-form-grid">
                <label><span>Published opening</span><select value={scenarioId} onChange={(event) => setScenarioId(event.currentTarget.value)}>{scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{scenario.title}</option>)}</select></label>
                <label><span>Character template</span><select value={protagonistOption} onChange={(event) => applyProtagonistOption(event.currentTarget.value)}><option value="custom">Custom protagonist</option>{protagonistOptions.map((option, index) => <option key={`${protagonistOptionLabel(record(option), index)}:${index}`} value={String(index)}>{protagonistOptionLabel(record(option), index)}</option>)}</select></label>
                <label><span>Name</span><input required value={playerName} onChange={(event) => setPlayerName(event.currentTarget.value)} /></label>
                <label><span>Pronouns</span><input value={pronouns} onChange={(event) => setPronouns(event.currentTarget.value)} /></label>
                <label><span>Background</span><input value={background} onChange={(event) => setBackground(event.currentTarget.value)} /></label>
                <label><span>Build</span><select value={build} onChange={(event) => setBuild(event.currentTarget.value)}><option value="balanced_adventurer">Balanced Adventurer</option><option value="warrior">Warrior</option><option value="ranger">Ranger</option><option value="silver_tongue">Silver Tongue</option></select></label>
              </div>

              <h4>Gameplay</h4>
              <div className="rpg-campaign-form-grid">
                <label><span>Difficulty</span><select value={difficulty} onChange={(event) => setDifficulty(event.currentTarget.value)}><option value="story">Story</option><option value="normal">Normal</option><option value="harsh">Harsh</option></select></label>
                <label><span>World activity</span><select value={worldActivity} onChange={(event) => setWorldActivity(event.currentTarget.value)}><option value="quiet">Quiet</option><option value="standard">Standard</option><option value="living_world">Living World</option></select></label>
                <label><span>Economy pressure</span><select value={economyPressure} onChange={(event) => setEconomyPressure(event.currentTarget.value)}><option value="relaxed">Relaxed</option><option value="normal">Normal</option><option value="strict">Strict</option></select></label>
                <label><span>Combat lethality</span><select value={combatLethality} onChange={(event) => setCombatLethality(event.currentTarget.value)}><option value="safe">Safe</option><option value="normal">Normal</option><option value="deadly">Deadly</option></select></label>
              </div>
              <div className="rpg-campaign-toggle-row">
                <label><input type="checkbox" checked={companionsEnabled} onChange={(event) => setCompanionsEnabled(event.currentTarget.checked)} /><span>Companions enabled</span></label>
                <label><input type="checkbox" checked={permadeath} onChange={(event) => setPermadeath(event.currentTarget.checked)} /><span>Permadeath</span></label>
              </div>

              <h4>Runtime features</h4>
              <div className="rpg-campaign-feature-grid">
                {(Object.keys(features) as Array<keyof typeof features>).map((feature) => (
                  <label key={feature}><input type="checkbox" checked={features[feature]} onChange={(event) => toggleFeature(feature, event.currentTarget.checked)} /><span>{humanize(feature)}</span></label>
                ))}
              </div>

              <section className="rpg-campaign-launch-review" aria-label="Campaign launch review">
                <h4>Launch review</h4>
                <dl>
                  <div><dt>World</dt><dd>{world?.title}</dd></div>
                  <div><dt>Opening</dt><dd>{selectedScenario?.title}</dd></div>
                  <div><dt>Release</dt><dd>{selectedRelease ? `r${selectedRevision?.world_revision} / ${selectedRelease.release}` : 'Unavailable'}</dd></div>
                  <div><dt>Protagonist</dt><dd>{playerName} · {humanize(build)}</dd></div>
                  <div><dt>Gameplay</dt><dd>{humanize(difficulty)} · {humanize(worldActivity)}</dd></div>
                  <div><dt>Certification</dt><dd>{launchReady ? 'Launch ready' : text((certification.missing_requirements as unknown[] | undefined)?.join(', '), 'Not ready')}</dd></div>
                </dl>
              </section>
              <button type="submit" disabled={!launchReady || launch.isPending}>{launch.isPending ? 'Launching…' : 'Launch Campaign'}</button>
            </form>
          )}
        </main>
      </div>
    </section>
  );
}
