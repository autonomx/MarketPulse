/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { Button, Group, Progress, Switch, Text, Title } from '@mantine/core';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import type {
  AssetListResponse,
  DiagnosticsPayload,
  JobListResponse,
  JobRecord,
  ProviderFacadePayload,
  ReportListResponse,
  SettingsPayload,
} from '../../api/client';
import { omnixApiClient } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixAssetCard, OmnixDiagnosticsView, OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import type { OmnixEventConnectionStatus } from '../../events/eventClient';
import { useEventConnectionStatus, useJobEventRefresh } from '../../events/useJobEvents';
import { jobProgressPercent } from '../../api/jobProgress';

const jobsEventQueryKeys: QueryKey[] = [['platform', 'jobs'], ['platform', 'diagnostics']];
const diagnosticsEventQueryKeys: QueryKey[] = [['platform', 'diagnostics'], ['platform', 'jobs']];
const artifactEventQueryKeys: QueryKey[] = [['platform', 'assets'], ['platform', 'reports']];
// Platform lists load one cursor page at a time (WP-5.5) instead of every row.
const JOB_PAGE_SIZE = 50;
const ASSET_PAGE_SIZE = 60;

const providerModelEventQueryKeys: QueryKey[] = [
  ['platform', 'providers'],
  ['platform', 'models'],
  ['platform', 'jobs'],
  ['platform', 'diagnostics'],
];

export { isPlatformModule } from '../../app/modules';

export function PlatformModuleWorkspace({ module }: { module: OmnixModuleDefinition }) {
  return (
    <WorkspacePanel labelledBy="module-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">Platform module</p>
          <h2 id="module-title">{module.label}</h2>
        </div>
        <code>{module.route}</code>
      </div>

      <p className="workspace-summary">{module.summary}</p>

      {module.id === 'providers' ? <ProvidersView /> : null}
      {module.id === 'models' ? <ModelsView /> : null}
      {module.id === 'jobs' ? <JobsView /> : null}
      {module.id === 'assets' ? <AssetsView /> : null}
      {module.id === 'reports' ? <ReportsView /> : null}
      {module.id === 'settings' ? <SettingsView /> : null}
      {module.id === 'diagnostics' ? <DiagnosticsView /> : null}
    </WorkspacePanel>
  );
}

