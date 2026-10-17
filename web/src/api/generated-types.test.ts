import { describe, expect, it } from 'vitest';
import type { GatewayApiPaths } from './client';

describe('generated gateway API types', () => {
  it('exposes the current gateway paths through the shared import convention', () => {
    const expectedWorkerPath = '/api/workers/health' satisfies keyof GatewayApiPaths;
    const expectedChatSessionsPath = '/api/chat/sessions' satisfies keyof GatewayApiPaths;
    const expectedChatMessagesPath = '/api/chat/sessions/{session_id}/messages' satisfies keyof GatewayApiPaths;
    const expectedLegacySessionsPath = '/api/sessions' satisfies keyof GatewayApiPaths;
    const expectedLegacySessionPath = '/api/sessions/{session_id}' satisfies keyof GatewayApiPaths;
    const expectedJobsPath = '/api/jobs' satisfies keyof GatewayApiPaths;
    const expectedProvidersPath = '/api/providers' satisfies keyof GatewayApiPaths;
    const expectedCodexAuthPath = '/api/providers/chatgpt-codex/auth' satisfies keyof GatewayApiPaths;
    const expectedCodexLoginPath = '/api/providers/chatgpt-codex/login' satisfies keyof GatewayApiPaths;
    const expectedProvidersRefreshPath = '/api/providers/refresh' satisfies keyof GatewayApiPaths;
    const expectedModelsRefreshPath = '/api/models/refresh' satisfies keyof GatewayApiPaths;
    const expectedModelResidencyPath = '/api/model-residency' satisfies keyof GatewayApiPaths;
    const expectedModelResidencyDeletePath = '/api/model-residency/{model_id}' satisfies keyof GatewayApiPaths;
    const expectedAssetsPath = '/api/assets' satisfies keyof GatewayApiPaths;
    const expectedLegacyAssetDryRunPath =
      '/api/assets/migrations/legacy-non-image/dry-run' satisfies keyof GatewayApiPaths;
    const expectedPromptsPath = '/api/prompts/render' satisfies keyof GatewayApiPaths;
    const expectedReplayPath = '/api/replay/primitives' satisfies keyof GatewayApiPaths;
    const expectedSettingsPath = '/api/settings' satisfies keyof GatewayApiPaths;
    const expectedReportsPath = '/api/reports' satisfies keyof GatewayApiPaths;
    const expectedDiagnosticsPath = '/api/diagnostics' satisfies keyof GatewayApiPaths;

    expect(expectedWorkerPath).toBe('/api/workers/health');
    expect(expectedChatSessionsPath).toBe('/api/chat/sessions');
    expect(expectedChatMessagesPath).toBe('/api/chat/sessions/{session_id}/messages');
    expect(expectedLegacySessionsPath).toBe('/api/sessions');
    expect(expectedLegacySessionPath).toBe('/api/sessions/{session_id}');
    expect(expectedJobsPath).toBe('/api/jobs');
    expect(expectedProvidersPath).toBe('/api/providers');
    expect(expectedCodexAuthPath).toBe('/api/providers/chatgpt-codex/auth');
    expect(expectedCodexLoginPath).toBe('/api/providers/chatgpt-codex/login');
    expect(expectedProvidersRefreshPath).toBe('/api/providers/refresh');
    expect(expectedModelsRefreshPath).toBe('/api/models/refresh');
    expect(expectedModelResidencyPath).toBe('/api/model-residency');
    expect(expectedModelResidencyDeletePath).toBe('/api/model-residency/{model_id}');
    expect(expectedAssetsPath).toBe('/api/assets');
    expect(expectedLegacyAssetDryRunPath).toBe('/api/assets/migrations/legacy-non-image/dry-run');
    expect(expectedPromptsPath).toBe('/api/prompts/render');
    expect(expectedReplayPath).toBe('/api/replay/primitives');
    expect(expectedSettingsPath).toBe('/api/settings');
    expect(expectedReportsPath).toBe('/api/reports');
    expect(expectedDiagnosticsPath).toBe('/api/diagnostics');
  });
});
