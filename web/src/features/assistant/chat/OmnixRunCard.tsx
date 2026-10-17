import { useQuery } from '@tanstack/react-query';
import { HtmlArtifactPreviews } from './HtmlArtifactPreview';
import { OmnixRunCard as OmnixRunCardCore } from './OmnixRunCardCore';
import './OmnixRunCardQuality.css';
import { assistantApiClient } from '../api/assistantClient';

type Metadata = Record<string, unknown>;

type CodingLifecycleStage = 'starting' | 'pi_working' | 'acceptance' | 'completed';

type QualityAwareAgentRun = {
  status?: unknown;
  quality_stage?: unknown;
  quality_attempt?: unknown;
  workspace_state_id?: unknown;
  spec?: {
    profile?: unknown;
    quality_policy?: unknown;
  };
};

const CODING_LIFECYCLE_STAGES: Array<{ id: CodingLifecycleStage; label: string }> = [
  { id: 'starting', label: 'Starting' },
  { id: 'pi_working', label: 'Pi working' },
  { id: 'acceptance', label: 'Acceptance' },
  { id: 'completed', label: 'Completed' },
];

function asRecord(value: unknown): Metadata | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Metadata : null;
}

function initialAgentRunId(metadata?: Metadata): string {
  const agent = asRecord(metadata?.agent_run);
  return typeof agent?.run_id === 'string' ? agent.run_id : '';
}

function lifecycleStage(run: QualityAwareAgentRun | undefined): CodingLifecycleStage {
  const status = String(run?.status ?? 'starting');
  if (status === 'completed') return 'completed';
  if (run?.quality_stage === 'acceptance') return 'acceptance';
  if (status === 'queued' || status === 'starting') return 'starting';
  return 'pi_working';
}

function stageLabel(stage: CodingLifecycleStage): string {
  if (stage === 'starting') return 'Starting coding agent';
  if (stage === 'acceptance') return 'Omnix final acceptance';
  if (stage === 'completed') return 'Completed';
  return 'Pi working';
}

function artifactHtmlPaths(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const artifacts = value.map(asRecord).filter((item): item is Metadata => Boolean(item));
  const diff = [...artifacts].reverse().find((item) => item.kind === 'diff');
  const metadata = asRecord(diff?.metadata);
  const paths = new Set<string>();
  if (Array.isArray(metadata?.file_stats)) {
    metadata.file_stats
      .map(asRecord)
      .filter((item): item is Metadata => Boolean(item))
      .forEach((item) => {
        if (typeof item.path === 'string' && item.path.trim()) paths.add(item.path.trim());
      });
  }
  if (!paths.size && typeof metadata?.preview === 'string') {
    metadata.preview.split(/\r?\n/).forEach((line) => {
      const match = line.match(/^\+\+\+ b\/(.+)$/) ?? line.match(/^diff --git a\/.+ b\/(.+)$/);
      if (match?.[1]) paths.add(match[1].trim());
    });
  }
  return [...paths].filter((path) => /\.html?$/i.test(path));
}

function CodingLifecycle({ stage }: { stage: CodingLifecycleStage }) {
  const currentIndex = CODING_LIFECYCLE_STAGES.findIndex((item) => item.id === stage);
  return (
    <section
      className="assistant-runtime-quality"
      data-quality-stage={stage}
      aria-label="Coding run lifecycle"
    >
      <div className="assistant-runtime-quality-heading">
        <strong>{stageLabel(stage)}</strong>
        <span>Pi-native mode</span>
      </div>
      <ol className="assistant-runtime-quality-steps">
        {CODING_LIFECYCLE_STAGES.map((item, index) => {
          const state = index < currentIndex
            ? 'complete'
            : index === currentIndex
              ? 'active'
              : 'pending';
          return (
            <li data-state={state} key={item.id}>
              <span aria-hidden="true">{state === 'complete' ? '✓' : state === 'active' ? '●' : '○'}</span>
              {item.label}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function OmnixRunCard({ metadata }: { metadata?: Metadata }) {
  const id = initialAgentRunId(metadata);
  const query = useQuery({
    queryKey: ['agent-run', id],
    queryFn: () => assistantApiClient.getAgentRun(id),
    enabled: Boolean(id),
    refetchInterval: (state) => {
      const run = state.state.data as QualityAwareAgentRun | undefined;
      const status = String(run?.status ?? '');
      return ['completed', 'failed', 'cancelled'].includes(status) ? false : 1500;
    },
  });
  // AgentRunSnapshot is intentionally hand-written in the legacy web client and
  // can lag the server OpenAPI model. Treat the quality fields as a typed
  // additive extension so the run card remains compatible while the generated
  // contract catches up.
  const run = query.data as QualityAwareAgentRun | undefined;
  const stage = lifecycleStage(run);
  const profile = String(run?.spec?.profile ?? '');
  const status = String(run?.status ?? '');
  const artifacts = useQuery({
    queryKey: ['agent-run', id, 'artifacts', 'html-previews'],
    queryFn: () => assistantApiClient.listAgentArtifacts(id),
    enabled: Boolean(id) && status === 'completed' && profile === 'coding',
  });
  const htmlPaths = artifactHtmlPaths(artifacts.data);

  return (
    <>
      {id && profile === 'coding' ? (
        <CodingLifecycle stage={stage} />
      ) : null}
      <OmnixRunCardCore metadata={metadata} />
      {id && status === 'completed' && profile === 'coding' ? (
        <HtmlArtifactPreviews runId={id} paths={htmlPaths} />
      ) : null}
    </>
  );
}
