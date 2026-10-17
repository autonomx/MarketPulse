/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { omnixApiClient } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import { imageGenerationDefaults } from '../settings';
import { loadSettingsProfile } from '../settings';
import { FeatureSubmitFeedback } from '../../shared/FeatureSubmitFeedback';
import { ImageAssetGallery } from './ImageAssetGallery';
import './ImageGenerationWorkspace.css';
import './ImageGenerationWorkspaceInteractions.css';
import { ImageJobList } from './ImageJobList';
import { ImageLatestResult } from './ImageLatestResult';
import {
  ImageModelControl,
  imageModelGenerationBlockReason,
  selectedImageModel,
  type ImageModelAction,
} from './ImageModelControl';
import { ImageReadinessPanel } from './ImageReadinessPanel';
import { imageApi } from './imageApi';
import { ImageRequestForm } from './ImageRequestForm';
import {
  readyImageProviders,
  resolveImageReadiness,
} from './imageReadinessModel';
import { buildImageGenerateInput, type ImageRequestFormValues } from './imageRequestModel';
import {
  IMAGE_ASSETS_QUERY_KEY,
  IMAGE_JOB_EVENT_TYPES,
  IMAGE_JOBS_QUERY_KEY,
  completedImageAssetIds,
  hasActiveImageJobs,
  isCompletedImageJobEventPayload,
  isImageJobEventPayload,
  parseJobEvent,
  selectLatestImageAsset,
} from './imageWorkspaceModel';
import type { components } from './api/generated';

const DEFAULT_IMAGE_MODEL = 'flux_klein';
const IMAGE_MODEL_QUERY_ROOT = ['image-generation', 'model-status'] as const;

type ImageModelDownloadRequest = components['schemas']['ImageModelDownloadRequest'];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Image model action failed.';
}

