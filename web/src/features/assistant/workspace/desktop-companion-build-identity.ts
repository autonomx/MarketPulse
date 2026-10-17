import type { DesktopCompanionRolloutEvidenceIdentity } from './desktop-companion-rollout';
import { unwrap } from '../../../api/http';
import { api } from '../api/gateway';

type BuildIdentity = {
  exact_commit_sha: string;
  app_version: string;
  source: string;
};

let buildIdentityPromise: Promise<BuildIdentity> | null = null;

export async function desktopCompanionRolloutEvidenceIdentity(input: {
  modelId: string | null;
  remoteProvider: boolean;
}): Promise<DesktopCompanionRolloutEvidenceIdentity> {
  const build = await loadDesktopCompanionBuildIdentity();
  return {
    exactCommitSha: build.exact_commit_sha,
    observationSchemaVersion: 1,
    attentionPolicyVersion: 1,
    visionProvider: input.remoteProvider ? 'openai-compatible-remote' : 'openai-compatible-local',
    visionModelHash: input.modelId ? await hashDesktopCompanionModelId(input.modelId) : null,
    remoteProvider: input.remoteProvider,
  };
}

export function loadDesktopCompanionBuildIdentity(): Promise<BuildIdentity> {
  if (!buildIdentityPromise) {
    buildIdentityPromise = unwrap(api.GET('/api/desktop-companion/build-identity'))
      .catch(() => ({ exact_commit_sha: 'unknown-local-build', app_version: '1.0.0', source: 'browser-fallback' }));
  }
  return buildIdentityPromise;
}

export async function hashDesktopCompanionModelId(value: string): Promise<string | null> {
  if (!crypto.subtle) return null;
  const bytes = new TextEncoder().encode(value.trim());
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, '0')).join('');
}
