import { Button, Group, Progress, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef } from 'react';
import { useForm } from 'react-hook-form';
import { omnixApiClient, type ProviderFacadePayload } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixAssetCard, OmnixAudioControls, OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import { voiceStudioDefaults } from '../settings';
import { loadSettingsProfile } from '../settings';
import { FeatureSubmitFeedback, FeatureValidationMessage } from '../../shared/FeatureSubmitFeedback';
import { buildProfileInput, cloneFormDefaults, type CloneFormValues } from './cloneFormDefaults';
import { jobProgressPercent } from '../../api/jobProgress';
import { GatewayErrorNotice } from '../../shared/GatewayErrorNotice';

export function VoiceCloningWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const appliedRevision = useRef('');
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
  const moduleDefaults = useMemo(() => {
    const defaults = voiceStudioDefaults(settingsQuery.data?.profile);
    return {
      providerId: defaults.voiceCloningProviderId,
      language: defaults.cloningLanguage,
      quality: defaults.cloningQuality,
    };
  }, [settingsQuery.data?.profile]);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<CloneFormValues>({
    defaultValues: cloneFormDefaults(moduleDefaults),
  });
  useEffect(() => {
    const revision = settingsQuery.data?.profile.revision;
    if (!revision || isDirty || appliedRevision.current === revision) return;
    reset(cloneFormDefaults(moduleDefaults));
    appliedRevision.current = revision;
  }, [isDirty, moduleDefaults, reset, settingsQuery.data?.profile.revision]);
  const providers = useMemo(() => voiceCloneCapableProviders(providersQuery.data), [providersQuery.data]);
  const sampleAssets = assetsQuery.data?.assets.filter((asset) => asset.type === 'voice_sample' || asset.type === 'audio') ?? [];
  const profileAssets = assetsQuery.data?.assets.filter((asset) => asset.type === 'voice_profile') ?? [];
  const voiceCloneJobs = jobsQuery.data?.jobs.filter((job) => job.module === 'voice-cloning') ?? [];
  const createJobMutation = useMutation({
    mutationFn: (values: CloneFormValues) =>
      omnixApiClient.createJob({
        module: 'voice-cloning',
        type: 'voice-cloning.train',
        resource_class: 'gpu:tts',
        priority: 0,
        input_ref: values.sampleAssetId ? { sample_asset_id: values.sampleAssetId } : null,
        input_payload: buildProfileInput(values, moduleDefaults),
        stages: [
          { id: 'ingest-sample', label: 'Ingest sample', resource_class: 'cpu', status: 'queued' },
          { id: 'build-profile', label: 'Build voice profile', resource_class: 'gpu:tts', status: 'queued' },
          { id: 'preview', label: 'Generate preview', resource_class: 'gpu:tts', status: 'queued' },
          { id: 'store-profile', label: 'Store voice profile asset', resource_class: 'cpu', status: 'queued' },
        ],
      }),
    onSuccess: async (_job, values) => {
      reset({ ...values, profileName: '', referenceText: '' });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
    },
  });
  const submitStatus = createJobMutation.isPending ? 'queueing' : createJobMutation.isError ? 'error' : createJobMutation.data?.status ?? 'ready';

  return (
    <WorkspacePanel>
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">Feature module</p>
          <h2 id="module-title">{module.label}</h2>
        </div>
        <code>{module.route}</code>
      </div>

      <p className="workspace-summary">{module.summary}</p>
      <GatewayErrorNotice label="Voice cloning data" errors={[providersQuery.error, jobsQuery.error, assetsQuery.error]} />

      <div className="feature-layout">
        <section className="feature-panel">
          <Group justify="space-between" align="start">
            <div>
              <Title order={4}>Voice profile</Title>
              <Text size="sm">Shared voice-cloning job queue</Text>
            </div>
            <OmnixStatusPill>{submitStatus}</OmnixStatusPill>
          </Group>

          <form className="feature-form" onSubmit={handleSubmit((values) => createJobMutation.mutate(values))}>
            <label>
              Provider
              <select {...register('providerId')}>
                <option value="">Default voice provider</option>
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Sample asset
              <select {...register('sampleAssetId')}>
                <option value="">No sample selected</option>
                {sampleAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.file_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Profile name
              <input aria-invalid={Boolean(errors.profileName)} {...register('profileName', { required: true })} placeholder="Narrator profile" />
            </label>
            <label>
              Language
              <input {...register('language')} placeholder="English" />
            </label>
            <label>
              Quality
              <select {...register('quality')}>
                <option value="High">High</option>
                <option value="Standard">Standard</option>
                <option value="Draft">Draft</option>
              </select>
            </label>
            <label className="feature-form-wide">
              Reference text
              <textarea rows={4} {...register('referenceText')} />
            </label>
            <Group className="feature-form-action" gap="xs">
              <Button type="button" variant="subtle" onClick={() => reset(cloneFormDefaults(moduleDefaults))}>Reset defaults</Button>
              <Button type="submit" disabled={createJobMutation.isPending} loading={createJobMutation.isPending}>
                {createJobMutation.isPending ? 'Queueing voice profile…' : 'Queue voice profile'}
              </Button>
            </Group>
          </form>

          <FeatureValidationMessage show={Boolean(errors.profileName)} message="Enter a profile name before queueing voice cloning." />
          <FeatureSubmitFeedback
            error={createJobMutation.error}
            errorPrefix="Voice profile request"
            isError={createJobMutation.isError}
            isPending={createJobMutation.isPending}
            jobId={createJobMutation.data?.id}
            pendingMessage="Queueing voice profile job…"
            successPrefix="Voice profile job queued"
          />

          <OmnixAudioControls label="voice profile preview" />
        </section>

        <section className="feature-panel">
          <Title order={4}>Voice profile jobs</Title>
          {voiceCloneJobs.length ? (
            <div className="feature-list">
              {voiceCloneJobs.map((job) => (
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
              No voice profile jobs queued.
            </div>
          )}
        </section>

        <section className="feature-panel feature-panel-wide">
          <Title order={4}>Voice profiles</Title>
          {profileAssets.length ? (
            <div className="platform-grid">
              {profileAssets.map((asset) => (
                <OmnixAssetCard key={asset.id} title={`${asset.type} / ${asset.module}`} metadata={asset.file_name} />
              ))}
            </div>
          ) : (
            <div className="platform-empty" role="status">
              No voice profiles indexed.
            </div>
          )}
        </section>
      </div>
    </WorkspacePanel>
  );
}

function voiceCloneCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.capabilities.includes('voice_cloning') || provider.capabilities.includes('tts')) ?? [];
}