export function ImageGenerationWorkspaceImpl({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [selectedModelProvider, setSelectedModelProvider] = useState(DEFAULT_IMAGE_MODEL);
  const providersQuery = useQuery({ queryKey: ['platform', 'providers'], queryFn: () => omnixApiClient.listProviders() });
  const workersQuery = useQuery({
    queryKey: ['image-generation', 'worker-health'],
    queryFn: imageApi.workerHealth,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: Infinity,
  });
  const modelStatusQuery = useQuery({
    queryKey: [...IMAGE_MODEL_QUERY_ROOT, selectedModelProvider],
    queryFn: () => imageApi.modelStatus(selectedModelProvider),
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: Infinity,
  });
  const jobsQuery = useQuery({
    queryKey: IMAGE_JOBS_QUERY_KEY,
    queryFn: imageApi.jobs,
    refetchInterval: (query) => (hasActiveImageJobs(query.state.data) ? 1_500 : false),
  });
  const assetsQuery = useQuery({
    queryKey: IMAGE_ASSETS_QUERY_KEY,
    queryFn: imageApi.assets,
  });
  const settingsQuery = useQuery({ queryKey: ['settings', 'profile'], queryFn: loadSettingsProfile });
  const moduleDefaults = useMemo(() => imageGenerationDefaults(settingsQuery.data?.profile), [settingsQuery.data?.profile]);
  const selectedFormDefaults = useMemo(() => ({
    ...moduleDefaults,
    providerId: `image:${selectedModelProvider}`,
  }), [moduleDefaults, selectedModelProvider]);

  useEffect(() => {
    if (typeof EventSource === 'undefined') return;
    const source = new EventSource('/events');
    const handleEvent = (event: Event) => {
      if (!(event instanceof MessageEvent)) return;
      const payload = parseJobEvent(event.data);
      if (!isImageJobEventPayload(payload)) return;
      void queryClient.invalidateQueries({ queryKey: IMAGE_JOBS_QUERY_KEY });
      if (event.type === 'job.completed' || isCompletedImageJobEventPayload(payload)) {
        void queryClient.refetchQueries({ queryKey: IMAGE_ASSETS_QUERY_KEY, type: 'active' });
      }
    };
    IMAGE_JOB_EVENT_TYPES.forEach((eventType) => source.addEventListener(eventType, handleEvent));
    return () => {
      IMAGE_JOB_EVENT_TYPES.forEach((eventType) => source.removeEventListener(eventType, handleEvent));
      source.close();
    };
  }, [queryClient]);

  const imageProviders = useMemo(() => readyImageProviders(providersQuery.data), [providersQuery.data]);
  const readiness = useMemo(() => resolveImageReadiness({
    providers: providersQuery.data,
    workers: workersQuery.data,
    loading: providersQuery.isLoading || settingsQuery.isLoading,
    providerError: providersQuery.isError,
    workerError: workersQuery.isError,
  }), [providersQuery.data, providersQuery.isError, providersQuery.isLoading, settingsQuery.isLoading, workersQuery.data, workersQuery.isError]);
  const imageJobs = jobsQuery.data?.jobs ?? [];
  const imageAssets = assetsQuery.data?.assets ?? [];
  const missingCompletedAssetSignature = useMemo(() => {
    const availableAssetIds = new Set(imageAssets.map((asset) => asset.id));
    return completedImageAssetIds(imageJobs)
      .filter((assetId) => !availableAssetIds.has(assetId))
      .sort()
      .join('|');
  }, [imageAssets, imageJobs]);

  useEffect(() => {
    if (!missingCompletedAssetSignature) return;
    let disposed = false;
    const refreshAssets = () => {
      if (disposed) return;
      void queryClient.refetchQueries({ queryKey: IMAGE_ASSETS_QUERY_KEY, type: 'active' });
    };

    refreshAssets();
    const retryTimer = window.setTimeout(refreshAssets, 750);
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
    };
  }, [missingCompletedAssetSignature, queryClient]);

  const refreshModelQueries = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: IMAGE_MODEL_QUERY_ROOT }),
    queryClient.invalidateQueries({ queryKey: ['image-generation', 'worker-health'] }),
    queryClient.invalidateQueries({ queryKey: ['platform', 'providers'] }),
  ]);
  const downloadModelMutation = useMutation({
    mutationFn: (request: ImageModelDownloadRequest) => imageApi.downloadModel(request),
    onSuccess: refreshModelQueries,
  });
  const loadModelMutation = useMutation({
    mutationFn: (provider: string) => imageApi.loadModel(provider),
    onSuccess: refreshModelQueries,
  });
  const unloadModelMutation = useMutation({
    mutationFn: (provider: string) => imageApi.unloadModel(provider),
    onSuccess: refreshModelQueries,
  });
  const modelAction: ImageModelAction = downloadModelMutation.isPending
    ? { type: 'download', provider: downloadModelMutation.variables.provider ?? '' }
    : loadModelMutation.isPending
      ? { type: 'load', provider: loadModelMutation.variables }
      : unloadModelMutation.isPending
        ? { type: 'unload', provider: unloadModelMutation.variables }
        : null;
  const selectedModel = selectedImageModel(modelStatusQuery.data, selectedModelProvider);
  const modelBlockReason = imageModelGenerationBlockReason(
    modelStatusQuery.data,
    selectedModelProvider,
    modelStatusQuery.isLoading,
    modelStatusQuery.isError,
  );
  const generationDisabledReason = !readiness.canGenerate ? readiness.message : modelBlockReason;

  const createJobMutation = useMutation({
    mutationFn: (values: ImageRequestFormValues) => omnixApiClient.createJob({
      module: 'image-generation',
      type: 'image.generate',
      resource_class: 'gpu:image',
      priority: 0,
      input_payload: buildImageGenerateInput(
        { ...values, providerId: `image:${selectedModelProvider}` },
        selectedFormDefaults,
      ),
      stages: [
        { id: 'generate-image', label: 'Generate image', resource_class: 'gpu:image', status: 'queued' },
        { id: 'store-asset', label: 'Store image asset', resource_class: 'cpu', status: 'queued' },
      ],
    }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: IMAGE_JOBS_QUERY_KEY }),
  });
  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => omnixApiClient.cancelJob(jobId, 'Canceled from Image Generation workspace'),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: IMAGE_JOBS_QUERY_KEY }),
  });
  const retryJobMutation = useMutation({
    mutationFn: (jobId: string) => imageApi.retryJob(jobId),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: IMAGE_JOBS_QUERY_KEY }),
  });
  const latestAsset = useMemo(
    () => selectLatestImageAsset(imageAssets, imageJobs, selectedAssetId, createJobMutation.data?.id),
    [createJobMutation.data?.id, imageAssets, imageJobs, selectedAssetId],
  );
  const submitStatus = createJobMutation.isPending
    ? 'queueing'
    : createJobMutation.isError
      ? 'error'
      : selectedModel?.loaded
        ? createJobMutation.data?.status ?? readiness.status
        : 'model unloaded';

  const refreshReadiness = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['platform', 'providers'] }),
      queryClient.invalidateQueries({ queryKey: ['image-generation', 'worker-health'] }),
      queryClient.invalidateQueries({ queryKey: IMAGE_MODEL_QUERY_ROOT }),
      queryClient.invalidateQueries({ queryKey: ['settings', 'profile'] }),
    ]);
  };

  const selectModelProvider = (provider: string) => {
    downloadModelMutation.reset();
    loadModelMutation.reset();
    unloadModelMutation.reset();
    setSelectedModelProvider(provider);
  };

  const openAssetInGallery = (assetId: string) => {
    setSelectedAssetId(assetId);
    document.getElementById('image-assets')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const modelActionError = downloadModelMutation.isError && downloadModelMutation.variables.provider === selectedModelProvider
    ? errorMessage(downloadModelMutation.error)
    : loadModelMutation.isError && loadModelMutation.variables === selectedModelProvider
      ? errorMessage(loadModelMutation.error)
      : unloadModelMutation.isError && unloadModelMutation.variables === selectedModelProvider
        ? errorMessage(unloadModelMutation.error)
        : modelStatusQuery.isError
          ? errorMessage(modelStatusQuery.error)
          : undefined;
  const modelName = selectedModel?.model || selectedModelProvider;
  const systemStatus = selectedModel?.loaded ? 'ready' : modelStatusQuery.isError ? 'blocked' : 'degraded';
  const systemTitle = selectedModel?.loaded ? `${modelName} loaded` : `${modelName} unloaded`;
  const systemMessage = selectedModel?.loaded
    ? 'Image generation is ready.'
    : selectedModel?.local_model?.complete
      ? 'Model files are present, but weights are not resident.'
      : 'Download the selected model, then load it explicitly.';

  return (
    <WorkspacePanel labelledBy="module-title" className="image-workspace">
      <h2 id="module-title" className="visually-hidden">{module.label}</h2>

      <div className="image-workspace-status-row">
        <section className="image-flow-banner" aria-label="Image generation flow">
          <span className="image-flow-icon" aria-hidden="true">⌾</span>
          <p><strong>Flow:</strong> Select a model <b>→</b> download if needed <b>→</b> load explicitly <b>→</b> generate.</p>
        </section>
        <section className={`image-system-card ${systemStatus}`} aria-live="polite">
          <span className="image-system-dot" aria-hidden="true" />
          <div><strong>{systemTitle}</strong><small>{systemMessage}</small></div>
          <span className="image-system-wave">{selectedModel?.state || 'checking'}</span>
        </section>
      </div>

      <div className="image-workspace-grid">
        <section className="image-surface image-request-card" aria-labelledby="image-request-title">
          <header className="image-section-header">
            <div className="image-section-heading">
              <span className="image-section-icon" aria-hidden="true">▧</span>
              <div>
                <Title id="image-request-title" order={3} aria-label="Image request">Image Request</Title>
                <Text size="sm">Choose a local model, download it once, and load weights only when needed.</Text>
              </div>
            </div>
            <OmnixStatusPill>{submitStatus}</OmnixStatusPill>
          </header>
          <ImageReadinessPanel
            readiness={readiness}
            refreshing={providersQuery.isFetching || workersQuery.isFetching || settingsQuery.isFetching || modelStatusQuery.isFetching}
            onRefresh={() => void refreshReadiness()}
          />
          <ImageModelControl
            status={modelStatusQuery.data}
            selectedProvider={selectedModelProvider}
            statusLoading={modelStatusQuery.isLoading || modelStatusQuery.isFetching}
            action={modelAction}
            error={modelActionError}
            onSelect={selectModelProvider}
            onDownload={(provider, hfToken) => downloadModelMutation.mutate({
              provider,
              ...(hfToken ? { hf_token: hfToken } : {}),
            })}
            onLoad={(provider) => loadModelMutation.mutate(provider)}
            onUnload={(provider) => unloadModelMutation.mutate(provider)}
            onRefresh={() => void modelStatusQuery.refetch()}
          />
          <ImageRequestForm
            defaults={selectedFormDefaults}
            providers={imageProviders}
            lockedProviderId={`image:${selectedModelProvider}`}
            pending={createJobMutation.isPending}
            disabled={Boolean(generationDisabledReason) || Boolean(modelAction)}
            disabledReason={generationDisabledReason}
            resetToken={createJobMutation.data?.id}
            onSubmit={(values) => createJobMutation.mutate(values)}
          />
          <FeatureSubmitFeedback
            error={createJobMutation.error}
            errorPrefix="Image request"
            isError={createJobMutation.isError}
            isPending={createJobMutation.isPending}
            jobId={createJobMutation.data?.id}
            pendingMessage="Queueing image job..."
            successPrefix="Image job queued"
          />
        </section>

        <aside className="image-workspace-rail">
          <section className="image-surface image-jobs-card" aria-labelledby="image-jobs-title">
            <header className="image-section-header image-section-header-compact">
              <div className="image-section-heading">
                <span className="image-section-icon" aria-hidden="true">☷</span>
                <div><Title id="image-jobs-title" order={3}>Image Jobs</Title><Text size="sm">Monitor and manage queued generations.</Text></div>
              </div>
            </header>
            <ImageJobList
              jobs={imageJobs}
              cancelingJobId={cancelJobMutation.isPending ? cancelJobMutation.variables : undefined}
              retryingJobId={retryJobMutation.isPending ? retryJobMutation.variables : undefined}
              onCancel={(jobId) => cancelJobMutation.mutate(jobId)}
              onRetry={(jobId) => retryJobMutation.mutate(jobId)}
              onSelectAsset={setSelectedAssetId}
            />
            {cancelJobMutation.isError ? <Text c="red" size="sm" role="alert">Image job cancel failed.</Text> : null}
            {retryJobMutation.isError ? <Text c="red" size="sm" role="alert">Image job retry failed.</Text> : null}
          </section>

          <ImageLatestResult asset={latestAsset} onOpenInAssets={openAssetInGallery} />
        </aside>

        <section id="image-assets" className="image-surface image-assets-card" aria-labelledby="image-assets-title">
          <header className="image-section-header image-assets-heading">
            <div className="image-section-heading">
              <span className="image-section-icon" aria-hidden="true">▣</span>
              <div><Title id="image-assets-title" order={3}>Image Assets</Title><Text size="sm">Browse and manage generated images and assets.</Text></div>
            </div>
          </header>
          <ImageAssetGallery assets={imageAssets} selectedAssetId={selectedAssetId} onSelect={setSelectedAssetId} />
        </section>
      </div>
    </WorkspacePanel>
  );
}
