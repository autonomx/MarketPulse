/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { rpgSessionClient, type RpgLaunchResponse, type RpgNewGameRequest } from './api/rpgSessionClient';
import {
  rpgWorldLibraryClient,
  type RpgScenarioRevision,
  type RpgScenarioSummary,
  type RpgWorldRelease,
  type RpgWorldSummary,
} from './api/rpgWorldLibraryClient';
import { loadSettingsProfile } from '../settings';
import { RpgCreateCampaignWizard as LegacyRpgCreateCampaignWizard } from './RpgCreateCampaignWizardLegacy';
import { RpgWorldCampaignCatalog } from './RpgWorldCampaignCatalog';
import { applyRpgWizardDefaults, rpgWizardDefaultsFromSettings } from './rpgWizardDefaults';

interface RpgCreateCampaignWizardProps {
  onCreateCampaign?: (request: RpgNewGameRequest) => Promise<RpgLaunchResponse>;
  onEnterWorld?: () => void;
}

type CampaignWizardView = 'catalog' | 'setup';

const RPG_SELECTED_SESSION_STORAGE_KEY = 'omnix:rpg:selected-session-id';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function number(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function humanizeId(value: string): string {
  const tail = value.split(':').pop() ?? value;
  return tail.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function scenarioLocationLabel(
  detail: Awaited<ReturnType<typeof rpgWorldLibraryClient.detail>> | undefined,
  scenario: RpgScenarioSummary | undefined,
  revision: RpgScenarioRevision | undefined,
): string {
  const locationId = text(record(revision?.document).starting_location_id)
    || text(record(scenario?.metadata).starting_location);
  if (!locationId) return 'Published scenario location';

  for (const topic of detail?.topics ?? []) {
    const content = record(topic.content);
    for (const value of [...array(content.entities), ...array(content.locations)]) {
      const row = record(value);
      const candidateId = text(row.location_id) || text(row.id) || text(row.entity_id);
      if (candidateId === locationId) {
        const label = text(row.name) || text(row.title);
        if (label) return label;
      }
    }
  }
  return humanizeId(locationId);
}

function latestScenarioRevision(revisions: RpgScenarioRevision[]): RpgScenarioRevision | undefined {
  return [...revisions].sort((left, right) => right.revision - left.revision)[0];
}

function matchingRelease(
  releases: RpgWorldRelease[],
  scenarioRevision: RpgScenarioRevision | undefined,
): RpgWorldRelease | undefined {
  if (!scenarioRevision) return undefined;
  const compatibleRelease = number(record(scenarioRevision.document).compatible_release);
  return releases.find((release) => (
    release.world_revision === scenarioRevision.world_revision
    && (!compatibleRelease || release.release === compatibleRelease)
  ));
}

function releaseIsLaunchReady(release: RpgWorldRelease | undefined): boolean {
  return Boolean(record(record(release?.document).certification).launch_ready);
}

function scenarioLabel(scenario: RpgScenarioSummary): string {
  return `${scenario.title}${scenario.description ? ` · ${scenario.description}` : ''}`;
}

function storeSelectedSession(sessionId: string): void {
  try {
    window.localStorage.setItem(RPG_SELECTED_SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Reload still refreshes the session inventory when storage is unavailable.
  }
}

export function RpgCreateCampaignWizard(props: RpgCreateCampaignWizardProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const touchedRef = useRef(false);
  const applyingRef = useRef(false);
  const [view, setView] = useState<CampaignWizardView>('catalog');
  const [selectedWorldId, setSelectedWorldId] = useState('');
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [launchedSessionId, setLaunchedSessionId] = useState('');
  const [catalogError, setCatalogError] = useState<string>();
  const [isContinuing, setIsContinuing] = useState(false);

  const libraryQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'campaign-wizard'],
    queryFn: () => rpgWorldLibraryClient.list(),
  });
  const availableWorlds = useMemo(
    () => (libraryQuery.data?.worlds ?? []).filter((world) => world.status !== 'archived'),
    [libraryQuery.data?.worlds],
  );
  const selectedWorld = availableWorlds.find((world) => world.id === selectedWorldId);
  const detailQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-library', 'campaign-wizard', selectedWorldId],
    queryFn: () => rpgWorldLibraryClient.detail(selectedWorldId),
    enabled: view === 'setup' && Boolean(selectedWorldId),
  });

  const publishedScenarios = useMemo(
    () => (detailQuery.data?.scenarios ?? []).filter((scenario) => (
      scenario.status === 'published'
      && Boolean(detailQuery.data?.scenario_revisions[scenario.id]?.length)
    )),
    [detailQuery.data],
  );

  useEffect(() => {
    if (!publishedScenarios.some((scenario) => scenario.id === selectedScenarioId)) {
      setSelectedScenarioId(publishedScenarios[0]?.id ?? '');
    }
  }, [publishedScenarios, selectedScenarioId]);

  useEffect(() => {
    if (view !== 'setup') return undefined;
    let active = true;
    loadSettingsProfile()
      .then(({ profile }) => {
        if (!active || touchedRef.current || !rootRef.current) return;
        applyingRef.current = true;
        try {
          applyRpgWizardDefaults(rootRef.current, rpgWizardDefaultsFromSettings(profile));
        } finally {
          applyingRef.current = false;
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [view]);

  const selectedScenario = publishedScenarios.find((scenario) => scenario.id === selectedScenarioId);
  const selectedScenarioRevision = latestScenarioRevision(
    detailQuery.data?.scenario_revisions[selectedScenarioId] ?? [],
  );
  const selectedRelease = matchingRelease(detailQuery.data?.releases ?? [], selectedScenarioRevision);
  const selectedLocationLabel = scenarioLocationLabel(
    detailQuery.data,
    selectedScenario,
    selectedScenarioRevision,
  );
  const launchReady = Boolean(
    selectedWorld
    && selectedScenario
    && selectedScenarioRevision
    && selectedRelease
    && releaseIsLaunchReady(selectedRelease),
  );

  const markTouched = () => {
    if (!applyingRef.current) touchedRef.current = true;
  };

  const openCampaignSetup = (worldId: string) => {
    touchedRef.current = false;
    setSelectedWorldId(worldId);
    setSelectedScenarioId('');
    setCatalogError(undefined);
    setView('setup');
  };

  const continueCampaign = async (campaignId: string) => {
    setIsContinuing(true);
    setCatalogError(undefined);
    try {
      const result = await rpgSessionClient.continueRpgSession(campaignId);
      if (!result.ok) throw new Error(result.error ?? 'The selected campaign could not be continued.');
      const sessionId = result.session_id ?? campaignId;
      storeSelectedSession(sessionId);
      props.onEnterWorld?.();
      window.location.reload();
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : 'The selected campaign could not be continued.');
    } finally {
      setIsContinuing(false);
    }
  };

  const launchExistingCampaign = async (request: RpgNewGameRequest): Promise<RpgLaunchResponse> => {
    if (!selectedWorld || !selectedScenario || !selectedScenarioRevision || !selectedRelease) {
      throw new Error('Select a published opening for this world before starting a campaign.');
    }
    if (!releaseIsLaunchReady(selectedRelease)) {
      throw new Error('The selected world release is not certified as launch ready.');
    }

    const result = await rpgWorldLibraryClient.launchScenario(
      selectedScenario.id,
      selectedScenarioRevision.revision,
      {
        world_id: selectedWorld.id,
        world_revision: selectedScenarioRevision.world_revision,
        world_release: selectedRelease.release,
        player: request.player ?? {},
        gameplay: {
          campaign_template: text(selectedWorld.metadata.campaign_template) || request.campaign_template,
          genre: selectedWorld.genre,
          tone: selectedWorld.tone,
          background: request.background,
          primary_capability: request.primary_capability,
          secondary_capabilities: request.secondary_capabilities,
          power_source: request.power_source,
          generated_class_name: request.generated_class_name,
          generated_class_summary: request.generated_class_summary,
          difficulty: request.difficulty,
          world_activity: request.world_activity,
          economy_pressure: request.economy_pressure,
          combat_lethality: request.combat_lethality,
          companions_enabled: request.companions_enabled,
          permadeath: request.permadeath,
          seed: request.seed,
          initial_stats: request.initial_stats,
          genesis: request.genesis,
        },
        features: request.features ?? {},
      },
    );

    if (!result.ok || !result.session_id) {
      throw new Error(result.error ?? 'The selected published scenario did not return a campaign session.');
    }

    setLaunchedSessionId(result.session_id);
    storeSelectedSession(result.session_id);
    return {
      ok: true,
      session_id: result.session_id,
      status: result.status ?? 'ready',
      session: result.session,
      game: result.game,
    };
  };

  const enterExistingWorld = () => {
    props.onEnterWorld?.();
    if (launchedSessionId) window.location.reload();
  };

  const sourceStatus = detailQuery.isPending
    ? 'Loading published scenarios and immutable releases…'
    : !publishedScenarios.length
      ? 'This world has no published scenario. Publish an opening before starting a campaign.'
      : launchReady
        ? `Ready: ${selectedWorld?.title} · ${selectedScenario?.title}`
        : 'The selected scenario does not have a launch-ready certified release.';

  if (view === 'catalog') {
    return (
      <div ref={rootRef} style={{ display: 'contents' }}>
        <RpgWorldCampaignCatalog
          campaigns={libraryQuery.data?.campaigns ?? []}
          error={catalogError ?? (libraryQuery.isError ? 'Unable to load reusable worlds.' : undefined)}
          isLoading={libraryQuery.isPending || isContinuing}
          onBack={props.onEnterWorld ?? (() => undefined)}
          onContinueCampaign={(campaignId) => void continueCampaign(campaignId)}
          onNewCampaign={openCampaignSetup}
          scenarios={libraryQuery.data?.scenarios ?? []}
          worlds={availableWorlds}
        />
      </div>
    );
  }

  return (
    <div ref={rootRef} style={{ display: 'contents' }} onChangeCapture={markTouched} onInputCapture={markTouched}>
      <section
        aria-label="Selected campaign world"
        style={{
          border: '1px solid var(--border-subtle, color-mix(in srgb, var(--c-neutral-1000) 12%, transparent))',
          borderRadius: 14,
          padding: 14,
          marginBottom: 14,
          background: 'var(--surface-elevated, color-mix(in srgb, var(--c-neutral-1000) 3.5%, transparent))',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>Campaign world</div>
            <div style={{ opacity: 0.72, fontSize: 13 }}>
              {selectedWorld?.title ?? 'Selected world'} · {selectedWorld?.genre.replace(/[_-]+/g, ' ') ?? 'reusable world'}
            </div>
          </div>
          <button className="rpg-secondary-button" type="button" onClick={() => setView('catalog')}>
            Change world
          </button>
        </div>
        <label style={{ display: 'grid', gap: 5, marginTop: 12 }}>
          <span>Published scenario</span>
          <select
            aria-label="Published scenario"
            value={selectedScenarioId}
            disabled={detailQuery.isPending || !publishedScenarios.length}
            onChange={(event) => setSelectedScenarioId(event.currentTarget.value)}
          >
            {!publishedScenarios.length ? <option value="">No published openings available</option> : null}
            {publishedScenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>{scenarioLabel(scenario)}</option>
            ))}
          </select>
        </label>
        <p style={{ margin: '10px 0 0', fontSize: 13, opacity: launchReady ? 0.9 : 0.72 }} aria-live="polite">
          {sourceStatus}
        </p>
      </section>
      {launchReady && selectedWorld && selectedScenario ? (
        <LegacyRpgCreateCampaignWizard
          {...props}
          onCreateCampaign={launchExistingCampaign}
          onEnterWorld={enterExistingWorld}
          publishedWorld={{
            genre: selectedWorld.genre.replace(/[_-]+/g, ' '),
            location: selectedLocationLabel,
            scenarioDescription: selectedScenario.description,
            scenarioTitle: selectedScenario.title,
            tone: selectedWorld.tone,
            worldTitle: selectedWorld.title,
          }}
        />
      ) : null}
    </div>
  );
}
