import type { AssistantSettings, DesktopCompanionRolloutStage } from '../../settings';
import type { components } from '../api/generated';
import { unwrap } from '../../../api/http';
import { api } from '../api/gateway';

export type DesktopCompanionRolloutStatus = components['schemas']['DesktopCompanionRolloutStatus'];

export type DesktopCompanionRolloutEvidenceIdentity = {
  exactCommitSha: string;
  observationSchemaVersion?: number;
  attentionPolicyVersion?: number;
  visionProvider?: string | null;
  visionModelHash?: string | null;
  remoteProvider?: boolean;
};

export type EffectiveDesktopCompanionSettings = {
  requestedStage: DesktopCompanionRolloutStage;
  enabled: boolean;
  shadowMode: boolean;
  textEnabled: boolean;
  speechEnabled: boolean;
  visionModelId: string;
  remoteVisionAllowed: boolean;
  showDiagnostics: boolean;
  backgroundCallsPerMinute: number;
  minimumObservationIntervalMs: number;
  observationTimeoutMs: number;
  observationTtlMs: number;
  commentaryCooldownMs: number;
  minimumChangeConfidence: number;
};

export function effectiveDesktopCompanionSettings(value: AssistantSettings): EffectiveDesktopCompanionSettings {
  const requestedStage = value.desktopCompanionEnabled ? value.desktopCompanionRolloutStage : 'disabled';
  return {
    requestedStage,
    enabled: requestedStage !== 'disabled',
    shadowMode: requestedStage === 'shadow',
    textEnabled: requestedStage === 'text' || requestedStage === 'speech',
    speechEnabled: requestedStage === 'speech' && value.autoSpeakReplies,
    visionModelId: value.desktopCompanionVisionModelId.trim(),
    remoteVisionAllowed: value.desktopCompanionRemoteVisionAllowed,
    showDiagnostics: value.desktopCompanionShowDiagnostics,
    backgroundCallsPerMinute: clampInt(value.desktopCompanionBackgroundCallsPerMinute, 1, 30),
    minimumObservationIntervalMs: clampInt(value.desktopCompanionMinimumObservationIntervalMs, 2_000, 120_000),
    observationTimeoutMs: clampInt(value.desktopCompanionObservationTimeoutMs, 1_000, 60_000),
    observationTtlMs: clampInt(value.desktopCompanionObservationTtlMs, 2_000, 120_000),
    commentaryCooldownMs: clampInt(value.desktopCompanionCommentaryCooldownMs, 5_000, 300_000),
    minimumChangeConfidence: clamp(value.desktopCompanionMinimumChangeConfidence, 0, 1),
  };
}

export async function fetchDesktopCompanionRolloutStatus(
  stage: DesktopCompanionRolloutStage,
  identity?: DesktopCompanionRolloutEvidenceIdentity,
  signal?: AbortSignal,
): Promise<DesktopCompanionRolloutStatus> {
  const evidence = identity ? {
    exact_commit_sha: identity.exactCommitSha,
    observation_schema_version: identity.observationSchemaVersion ?? 1,
    attention_policy_version: identity.attentionPolicyVersion ?? 1,
    ...(identity.visionProvider ? { vision_provider: identity.visionProvider } : {}),
    ...(identity.visionModelHash ? { vision_model_hash: identity.visionModelHash } : {}),
    remote_provider: identity.remoteProvider === true,
  } : {};
  return unwrap(api.GET('/api/desktop-companion/rollout-status', {
    params: { query: { requested_stage: stage, ...evidence } },
    signal,
  }));
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? value : minimum));
}

function clampInt(value: number, minimum: number, maximum: number): number {
  return Math.round(clamp(value, minimum, maximum));
}
