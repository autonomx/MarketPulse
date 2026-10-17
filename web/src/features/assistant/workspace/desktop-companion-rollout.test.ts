import { afterEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_SETTINGS_DOCUMENT } from '../../settings';
import {
  effectiveDesktopCompanionSettings,
  fetchDesktopCompanionRolloutStatus,
} from './desktop-companion-rollout';

afterEach(() => vi.unstubAllGlobals());

describe('desktop companion rollout settings', () => {
  it('keeps the runtime disabled even if a stage is selected without the enable switch', () => {
    const settings = {
      ...DEFAULT_SETTINGS_DOCUMENT.assistant,
      desktopCompanionEnabled: false,
      desktopCompanionRolloutStage: 'speech' as const,
      autoSpeakReplies: true,
    };

    expect(effectiveDesktopCompanionSettings(settings)).toMatchObject({
      requestedStage: 'disabled',
      enabled: false,
      shadowMode: false,
      textEnabled: false,
      speechEnabled: false,
    });
  });

  it('requires auto-speak before the configured speech setting becomes active', () => {
    const base = {
      ...DEFAULT_SETTINGS_DOCUMENT.assistant,
      desktopCompanionEnabled: true,
      desktopCompanionRolloutStage: 'speech' as const,
    };

    expect(effectiveDesktopCompanionSettings({ ...base, autoSpeakReplies: false }).speechEnabled).toBe(false);
    expect(effectiveDesktopCompanionSettings({ ...base, autoSpeakReplies: true }).speechEnabled).toBe(true);
  });

  it('binds rollout resolution to exact evidence identity', async () => {
    let requestedUrl = '';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      requestedUrl = String(input);
      return new Response(JSON.stringify({
        requested_stage: 'text',
        effective_stage: 'shadow',
        enabled: true,
        reason: 'release_gate_requires_shadow',
        release_gate_status: 'insufficient',
        evidence_evaluation_ids: [],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    await fetchDesktopCompanionRolloutStatus('text', {
      exactCommitSha: 'abcdef0123456789',
      observationSchemaVersion: 1,
      attentionPolicyVersion: 2,
      visionProvider: 'openai-compatible-local',
      visionModelHash: 'model-hash',
      remoteProvider: false,
    });

    const url = new URL(requestedUrl, 'http://localhost');
    expect(url.searchParams.get('requested_stage')).toBe('text');
    expect(url.searchParams.get('exact_commit_sha')).toBe('abcdef0123456789');
    expect(url.searchParams.get('attention_policy_version')).toBe('2');
    expect(url.searchParams.get('vision_model_hash')).toBe('model-hash');
    expect(url.searchParams.get('remote_provider')).toBe('false');
  });
});
