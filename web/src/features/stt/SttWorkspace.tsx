import { Button, Group, Progress, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { omnixApiClient, type ProviderFacadePayload } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixAssetCard, OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import { speechInputDefaults } from '../settings';
import { loadSettingsProfile } from '../settings';
import { FeatureSubmitFeedback } from '../../shared/FeatureSubmitFeedback';
import { buildSttInputPayload, buildSttStages, type SttJobFormValues } from './sttJobDefaults';
import { jobProgressPercent } from '../../api/jobProgress';
import { GatewayErrorNotice } from '../../shared/GatewayErrorNotice';

export function SttWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const providersQuery = useQuery({
    queryKey: ['platform', 'providers'],
    queryFn: () => omnixApiClient.listProviders(),
  });
  const jobsQuery = useQuery({
    queryKey: ['platform', 'jobs'],
    queryFn: () => omnixApiClient.listJobs(),
  });
  const assetsQuery = useQuery({
    queryKey: ['platform', 'assets'],
    queryFn: () => omnixApiClient.listAssets(),
  });
  const settingsQuery = useQuery({
    queryKey: ['settings', 'profile'],
    queryFn: () => loadSettingsProfile(),
  });
  const moduleDefaults = useMemo(() => speechInputDefaults(settingsQuery.data?.profile), [settingsQuery.data?.profile]);
  const {
    register,
    handleSubmit,
    reset,
    formState: { isDirty },
  } = useForm<SttJobFormValues>({
    defaultValues: { providerId: '', audioAssetId: '', sourcePath: '', language: '' },
  });
  useEffect(() => {
    if (!settingsQuery.data || isDirty) return;
    reset({
      providerId: moduleDefaults.providerId,
      audioAssetId: '',
      sourcePath: '',
      language: moduleDefaults.language,
    });
  }, [isDirty, moduleDefaults, reset, settingsQuery.data]);
  const sttProviders = useMemo(() => sttCapableProviders(providersQuery.data), [providersQuery.data]);
  const audioAssets = assetsQuery.data?.assets.filter((asset) => asset.type === 'audio' || asset.type === 'voice_sample') ?? [];
  const transcriptAssets = assetsQuery.data?.assets.filter((asset) => asset.type === 'transcript') ?? [];
  const sttJobs = jobsQuery.data?.jobs.filter((job) => job.module === 'stt') ?? [];
  const createJobMutation = useMutation({
    mutationFn: (values: SttJobFormValues) =>
      omnixApiClient.createJob({
        module: 'stt',
        type: 'stt.transcribe',
        resource_class: 'gpu:stt',
        priority: 0,
        input_ref: values.audioAssetId ? { asset_id: values.audioAssetId } : null,
        input_payload: buildSttInputPayload(values, moduleDefaults),
        stages: buildSttStages(moduleDefaults),
      }),
    onSuccess: async (_job, values) => {
      reset({ providerId: values.providerId, audioAssetId: values.audioAssetId, sourcePath: '', language: values.language });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
    },
  });
  const submitStatus = createJobMutation.isPending ? 'queueing' : createJobMutation.isError ? 'error' : createJobMutation.data?.status ?? 'ready';

  return (
    <WorkspacePanel labelledBy="module-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">Feature module</p>
          <h2 id="module-title">{module.label}</h2>
        </div>
        <code>{module.route}</code>
      </div>

      <p className="workspace-summary">{module.summary}</p>
      <GatewayErrorNotice label="Speech-to-text data" errors={[providersQuery.error, jobsQuery.error, assetsQuery.error]} />

      <div className="feature-layout">
        <section className="feature-panel">
          <Group justify="space-between" align="start">
            <div>
              <Title order={4}>Transcription</Title>
              <Text size="sm">Shared STT job queue</Text>
            </div>
            <OmnixStatusPill>{submitStatus}</OmnixStatusPill>
          </Group>

          <form className="feature-form" onSubmit={handleSubmit((values) => createJobMutation.mutate(values))}>
            <label>
              Provider
              <select {...register('providerId')}>
                <option value="">Default STT provider</option>
                {sttProviders.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Audio asset
              <select {...register('audioAssetId')}>
                <option value="">External path</option>
                {audioAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.file_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Language
              <input {...register('language')} placeholder="auto" />
            </label>
            <label className="feature-form-wide">
              Source path
              <input {...register('sourcePath')} placeholder="resources/data/input.wav" />
            </label>
            <Button className="feature-form-action" type="submit" disabled={createJobMutation.isPending} loading={createJobMutation.isPending}>
              {createJobMutation.isPending ? 'Queueing transcription…' : 'Queue transcription'}
            </Button>
          </form>

          <FeatureSubmitFeedback
            error={createJobMutation.error}
            errorPrefix="STT request"
            isError={createJobMutation.isError}
            isPending={createJobMutation.isPending}
            jobId={createJobMutation.data?.id}
            pendingMessage="Queueing STT job…"
            successPrefix="STT job queued"
          />
        </section>

        <section className="feature-panel">
          <Title order={4}>STT jobs</Title>
          {sttJobs.length ? (
            <div className="feature-list">
              {sttJobs.map((job) => (
                <article className="feature-mini-card" key={job.id}>
                  <Group justify="space-between">
                    <strong>{job.type}</strong>
                    <OmnixStatusPill>{job.status}</OmnixStatusPill>
                  </Group>
                  <Progress value={jobProgressPercent(job.progress)} aria-label={`${job.type} progress`} />
                  <Text size="sm">{job.resource_class}</Text>
                </article>
              ))}
            </div>
          ) : (
            <div className="platform-empty" role="status">
              No STT jobs queued.
            </div>
          )}
        </section>

        <section className="feature-panel feature-panel-wide">
          <Title order={4}>Transcript assets</Title>
          {transcriptAssets.length ? (
            <div className="platform-grid">
              {transcriptAssets.map((asset) => (
                <OmnixAssetCard key={asset.id} title={`${asset.type} / ${asset.module}`} metadata={asset.file_name} />
              ))}
            </div>
          ) : (
            <div className="platform-empty" role="status">
              No transcript assets indexed.
            </div>
          )}
        </section>
      </div>
    </WorkspacePanel>
  );
}

function sttCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.capabilities.includes('stt')) ?? [];
}

