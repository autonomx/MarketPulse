/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldImageClient,
  type RpgWorldImageTarget,
} from './api/rpgWorldImageClient';
import './RpgWorldImagesPanel.css';

interface RpgWorldImagesPanelProps {
  worldId: string;
}

function imageUrl(assetId: string | null | undefined): string | undefined {
  return assetId ? `/api/assets/${encodeURIComponent(assetId)}/file` : undefined;
}

function latestAsset(target: RpgWorldImageTarget): string | undefined {
  return target.attempts.find((attempt) => attempt.asset_id)?.asset_id ?? undefined;
}

function label(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function imageGenerationProgress(targets: RpgWorldImageTarget[]) {
  const counts = targets.reduce<Record<string, number>>((current, target) => {
    current[target.status] = (current[target.status] ?? 0) + 1;
    return current;
  }, {});
  const total = targets.length;
  const ready = counts.ready ?? 0;
  const queued = counts.queued ?? 0;
  const generating = counts.generating ?? 0;
  const failed = counts.failed ?? 0;
  const outstanding = total - ready - failed;

  return {
    total,
    ready,
    queued,
    generating,
    failed,
    outstanding,
    percent: total ? Math.round((ready / total) * 100) : 0,
  };
}

export function RpgWorldImagesPanel({ worldId }: RpgWorldImagesPanelProps) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [providerId, setProviderId] = useState('');
  const [style, setStyle] = useState('concept art');
  const [width, setWidth] = useState(768);
  const [height, setHeight] = useState(768);
  const [category, setCategory] = useState('all');
  const [feedback, setFeedback] = useState('');
  const query = useQuery({
    queryKey: ['feature', 'rpg', 'world-image-targets', worldId],
    queryFn: () => rpgWorldImageClient.list(worldId),
    refetchInterval: (state) => (
      state.state.data?.targets.some((target) => ['queued', 'generating'].includes(target.status))
        ? 3000
        : false
    ),
  });
  const targets = query.data?.targets ?? [];
  const progress = imageGenerationProgress(targets);
  const categories = useMemo(
    () => Array.from(new Set(targets.map((target) => target.target_type))).sort(),
    [targets],
  );
  const visibleTargets = useMemo(
    () => (category === 'all' ? targets : targets.filter((target) => target.target_type === category)),
    [category, targets],
  );
  const selectedTargets = useMemo(
    () => targets.filter((target) => selected.includes(target.target_id)),
    [selected, targets],
  );

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-image-targets', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-workspace'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
    ]);
  };

  const generate = useMutation({
    mutationFn: () => rpgWorldImageClient.generate(worldId, {
      target_ids: selected,
      prompts: Object.fromEntries(selectedTargets.map((target) => [
        target.target_id,
        prompts[target.target_id] ?? target.suggested_prompt,
      ])),
      provider_id: providerId,
      width,
      height,
      style,
      no_cache: false,
    }),
    onSuccess: async (result) => {
      setFeedback(`Queued ${result.jobs.length} image job${result.jobs.length === 1 ? '' : 's'}.`);
      setSelected([]);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Images could not be queued.'),
  });

  const review = useMutation({
    mutationFn: ({ targetId, reviewState, assetId }: { targetId: string; reviewState: string; assetId?: string }) => (
      rpgWorldImageClient.update(worldId, targetId, {
        review_state: reviewState,
        active_asset_id: assetId,
      })
    ),
    onSuccess: async () => {
      setFeedback('Image review updated.');
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Image review failed.'),
  });

  const regenerate = useMutation({
    mutationFn: (target: RpgWorldImageTarget) => rpgWorldImageClient.regenerate(
      worldId,
      target.target_id,
      {
        prompt: prompts[target.target_id] ?? target.suggested_prompt,
        provider_id: providerId,
        width,
        height,
        style,
        no_cache: true,
      },
    ),
    onSuccess: async () => {
      setFeedback('A replacement image was queued.');
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Image regeneration failed.'),
  });

  const regenerateAllImages = useMutation({
    mutationFn: () => rpgWorldImageClient.generate(worldId, {
      target_ids: targets.map((target) => target.target_id),
      prompts: Object.fromEntries(targets.map((target) => [
        target.target_id,
        prompts[target.target_id] ?? target.suggested_prompt,
      ])),
      provider_id: providerId,
      width,
      height,
      style,
      no_cache: true,
    }),
    onSuccess: async (result) => {
      setFeedback(`Queued fresh generations for ${result.jobs.length} image${result.jobs.length === 1 ? '' : 's'}.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Images could not be regenerated.'),
  });

  const regeneratePrompts = useMutation({
    mutationFn: (targetIds: string[]) => rpgWorldImageClient.regeneratePrompts(worldId, { target_ids: targetIds }),
    onSuccess: async (_result, targetIds) => {
      setPrompts((current) => Object.fromEntries(
        Object.entries(current).filter(([targetId]) => !targetIds.includes(targetId)),
      ));
      setFeedback(`Regenerated ${targetIds.length} image prompt${targetIds.length === 1 ? '' : 's'}.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Image prompts could not be regenerated.'),
  });

  const toggleTarget = (targetId: string, checked: boolean) => {
    setSelected((current) => (
      checked
        ? Array.from(new Set([...current, targetId]))
        : current.filter((value) => value !== targetId)
    ));
  };

  return (
    <section className="rpg-authoring-page rpg-world-images-panel" aria-label="World images">
      <div className="rpg-authoring-page-heading">
        <div><p className="eyebrow">Presentation assets</p><h2>Images</h2><p>Generate, review, and assign images independently from canon generation.</p></div>
        <span>{targets.length} target{targets.length === 1 ? '' : 's'}</span>
      </div>
      <div className="rpg-world-images-toolbar">
        <button className="rpg-secondary-button" type="button" disabled={!targets.length || regenerateAllImages.isPending} onClick={() => regenerateAllImages.mutate()}>{regenerateAllImages.isPending ? 'Queuing fresh images...' : 'Regenerate All Images'}</button>
        <button type="button" disabled={!selected.length || generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? 'Queuing…' : `Generate Selected (${selected.length})`}</button>
        <button className="rpg-secondary-button" type="button" disabled={!selected.length || regeneratePrompts.isPending} onClick={() => regeneratePrompts.mutate(selected)}>{regeneratePrompts.isPending ? 'Regenerating promptsâ€¦' : `Regenerate Selected Prompts (${selected.length})`}</button>
        <button className="rpg-secondary-button" type="button" disabled={!targets.length || regeneratePrompts.isPending} onClick={() => regeneratePrompts.mutate(targets.map((target) => target.target_id))}>Regenerate All Prompts</button>
        <label className="rpg-world-images-category-filter">
          <span>Category</span>
          <select aria-label="Image category" value={category} onChange={(event) => setCategory(event.currentTarget.value)}>
            <option value="all">All categories ({targets.length})</option>
            {categories.map((targetType) => <option key={targetType} value={targetType}>{label(targetType)}</option>)}
          </select>
        </label>
        <button className="rpg-secondary-button" type="button" onClick={() => setSelected(visibleTargets.filter((target) => ['missing', 'stale', 'failed'].includes(target.status)).map((target) => target.target_id))}>Select Missing &amp; Stale</button>
        <button className="rpg-secondary-button" type="button" disabled={!selected.length} onClick={() => setSelected([])}>Clear Selection</button>
      </div>
      <details className="rpg-world-images-advanced">
        <summary>Advanced generation settings</summary>
        <div>
          <label><span>Provider / model route</span><input aria-label="Image provider route" placeholder="Use configured image provider and model" value={providerId} onChange={(event) => setProviderId(event.currentTarget.value)} /></label>
          <label><span>Style</span><input aria-label="Image style" value={style} onChange={(event) => setStyle(event.currentTarget.value)} /></label>
          <label><span>Width</span><input aria-label="Image width" type="number" min={128} max={4096} step={64} value={width} onChange={(event) => setWidth(Number(event.currentTarget.value) || 768)} /></label>
          <label><span>Height</span><input aria-label="Image height" type="number" min={128} max={4096} step={64} value={height} onChange={(event) => setHeight(Number(event.currentTarget.value) || 768)} /></label>
        </div>
      </details>
      {progress.total ? (
        <section className="rpg-world-images-progress" aria-label="Image generation progress">
          <div className="rpg-world-images-progress-heading">
            <div>
              <p className="eyebrow">Generation progress</p>
              <strong>{progress.ready} of {progress.total} image{progress.total === 1 ? '' : 's'} ready</strong>
            </div>
            <span>{progress.percent}%</span>
          </div>
          <div
            className="rpg-world-images-progress-track"
            aria-label={`${progress.ready} of ${progress.total} images ready`}
            aria-valuemax={progress.total}
            aria-valuemin={0}
            aria-valuenow={progress.ready}
            role="progressbar"
          >
            <span style={{ width: `${progress.percent}%` }} />
          </div>
          <div className="rpg-world-images-progress-summary" aria-live="polite">
            {progress.generating ? <span className="is-generating">{progress.generating} generating</span> : null}
            {progress.queued ? <span>{progress.queued} queued</span> : null}
            {progress.outstanding && !progress.generating && !progress.queued ? <span>{progress.outstanding} awaiting generation</span> : null}
            {progress.failed ? <span className="is-failed">{progress.failed} failed</span> : null}
            {!progress.outstanding && !progress.failed ? <span className="is-complete">All images ready</span> : null}
          </div>
        </section>
      ) : null}
      {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
      {query.isPending ? <p>Discovering image targets…</p> : null}
      {query.isError ? <p className="rpg-world-catalog-error">Unable to load image targets.</p> : null}
      {!query.isPending && !query.isError && !targets.length ? <p>No image targets are available yet. Generate world canon first.</p> : null}
      <div className="rpg-world-image-grid">
        {visibleTargets.map((target) => {
          const generatedAsset = latestAsset(target);
          const visibleAsset = target.active_asset_id ?? generatedAsset;
          const preview = imageUrl(visibleAsset);
          return (
            <article key={target.target_id}>
              <div className="rpg-world-image-preview" style={preview ? { backgroundImage: `url(${JSON.stringify(preview)})` } : undefined}>
                {!preview ? <span>{target.role.slice(0, 1).toUpperCase()}</span> : null}
                <label><input type="checkbox" checked={selected.includes(target.target_id)} onChange={(event) => toggleTarget(target.target_id, event.currentTarget.checked)} /><span>Select</span></label>
              </div>
              <div className="rpg-world-image-copy">
                <h3>{String(target.metadata.entity_name ?? target.target_id)}</h3>
                <p>{label(target.target_type)} · {label(target.role)}</p>
                <div className="rpg-chip-row"><span>{label(target.status)}</span><span>{label(target.review_state)}</span>{target.active_asset_id ? <span>Active</span> : null}</div>
                <label className="rpg-world-image-prompt"><span>Prompt</span><textarea aria-label={`Prompt for ${String(target.metadata.entity_name ?? target.target_id)}`} rows={4} value={prompts[target.target_id] ?? target.suggested_prompt} onChange={(event) => {
                  const prompt = event.currentTarget.value;
                  setPrompts((current) => ({ ...current, [target.target_id]: prompt }));
                }} /></label>
                <div className="rpg-world-image-actions">
                  <button className="rpg-secondary-button" type="button" disabled={regenerate.isPending} onClick={() => regenerate.mutate(target)}>Regenerate</button>
                  <button type="button" disabled={!generatedAsset || review.isPending} onClick={() => review.mutate({ targetId: target.target_id, reviewState: 'approved', assetId: generatedAsset })}>Approve latest</button>
                  <button className="rpg-secondary-button" type="button" disabled={review.isPending} onClick={() => review.mutate({ targetId: target.target_id, reviewState: 'rejected' })}>Reject</button>
                </div>
                {target.attempts.length ? (
                  <details>
                    <summary>Previous images ({target.attempts.length})</summary>
                    {target.attempts.map((attempt) => (
                      <div key={attempt.job_id}>
                        <span>{label(attempt.status)}</span>
                        {attempt.asset_id ? <a href={imageUrl(attempt.asset_id)} target="_blank" rel="noreferrer">Open image</a> : null}
                        {attempt.asset_id ? <button className="rpg-link-button" type="button" disabled={review.isPending || target.active_asset_id === attempt.asset_id} onClick={() => review.mutate({ targetId: target.target_id, reviewState: 'approved', assetId: attempt.asset_id ?? undefined })}>Make active</button> : null}
                        {Object.keys(attempt.error ?? {}).length ? <small>{JSON.stringify(attempt.error)}</small> : null}
                      </div>
                    ))}
                  </details>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
