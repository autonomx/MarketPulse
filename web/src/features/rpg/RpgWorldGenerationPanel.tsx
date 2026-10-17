import { forwardRef, useImperativeHandle, useMemo, useState } from 'react';
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldAuthoringClient,
  type RpgAuthoringSection,
} from './api/rpgWorldAuthoringClient';
import {
  rpgWorldLibraryClient,
  RpgWorldGenerationRequestError,
  type RpgWorldGenerationRun,
} from './api/rpgWorldLibraryClient';
import './RpgWorldGenerationPanel.css';

interface RpgWorldGenerationPanelProps {
  generation?: RpgWorldGenerationRun | Record<string, never>;
  onOpenImages?: () => void;
  profileApproved: boolean;
  sections: RpgAuthoringSection[];
  worldId: string;
}

interface GenerationMutationInput {
  scope: Record<string, unknown>;
  strategyOverride?: string;
  feedbackPrefix?: string;
}

interface TopicLoreQuality {
  score: number;
  threshold: number;
  status: string;
  attempts: number;
}

export interface RpgWorldGenerationPanelHandle {
  generateWorld: () => void;
  regenerateStale: () => void;
  retryFailed: () => void;
  continueGeneration: () => void;
  publish: () => void;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : [];
}

function statusLabel(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function parseEntityManifest(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Entity manifest must be a JSON object.');
  }
  return parsed as Record<string, unknown>;
}

function topicLoreQuality(value: unknown): TopicLoreQuality | undefined {
  const topic = record(record(value).topic);
  const provenance = record(topic.provenance);
  const report = record(provenance.lore_quality);
  const score = Number(provenance.lore_quality_selected_score ?? provenance.lore_quality_score ?? report.score);
  if (!Number.isFinite(score)) return undefined;
  const threshold = Number(provenance.lore_quality_threshold ?? report.threshold ?? 80);
  const attempts = Number(provenance.lore_quality_total_attempts ?? provenance.targeted_regeneration_attempt_count ?? 1);
  return {
    score,
    threshold: Number.isFinite(threshold) ? threshold : 80,
    status: String(provenance.lore_quality_status ?? report.status ?? (score >= threshold ? 'accepted' : 'needs_review')),
    attempts: Number.isFinite(attempts) ? attempts : 1,
  };
}

function generationResultFeedback(result: unknown, prefix: string): string {
  const response = record(result);
  const run = record(response.run);
  const summary = record(response.execution_summary);
  const route = record(response.resolved_route);
  const queued = Number(summary.queued_count ?? 0);
  const reused = Number(summary.reused_count ?? 0);
  const protectedCount = Number(summary.protected_count ?? 0);
  const provider = String(route.provider ?? record(run.settings).provider_route ?? 'unknown');
  const model = String(route.model ?? record(run.settings).model ?? 'default model');
  const routeLabel = provider === 'deterministic'
    ? 'deterministic reference-safe generator'
    : `${provider}${model ? ` / ${model}` : ''}`;
  const work = queued > 0
    ? `${queued} provider topic job${queued === 1 ? '' : 's'} queued; ${reused} reused; ${protectedCount} protected.`
    : `No provider calls were queued; ${reused} topic${reused === 1 ? ' was' : 's were'} reused and ${protectedCount} protected.`;
  return `${prefix}: ${String(run.run_id ?? 'unknown run')} · ${work} Route: ${routeLabel}.`;
}

function isPartialReviewRun(run: RpgWorldGenerationRun | undefined): boolean {
  if (run?.status !== 'review') return false;
  const planned = stringArray(record(run.plan).topic_ids);
  const nodes = record(run.graph).nodes;
  return planned.length > 0 && Array.isArray(nodes) && nodes.length > planned.length;
}

