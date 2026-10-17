import type { components } from '../api/generated';
import type { DesktopCompanionRolloutStage } from '../../settings';
import { unwrap } from '../../../api/http';
import { api } from '../api/gateway';

export type DesktopCompanionEvaluationPayload = components['schemas']['DesktopCompanionEvaluationCreate'];

type AccumulatorIdentity = {
  runId: string;
  sessionId: string | null;
  exactCommitSha: string;
  appVersion: string;
  characterId: string;
  profileVersion: number | null;
  observationSchemaVersion: number;
  attentionPolicyVersion: number;
  rolloutStage: DesktopCompanionRolloutStage;
  visionProvider: string;
  visionModelHash: string | null;
  remoteProvider: boolean;
  startedAt?: Date;
};

export class DesktopCompanionEvaluationAccumulator {
  private readonly identity: AccumulatorIdentity;
  private readonly counts = new Map<string, number>();
  private readonly observationLatencies: number[] = [];
  private readonly commentaryLatencies: number[] = [];
  private readonly scenarios = new Set<string>();

  constructor(identity: AccumulatorIdentity) {
    this.identity = identity;
  }

  recordCapture(): void { this.increment('captures'); }
  recordMeaningfulChange(): void { this.increment('meaningful_changes'); }
  recordVisionRequest(input: { latencyMs?: number; callsThisMinute?: number; providerError?: boolean; stale?: boolean }): void {
    this.increment('vision_requests');
    if (input.latencyMs !== undefined && Number.isFinite(input.latencyMs)) this.observationLatencies.push(Math.max(0, input.latencyMs));
    if (input.providerError) this.increment('provider_errors');
    if (input.stale) this.increment('stale_outputs');
    if (input.callsThisMinute !== undefined) {
      this.counts.set('max_vision_calls_per_minute', Math.max(
        this.counts.get('max_vision_calls_per_minute') ?? 0,
        Math.max(0, Math.round(input.callsThisMinute)),
      ));
    }
  }
  recordObservation(): void { this.increment('observations'); }
  recordCoalesced(): void { this.increment('coalesced_requests'); }
  recordDropped(): void { this.increment('dropped_requests'); }
  recordCommentary(input: { latencyMs?: number; duplicate?: boolean; unsupportedClaim?: boolean; skipped?: boolean }): void {
    this.increment('commentary_candidates');
    if (input.latencyMs !== undefined && Number.isFinite(input.latencyMs)) this.commentaryLatencies.push(Math.max(0, input.latencyMs));
    if (input.duplicate) this.increment('duplicate_comments');
    if (input.unsupportedClaim) this.increment('unsupported_claims');
    if (input.skipped) this.increment('skipped_comments');
  }
  recordDelivery(input: { collision?: boolean; interrupted?: boolean }): void {
    this.increment('deliveries');
    if (input.collision) this.increment('collisions');
    if (input.interrupted) this.increment('interruptions');
  }
  addScenario(label: string): void {
    const normalized = label.trim().toLocaleLowerCase();
    if (/^[a-z0-9_.:-]{1,160}$/.test(normalized)) this.scenarios.add(normalized);
  }

  finalize(endedAt = new Date()): DesktopCompanionEvaluationPayload {
    const counts = Object.fromEntries(this.counts.entries());
    const visionRequests = counts.vision_requests ?? 0;
    const candidates = counts.commentary_candidates ?? 0;
    const deliveries = counts.deliveries ?? 0;
    return {
      run_id: this.identity.runId,
      session_id: this.identity.sessionId,
      started_at: (this.identity.startedAt ?? endedAt).toISOString(),
      ended_at: endedAt.toISOString(),
      exact_commit_sha: this.identity.exactCommitSha,
      app_version: this.identity.appVersion,
      browser_version: typeof navigator === 'undefined' ? 'unknown' : navigator.userAgent.slice(0, 240),
      os_version: typeof navigator === 'undefined' ? 'unknown' : navigator.platform.slice(0, 160),
      character_id: this.identity.characterId,
      profile_version: this.identity.profileVersion,
      observation_schema_version: this.identity.observationSchemaVersion,
      attention_policy_version: this.identity.attentionPolicyVersion,
      rollout_stage: this.identity.rolloutStage,
      vision_provider: this.identity.visionProvider,
      vision_model_hash: this.identity.visionModelHash,
      remote_provider: this.identity.remoteProvider,
      counts,
      latency_ms: {
        observation_p50: percentile(this.observationLatencies, 0.5),
        observation_p95: percentile(this.observationLatencies, 0.95),
        commentary_p50: percentile(this.commentaryLatencies, 0.5),
        commentary_p95: percentile(this.commentaryLatencies, 0.95),
      },
      rates: {
        stale_output_rate: rate(counts.stale_outputs, visionRequests),
        duplicate_comment_rate: rate(counts.duplicate_comments, candidates),
        unsupported_claim_rate: rate(counts.unsupported_claims, candidates),
        collision_rate: rate(counts.collisions, deliveries),
        provider_error_rate: rate(counts.provider_errors, visionRequests),
      },
      scenario_labels: [...this.scenarios].sort(),
    };
  }

  private increment(key: string): void {
    this.counts.set(key, (this.counts.get(key) ?? 0) + 1);
  }
}

export async function submitDesktopCompanionEvaluation(payload: DesktopCompanionEvaluationPayload): Promise<void> {
  await unwrap(api.POST('/api/desktop-companion/evaluations', { body: payload }));
}

function rate(numerator: number | undefined, denominator: number): number | null {
  return denominator > 0 ? Math.max(0, numerator ?? 0) / denominator : null;
}

function percentile(values: number[], fraction: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * fraction) - 1));
  return Number((sorted[index] ?? 0).toFixed(3));
}