function ProvidersView() {
  const queryClient = useQueryClient();
  useJobEventRefresh(providerModelEventQueryKeys);
  const query = useQuery({
    queryKey: ['platform', 'providers'],
    queryFn: () => omnixApiClient.listProviders(),
  });
  const refreshMutation = useMutation({
    mutationFn: () => omnixApiClient.refreshProviders({ scope: 'providers', reason: 'web.providers.refresh', priority: 0 }),
    onSuccess: async () => {
      for (const queryKey of providerModelEventQueryKeys) {
        await queryClient.invalidateQueries({ queryKey });
      }
    },
  });

  return (
    <>
      <section className="platform-section">
        <Group justify="space-between" align="center">
          <Title order={4}>Registry refresh</Title>
          <Button size="xs" variant="light" disabled={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>
            Refresh
          </Button>
        </Group>
        <DetailList rows={[['Last request', refreshMutation.isError ? refreshMutation.error.message : refreshMutation.isSuccess ? 'queued' : 'idle']]} />
      </section>
      <QueryState query={query} empty={!query.data?.providers.length} emptyText="No providers returned by gateway.">
        {(data) => (
          <div className="platform-grid">
            {data.providers.map((provider) => (
              <section className="platform-section" key={provider.id}>
                <Group justify="space-between" align="start">
                  <div>
                    <Title order={4}>{provider.label}</Title>
                    <Text size="sm">{provider.source}</Text>
                  </div>
                  <OmnixStatusPill>{provider.status}</OmnixStatusPill>
                </Group>
                <DetailList
                  rows={[
                    ['Family', provider.family],
                    ['Capabilities', provider.capabilities.join(', ') || 'none'],
                    ['Latency', metadataValue(provider.metadata, 'latency_ms')],
                    ['Errors', metadataValue(provider.metadata, 'last_error')],
                  ]}
                />
              </section>
            ))}
          </div>
        )}
      </QueryState>
    </>
  );
}

function ModelsView() {
  const queryClient = useQueryClient();
  useJobEventRefresh(providerModelEventQueryKeys);
  const query = useQuery({
    queryKey: ['platform', 'models'],
    queryFn: () => omnixApiClient.listModels(),
  });
  const refreshMutation = useMutation({
    mutationFn: () => omnixApiClient.refreshModels({ scope: 'models', reason: 'web.models.refresh', priority: 0 }),
    onSuccess: async () => {
      for (const queryKey of providerModelEventQueryKeys) {
        await queryClient.invalidateQueries({ queryKey });
      }
    },
  });

  return (
    <>
      <section className="platform-section">
        <Group justify="space-between" align="center">
          <Title order={4}>Model refresh</Title>
          <Button size="xs" variant="light" disabled={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>
            Refresh
          </Button>
        </Group>
        <DetailList rows={[['Last request', refreshMutation.isError ? refreshMutation.error.message : refreshMutation.isSuccess ? 'queued' : 'idle']]} />
      </section>
      <QueryState query={query} empty={!query.data?.models.length} emptyText="No models returned by gateway.">
        {(data) => (
          <div className="platform-grid">
            {data.models.map((model) => (
              <section className="platform-section" key={model.id}>
                <Group justify="space-between" align="start">
                  <div>
                    <Title order={4}>{model.label}</Title>
                    <Text size="sm">{model.provider_id}</Text>
                  </div>
                  <OmnixStatusPill>{model.location}</OmnixStatusPill>
                </Group>
                <DetailList
                  rows={[
                    ['Capabilities', model.capabilities.join(', ') || 'none'],
                    ['VRAM hint', model.vram_hint_mb ? `${model.vram_hint_mb} MB` : 'unknown'],
                    ['Default for', metadataValue(model.metadata, 'default_for')],
                  ]}
                />
              </section>
            ))}
          </div>
        )}
      </QueryState>
    </>
  );
}

function JobsView() {
  const queryClient = useQueryClient();
  const eventStatus = useEventConnectionStatus();
  useJobEventRefresh(jobsEventQueryKeys);
  const query = useInfiniteQuery({
    // Other workspaces cache the plain jobs list under ['platform', 'jobs']; invalidating that prefix refreshes these pages too.
    queryKey: ['platform', 'jobs', 'pages'],
    queryFn: ({ pageParam }) => omnixApiClient.listJobs({ limit: JOB_PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: nextCursor,
  });
  const jobs = query.data?.pages.flatMap((page) => page.jobs) ?? [];
  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => omnixApiClient.cancelJob(jobId, 'Canceled from Omnix web Jobs module'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
  });

  return (
    <>
      <section className="platform-section">
        <Title order={4}>Live updates</Title>
        <DetailList rows={eventStatusRows(eventStatus)} />
      </section>
      <QueryState query={query} empty={!jobs.length} emptyText="No jobs in the shared queue.">
        {() => (
          <div className="platform-list">
            {jobs.map((job) => {
              const progressValue = jobProgressPercent(job.progress);
              const canCancel = ['queued', 'leased', 'running', 'waiting', 'retrying'].includes(job.status);

              return (
                <section className="platform-section" key={job.id}>
                  <Group justify="space-between" align="start">
                    <div>
                      <Title order={4}>{job.type}</Title>
                      <Text size="sm">{job.module}</Text>
                    </div>
                    <Group gap="xs">
                      <OmnixStatusPill>{job.status}</OmnixStatusPill>
                      <Button
                        size="xs"
                        variant="light"
                        disabled={!canCancel || cancelMutation.isPending}
                        onClick={() => cancelMutation.mutate(job.id)}
                      >
                        Cancel
                      </Button>
                    </Group>
                  </Group>
                  <Progress value={progressValue} aria-label={`${job.type} progress`} />
                  <DetailList
                    rows={[
                      ['Resource lock', job.resource_class],
                      ['Progress', job.progress?.message ?? `${progressValue}%`],
                      ['Stages', job.stages?.map((stage) => `${stage.label}: ${stage.status}`).join(', ') || 'none'],
                      ['Logs', job.logs?.length ? `${job.logs.length}` : 'none'],
                    ]}
                  />
                </section>
              );
            })}
            <LoadMore query={query} label="jobs" />
          </div>
        )}
      </QueryState>
    </>
  );
}

function AssetsView() {
  useJobEventRefresh(artifactEventQueryKeys);
  const query = useInfiniteQuery({
    // Other workspaces cache the plain assets list under ['platform', 'assets']; invalidating that prefix refreshes these pages too.
    queryKey: ['platform', 'assets', 'pages'],
    queryFn: ({ pageParam }) => omnixApiClient.listAssetPage({ limit: ASSET_PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: nextCursor,
  });
  const assets = query.data?.pages.flatMap((page) => page.assets) ?? [];

  return (
    <QueryState query={query} empty={!assets.length} emptyText="No assets indexed in the shared library.">
      {() => (
        <>
          <div className="platform-grid">
            {assets.map((asset) => (
              <OmnixAssetCard
                key={asset.id}
                title={`${asset.type} / ${asset.module}`}
                metadata={`${asset.mime_type} - ${asset.file_name}`}
              />
            ))}
          </div>
          <LoadMore query={query} label="assets" />
        </>
      )}
    </QueryState>
  );
}

function ReportsView() {
  useJobEventRefresh(artifactEventQueryKeys);
  const query = useQuery({
    queryKey: ['platform', 'reports'],
    queryFn: () => omnixApiClient.listReports(),
  });

  return (
    <QueryState query={query} empty={!query.data?.reports?.length} emptyText="No generated reports found.">
      {(data) => (
        <div className="platform-grid">
          {(data.reports ?? []).map((report) => (
            <section className="platform-section" key={report.id}>
              <Group justify="space-between" align="start">
                <div>
                  <Title order={4}>{report.id}</Title>
                  <Text size="sm">{report.path}</Text>
                </div>
                <OmnixStatusPill>{report.kind}</OmnixStatusPill>
              </Group>
              <DetailList rows={[['Size', `${report.size_bytes} bytes`]]} />
            </section>
          ))}
        </div>
      )}
    </QueryState>
  );
}

function SettingsView() {
  const queryClient = useQueryClient();
  const [llmProvider, setLlmProvider] = useState('lmstudio');
  const [ttsProvider, setTtsProvider] = useState('faster-qwen3-tts');
  const [sttProvider, setSttProvider] = useState('parakeet');
  const query = useQuery({
    queryKey: ['platform', 'settings'],
    queryFn: () => omnixApiClient.getSettings(),
  });
  const providersQuery = useQuery({
    queryKey: ['platform', 'providers'],
    queryFn: () => omnixApiClient.listProviders(),
  });
  const saveMutation = useMutation({
    mutationFn: () => {
      const values = {
        provider: llmProvider,
        audio_provider_tts: ttsProvider,
        audio_provider_stt: sttProvider,
      };
      // The gateway refuses a save whose revisions are stale (another tab or
      // process changed the setting); the form then reloads.
      const revisions = Object.fromEntries(
        Object.keys(values).map((key) => [key, query.data?.revisions?.[key] ?? 0]),
      );
      return omnixApiClient.saveSettings({ values, revisions });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['platform', 'settings'] });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'providers'] });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'models'] });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'diagnostics'] });
    },
  });

  useEffect(() => {
    if (query.data) {
      setLlmProvider(query.data.provider || 'lmstudio');
      setTtsProvider(query.data.audio_provider_tts || 'faster-qwen3-tts');
      setSttProvider(query.data.audio_provider_stt || 'parakeet');
    }
  }, [query.data]);

  const llmOptions = providerOptions(providersQuery.data, 'llm', ['lmstudio', 'openrouter', 'cerebras', 'llamacpp']);
  const ttsOptions = providerOptions(providersQuery.data, 'tts', ['faster-qwen3-tts']);
  const sttOptions = providerOptions(providersQuery.data, 'stt', ['parakeet']);

  return (
    <QueryState query={query} empty={!query.data} emptyText="Settings payload is unavailable.">
      {(data) => (
        <div className="platform-grid">
          <section className="platform-section platform-section-wide">
            <Title order={4}>Provider settings</Title>
            <Text size="sm">Choose the same default provider families used by the legacy app, including LM Studio and remote LLM providers.</Text>
            <form
              className="provider-settings-form"
              onSubmit={(event) => {
                event.preventDefault();
                saveMutation.mutate();
              }}
            >
              <div className="provider-settings-grid">
                <label>
                  LLM provider
                  <select aria-label="LLM provider" value={llmProvider} onChange={(event) => setLlmProvider(event.target.value)}>
                    {llmOptions.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  TTS provider
                  <select aria-label="TTS provider" value={ttsProvider} onChange={(event) => setTtsProvider(event.target.value)}>
                    {ttsOptions.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  STT provider
                  <select aria-label="STT provider" value={sttProvider} onChange={(event) => setSttProvider(event.target.value)}>
                    {sttOptions.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="provider-settings-actions">
                {saveMutation.isError ? <Text role="alert">Provider settings failed to save: {saveMutation.error.message}</Text> : null}
                {saveMutation.isSuccess ? <Text role="status">Provider settings saved.</Text> : null}
                <Button type="submit" variant="light" loading={saveMutation.isPending} disabled={saveMutation.isPending}>
                  Save provider defaults
                </Button>
              </div>
            </form>
          </section>
          <section className="platform-section">
            <Title order={4}>Current defaults</Title>
            <DetailList
              rows={[
                ['LLM provider', data.provider],
                ['TTS provider', data.audio_provider_tts],
                ['STT provider', data.audio_provider_stt],
              ]}
            />
          </section>
          <section className="platform-section">
            <Title order={4}>Provider configuration</Title>
            <div className="provider-config-list">
              {['lmstudio', 'openrouter', 'cerebras', 'llamacpp'].map((providerId) => (
                <article className="provider-config-card" key={providerId}>
                  <Title order={5}>{providerLabel(providerId)}</Title>
                  <DetailList rows={providerSettingsRows(data.settings, providerId)} />
                </article>
              ))}
            </div>
          </section>
          <section className="platform-section">
            <Title order={4}>Local services</Title>
            <div className="platform-switches">
              <Switch label="Image generation" checked={data.image_enabled} readOnly />
              <Switch label="RPG visuals" checked={data.rpg_visual_enabled} readOnly />
            </div>
            <DetailList rows={Object.entries(data.worker_urls ?? {}).map(([key, value]) => [key, value])} />
          </section>
        </div>
      )}
    </QueryState>
  );
}

function DiagnosticsView() {
  const eventStatus = useEventConnectionStatus();
  useJobEventRefresh(diagnosticsEventQueryKeys);
  const query = useQuery({
    queryKey: ['platform', 'diagnostics'],
    queryFn: () => omnixApiClient.getDiagnostics(),
  });

  return (
    <QueryState query={query} empty={!query.data} emptyText="Diagnostics payload is unavailable.">
      {(data) => {
        const workerRows =
          data.workers.workers?.map((worker) => ({
            label: worker.id,
            value: worker.ok ? worker.status : worker.error ?? worker.status,
          })) ?? [];

        return (
          <div className="platform-grid">
            <section className="platform-section">
              <Title order={4}>Gateway status</Title>
              <OmnixDiagnosticsView
                rows={[
                  { label: 'Gateway', value: data.status },
                  { label: 'Workers', value: data.workers.status },
                  { label: 'Event stream', value: eventStatusText(eventStatus) },
                  { label: 'Diagnostics payload', value: data.event_stream?.status ?? 'unknown' },
                  { label: 'Model residency', value: data.model_residency?.status ?? 'unknown' },
                  {
                    label: 'Co-residency',
                    value: data.model_residency?.policy?.allow_co_residency ? 'enabled' : 'conservative',
                  },
                  { label: 'Provider cache', value: data.provider_model_cache?.status ?? 'unknown' },
                ]}
              />
            </section>
            <section className="platform-section">
              <Title order={4}>Worker health</Title>
              {workerRows.length ? <OmnixDiagnosticsView rows={workerRows} /> : <EmptyState text="No workers configured." />}
            </section>
            <section className="platform-section platform-section-wide">
              <Title order={4}>Logs</Title>
              <div className="platform-log">
                {(data.logs ?? []).length ? data.logs?.map((log, index) => <code key={index}>{stringifyUnknown(log)}</code>) : <span>none</span>}
              </div>
            </section>
          </div>
        );
      }}
    </QueryState>
  );
}

type PagedQuery = {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => Promise<unknown>;
};

function nextCursor(page: { next_cursor?: string | null; has_more?: boolean }): string | undefined {
  return page.has_more && page.next_cursor ? page.next_cursor : undefined;
}

function LoadMore({ query, label }: { query: PagedQuery; label: string }) {
  if (!query.hasNextPage) return null;
  return (
    <Button variant="light" size="xs" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
      {query.isFetchingNextPage ? `Loading more ${label}…` : `Load more ${label}`}
    </Button>
  );
}

function QueryState<T>({
  query,
  empty,
  emptyText,
  children,
}: {
  query: { isLoading: boolean; isError: boolean; error: Error | null; data: T | undefined };
  empty: boolean;
  emptyText: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isLoading) {
    return <EmptyState text="Loading platform data." />;
  }

  if (query.isError) {
    return <EmptyState text={`Gateway request failed: ${query.error?.message ?? 'unknown error'}`} />;
  }

  if (!query.data || empty) {
    return <EmptyState text={emptyText} />;
  }

  return <>{children(query.data)}</>;
}

function DetailList({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="platform-details">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="platform-empty" role="status">
      {text}
    </div>
  );
}

function eventStatusRows(status: OmnixEventConnectionStatus): Array<[string, string]> {
  return [
    ['Event stream', eventStatusText(status)],
    ['Retry attempt', `${status.reconnectAttempt}`],
    ['Next retry', status.nextReconnectDelayMs ? `${status.nextReconnectDelayMs} ms` : 'none'],
  ];
}

function eventStatusText(status: OmnixEventConnectionStatus): string {
  return status.lastError ? `${status.state} (${status.lastError})` : status.state;
}


function providerOptions(payload: ProviderFacadePayload | undefined, family: 'llm' | 'tts' | 'stt', fallbackIds: string[]) {
  const options = new Map<string, { id: string; label: string }>();

  for (const id of fallbackIds) {
    options.set(id, { id, label: providerLabel(id) });
  }

  for (const provider of payload?.providers ?? []) {
    if (providerMatchesFamily(provider, family)) {
      options.set(provider.id, { id: provider.id, label: provider.label || providerLabel(provider.id) });
    }
  }

  return Array.from(options.values());
}

function providerMatchesFamily(provider: ProviderFacadePayload['providers'][number], family: 'llm' | 'tts' | 'stt'): boolean {
  if (family === 'llm') {
    return provider.family === 'llm' || provider.capabilities.includes('chat');
  }

  if (family === 'tts') {
    return provider.family === 'tts' || provider.capabilities.includes('tts') || provider.id.includes('tts');
  }

  return provider.family === 'stt' || provider.capabilities.includes('stt') || provider.id.includes('stt') || provider.id === 'parakeet';
}

function providerLabel(providerId: string): string {
  const labels: Record<string, string> = {
    lmstudio: 'LM Studio',
    openrouter: 'OpenRouter',
    cerebras: 'Cerebras',
    llamacpp: 'llama.cpp',
    'faster-qwen3-tts': 'Faster Qwen3 TTS',
    parakeet: 'Parakeet STT',
  };

  return labels[providerId] ?? providerId;
}

function providerSettingsRows(settings: SettingsPayload['settings'] | undefined, providerId: string): Array<[string, string]> {
  const value = settings?.[providerId];

  if (!isRecord(value)) {
    return [['Status', 'not configured']];
  }

  const rows = Object.entries(value)
    .filter(([key]) => key !== 'api_key')
    .slice(0, 5)
    .map(([key, item]) => [key, stringifyUnknown(item)] as [string, string]);

  return rows.length ? rows : [['Status', 'configured']];
}

function metadataValue(metadata: Record<string, unknown> | undefined, key: string): string {
  const value = metadata?.[key];

  if (value === undefined || value === null || value === '') {
    return 'unknown';
  }

  return typeof value === 'string' ? value : stringifyUnknown(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