function generationErrorFeedback(cause: unknown, fallback: string): string {
  const message = cause instanceof Error ? cause.message : '';
  if (message.includes('world_forge_provider_and_model_required')) {
    return 'World generation needs an AI provider and model. Open Settings, choose a Default LLM provider and its model under AI Providers, save, then return here and generate the world.';
  }
  if (
    cause instanceof RpgWorldGenerationRequestError
    && cause.retryable
  ) {
    if (cause.code === 'world_generation_database_authentication_failed') {
      return 'PostgreSQL is reachable, but Omnix’s database credential was rejected. Refresh the protected credential and restart Omnix; completed topics remain safe.';
    }
    if (cause.code !== 'world_generation_database_unavailable') {
      return cause.message;
    }
    return 'World generation is paused because Omnix cannot reach PostgreSQL. Completed topics remain safe. Restore database connectivity, then retry this same run.';
  }
  return message || fallback;
}

export const RpgWorldGenerationPanel = forwardRef<
  RpgWorldGenerationPanelHandle,
  RpgWorldGenerationPanelProps
>(function RpgWorldGenerationPanel({ generation, onOpenImages, profileApproved, sections, worldId }, ref) {
  const queryClient = useQueryClient();
  const [depth, setDepth] = useState('standard');
  const [startingLocation, setStartingLocation] = useState('');
  const [backgroundExpansion, setBackgroundExpansion] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [strategy, setStrategy] = useState('reuse_unchanged');
  const [replaceLocked, setReplaceLocked] = useState(false);
  const [directions, setDirections] = useState<Record<string, string>>({});
  const [generatorVersion, setGeneratorVersion] = useState('world-generator-v1');
  const [promptVersion, setPromptVersion] = useState('world-prompt-v1');
  const [providerRoute, setProviderRoute] = useState('configured');
  const [model, setModel] = useState('configured');
  const [entityManifestJson, setEntityManifestJson] = useState('{}');
  const [feedback, setFeedback] = useState('');
  const [diagnosticLog, setDiagnosticLog] = useState('resources\\logs\\rpg\\world-generation-YYYY-MM-DD.jsonl');
  const generationSections = useMemo(
    () => sections.filter((section) => section.supports_generation),
    [sections],
  );
  const currentRun = generation && 'run_id' in generation ? generation as RpgWorldGenerationRun : undefined;
  const progress = record(currentRun?.progress);
  const failedTopicIds = stringArray(progress.failed_topic_ids);
  const generationBusy = currentRun?.status === 'running' || currentRun?.status === 'planned';
  const canRetryFailed = profileApproved && !generationBusy && failedTopicIds.length > 0;
  const canContinueGeneration = profileApproved && !generationBusy && (
    currentRun?.status === 'failed' || isPartialReviewRun(currentRun)
  );
  const qualityQueries = useQueries({
    queries: generationSections.map((section) => ({
      queryKey: ['feature', 'rpg', 'world-authoring-section', worldId, section.id],
      queryFn: () => rpgWorldAuthoringClient.section(worldId, section.id),
      enabled: Boolean(currentRun) && !generationBusy,
      staleTime: 10_000,
    })),
  });
  const qualityByTopic = new Map<string, TopicLoreQuality>();
  qualityQueries.forEach((query, index) => {
    const quality = topicLoreQuality(query.data);
    if (quality) qualityByTopic.set(generationSections[index].id, quality);
  });
  const reviewTopicIds = generationSections
    .filter((section) => qualityByTopic.get(section.id)?.status === 'needs_review')
    .map((section) => section.id);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-workspace'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] }),
    ]);
  };

  const generate = useMutation({
    mutationFn: ({ scope, strategyOverride }: GenerationMutationInput) => rpgWorldLibraryClient.startGeneration(worldId, {
      depth,
      starting_location: startingLocation,
      background_expansion: backgroundExpansion,
      scope,
      strategy: strategyOverride ?? strategy,
      replace_locked: replaceLocked,
      directives: Object.fromEntries(
        Object.entries(directions)
          .filter(([, value]) => value.trim())
          .map(([topicId, direction]) => [topicId, { direction: direction.trim() }]),
      ),
      entity_manifest: parseEntityManifest(entityManifestJson),
      generator_version: generatorVersion.trim() || 'world-generator-v1',
      prompt_version: promptVersion.trim() || 'world-prompt-v1',
      provider_route: providerRoute.trim() || 'configured',
      model: model.trim() || 'configured',
    }),
    onSuccess: async (result, input) => {
      if (result.diagnostic_log) setDiagnosticLog(result.diagnostic_log);
      setFeedback(generationResultFeedback(result, input.feedbackPrefix ?? 'Generation started'));
      await refresh();
    },
    onError: (cause) => setFeedback(generationErrorFeedback(cause, 'Generation could not be started.')),
  });

  const retryFailed = useMutation({
    mutationFn: () => {
      if (!currentRun) throw new Error('No failed generation run is available.');
      return rpgWorldLibraryClient.retryFailedGeneration(currentRun.run_id);
    },
    onSuccess: async (result) => {
      if (result.diagnostic_log) setDiagnosticLog(result.diagnostic_log);
      setFeedback(generationResultFeedback(result, `Retry started from ${result.retry_of_run_id ?? currentRun?.run_id}`));
      await refresh();
    },
    onError: (cause) => setFeedback(generationErrorFeedback(cause, 'Failed topics could not be retried.')),
  });

  const continueGeneration = useMutation({
    mutationFn: () => {
      if (!currentRun) throw new Error('No resumable generation run is available.');
      return rpgWorldLibraryClient.continueGeneration(currentRun.run_id);
    },
    onSuccess: async (result) => {
      if (result.diagnostic_log) setDiagnosticLog(result.diagnostic_log);
      setFeedback(generationResultFeedback(result, `Continuation started from ${result.continue_of_run_id ?? currentRun?.run_id}`));
      await refresh();
    },
    onError: (cause) => setFeedback(generationErrorFeedback(cause, 'Generation could not be continued.')),
  });

  const publish = useMutation({
    mutationFn: () => {
      if (!currentRun) throw new Error('No generation run is available to publish.');
      return rpgWorldLibraryClient.publishGeneration(currentRun.run_id);
    },
    onSuccess: async (result) => {
      const publication = record(result.publication);
      const revision = publication.world_revision;
      const release = publication.world_release;
      setFeedback(
        revision && release
          ? `Published world revision ${revision}, release ${release}.`
          : 'World generation published.',
      );
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'World generation could not be published.'),
  });

  const start = (mode: string) => {
    if (!profileApproved) {
      setFeedback('Review and approve the current world profile before generating content.');
      return;
    }
    if (generationBusy) {
      setFeedback('World generation is still running. Wait for it to finish before starting another scope.');
      return;
    }
    if (mode === 'failed') {
      if (!failedTopicIds.length) {
        setFeedback('No failed topics are available to retry.');
        return;
      }
      retryFailed.mutate();
      return;
    }
    if (mode === 'continue') {
      if (!canContinueGeneration) {
        setFeedback('Only a failed or partial-review generation run can be continued.');
        return;
      }
      continueGeneration.mutate();
      return;
    }
    if (mode === 'selected' && !selected.length) {
      setFeedback('Select at least one topic before generating a selected scope.');
      return;
    }
    const scope = mode === 'selected'
      ? { mode, topic_ids: selected }
      : { mode };
    generate.mutate({ scope });
  };

  const retryTopics = (topicIds: string[], label: string) => {
    if (!topicIds.length) {
      setFeedback(`No ${label.toLowerCase()} topics are available to retry.`);
      return;
    }
    if (generationBusy) {
      setFeedback('Wait for world generation to finish before retrying lore.');
      return;
    }
    generate.mutate({
      scope: { mode: 'selected', topic_ids: topicIds },
      strategyOverride: 'force',
      feedbackPrefix: `${label} retry started`,
    });
  };

  useImperativeHandle(ref, () => ({
    generateWorld: () => start('full'),
    regenerateStale: () => start('stale'),
    retryFailed: () => start('failed'),
    continueGeneration: () => start('continue'),
    publish: () => publish.mutate(),
  }));

  const toggleSelected = (topicId: string, checked: boolean) => {
    setSelected((current) => checked
      ? Array.from(new Set([...current, topicId]))
      : current.filter((value) => value !== topicId));
  };

  const updateDirection = (topicId: string, value: string) => {
    setDirections((current) => ({ ...current, [topicId]: value }));
  };

  const generationDisabled = generate.isPending
    || retryFailed.isPending
    || continueGeneration.isPending
    || generationBusy
    || !profileApproved;

  return (
    <section className="rpg-authoring-page rpg-generation-panel" aria-label="World generation">
      <div className="rpg-authoring-page-heading">
        <div><p className="eyebrow">Workspace</p><h2>World Generation</h2><p>Generate the full world or safely target selected, stale, failed, or low-scoring topics.</p></div>
        {currentRun ? <span>{statusLabel(currentRun.status)} · {Number(progress.percent || 0)}%</span> : <span>Not generated</span>}
      </div>

      {!profileApproved ? <p className="rpg-authoring-feedback">World-content controls are locked until the profile preview above is approved.</p> : null}

      <div className="rpg-generation-settings">
        <label><span>Depth</span><select aria-label="Depth" value={depth} onChange={(event) => setDepth(event.currentTarget.value)}><option value="quick">Quick</option><option value="standard">Standard</option><option value="epic">Epic</option></select></label>
        <label><span>Starting location</span><input aria-label="Starting location" placeholder="Optional stable location ID" value={startingLocation} onChange={(event) => setStartingLocation(event.currentTarget.value)} /></label>
        <label><span>Strategy</span><select value={strategy} onChange={(event) => setStrategy(event.currentTarget.value)}><option value="reuse_unchanged">Reuse unchanged</option><option value="force">Force selected replacement</option></select></label>
        <label className="rpg-authoring-checkbox"><input type="checkbox" checked={backgroundExpansion} onChange={(event) => setBackgroundExpansion(event.currentTarget.checked)} /><span>Allow optional topics to continue as background expansion</span></label>
        <label className="rpg-authoring-checkbox"><input type="checkbox" checked={replaceLocked} onChange={(event) => setReplaceLocked(event.currentTarget.checked)} /><span>Allow forced replacement of locked manual topics</span></label>
      </div>

      <details className="rpg-generation-advanced">
        <summary>Advanced generation settings</summary>
        <div className="rpg-generation-advanced-grid">
          <label><span>Provider route</span><input aria-label="World generation provider route" value={providerRoute} onChange={(event) => setProviderRoute(event.currentTarget.value)} /></label>
          <label><span>Model</span><input aria-label="World generation model" value={model} onChange={(event) => setModel(event.currentTarget.value)} /></label>
          <label><span>Generator version</span><input aria-label="World generator version" value={generatorVersion} onChange={(event) => setGeneratorVersion(event.currentTarget.value)} /></label>
          <label><span>Prompt version</span><input aria-label="World prompt version" value={promptVersion} onChange={(event) => setPromptVersion(event.currentTarget.value)} /></label>
          <label className="rpg-generation-manifest"><span>Entity manifest JSON</span><textarea aria-label="World generation entity manifest" rows={8} value={entityManifestJson} onChange={(event) => setEntityManifestJson(event.currentTarget.value)} /></label>
        </div>
      </details>

      <div className="rpg-generation-actions">
        <button type="button" disabled={generationDisabled} onClick={() => start('full')}>Generate World</button>
        <button type="button" disabled={generationDisabled || !selected.length} onClick={() => start('selected')}>Generate Selected</button>
        <button
          className="rpg-secondary-button"
          type="button"
          disabled={generationDisabled || !selected.length}
          title="Force a fresh initial generation plus three lore retries for the selected completed topics"
          onClick={() => retryTopics(selected, 'Selected lore')}
        >
          Retry Selected Lore
        </button>
        <button
          className="rpg-secondary-button"
          type="button"
          disabled={generationDisabled || !reviewTopicIds.length}
          title={reviewTopicIds.length ? 'Force fresh generation for all topics whose selected lore score remains below threshold' : 'No topics currently need lore review'}
          onClick={() => retryTopics(reviewTopicIds, 'Needs review')}
        >
          Retry Needs Review{reviewTopicIds.length ? ` (${reviewTopicIds.length})` : ''}
        </button>
        <button className="rpg-secondary-button" type="button" disabled={generationDisabled} onClick={() => start('stale')}>Regenerate Stale</button>
        <button
          className="rpg-secondary-button"
          type="button"
          disabled={generate.isPending || retryFailed.isPending || continueGeneration.isPending || !canRetryFailed}
          title={canRetryFailed ? `Retry ${failedTopicIds.length} failed topic(s) using the original run settings` : 'No terminally failed topics are available'}
          onClick={() => start('failed')}
        >
          {retryFailed.isPending ? 'Retrying…' : `Retry Failed${failedTopicIds.length ? ` (${failedTopicIds.length})` : ''}`}
        </button>
        <button
          className="rpg-secondary-button"
          type="button"
          disabled={generate.isPending || retryFailed.isPending || continueGeneration.isPending || !canContinueGeneration}
          title={canContinueGeneration ? 'Resume this run, preserving completed topics and generating what remains' : 'A failed or partial review generation run is required'}
          onClick={() => start('continue')}
        >
          {continueGeneration.isPending ? 'Continuing…' : 'Continue Generation'}
        </button>
        <button type="button" disabled={publish.isPending || currentRun?.status !== 'review'} onClick={() => publish.mutate()}>{publish.isPending ? 'Publishing…' : 'Publish World'}</button>
        {onOpenImages ? <button className="rpg-secondary-button" type="button" onClick={onOpenImages}>Generate Images</button> : null}
      </div>
      {generationBusy ? <small>Generation is running. Failed-topic and Game Master lore retry become available after the current run finishes.</small> : null}
      {currentRun && currentRun.status !== 'review' && !generationBusy ? <small>Publishing becomes available when generation reaches Review.</small> : null}
      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}{feedback.includes('Open Settings') ? <> <a href="/settings">Open AI Provider Settings</a></> : null}</p> : null}
      <small className="rpg-generation-diagnostic-hint">
        Compact diagnostics: <code>{diagnosticLog}</code>. Prompts, completions, and generated world content are omitted.
      </small>

      <div className="rpg-generation-topic-grid">
        {generationSections.map((section) => {
          const quality = qualityByTopic.get(section.id);
          return (
            <article key={section.id}>
              <label className="rpg-generation-topic-choice">
                <input
                  type="checkbox"
                  checked={selected.includes(section.id)}
                  onChange={(event) => toggleSelected(section.id, event.currentTarget.checked)}
                />
                <span>
                  <strong>{section.label}</strong>
                  <small>
                    {statusLabel(section.operational_status)} · {statusLabel(section.editorial_status)}
                    {quality ? ` · Lore ${quality.score}/100 (${quality.attempts} attempt${quality.attempts === 1 ? '' : 's'}) · ${statusLabel(quality.status)}` : ''}
                  </small>
                </span>
              </label>
              <textarea
                aria-label={`Generation direction for ${section.label}`}
                placeholder="Optional direction for this topic or retry…"
                rows={2}
                value={directions[section.id] ?? ''}
                onChange={(event) => updateDirection(section.id, event.currentTarget.value)}
              />
            </article>
          );
        })}
      </div>

      {currentRun ? (
        <details className="rpg-authoring-structured-data">
          <summary>Current run details</summary>
          <pre>{JSON.stringify({ scope: record(currentRun.context).scope, progress: currentRun.progress, plan: currentRun.plan, error: currentRun.error }, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
});
