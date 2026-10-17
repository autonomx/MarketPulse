import { pipelineFetch } from '../../../api/fetchPipeline';
import type { components } from './generated';
export type RpgWorldSummary = components['schemas']['RpgWorldSummary'];

export type RpgScenarioSummary = components['schemas']['RpgScenarioSummary'];

export type RpgWorldCampaignSummary = components['schemas']['RpgWorldCampaignSummary'];

export type RpgWorldGenerationRun = components['schemas']['RpgWorldGenerationRun'];

export type RpgWorldTopic = components['schemas']['RpgWorldTopic'];

export type RpgMapBlueprintRevision = components['schemas']['RpgMapBlueprintRevision'];

export type RpgWorldRevision = components['schemas']['RpgWorldRevision'];

export type RpgWorldRelease = components['schemas']['RpgWorldRelease'];

export type RpgScenarioRevision = components['schemas']['RpgScenarioRevision'];

export type RpgWorldLibraryResponse = components['schemas']['RpgWorldLibraryResponse'];

export type RpgWorldDetailResponse = components['schemas']['RpgWorldDetailResponse'];

export type RpgWorldLaunchRepairResponse = components['schemas']['RpgWorldLaunchRepairResponse'];

export type RpgPublishedLaunchResponse = components['schemas']['RpgPublishedLaunchResponse'];

export type RpgStarterBubbleResponse = components['schemas']['RpgStarterBubbleResponse'];

export type RpgStarterBubblePromotionResponse = components['schemas']['RpgStarterBubblePromotionResponse'];

export type RpgDeferredMaterializationResponse = components['schemas']['RpgDeferredMaterializationResponse'];

export type RpgWorldGenerationMutationResponse = components['schemas']['RpgWorldGenerationMutationResponse'];

export class RpgWorldGenerationRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, message: string, retryable = false) {
    super(message);
    this.name = 'RpgWorldGenerationRequestError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await pipelineFetch(path, init);
  const text = await response.text();
  if (!response.ok) {
    let detail: unknown = text;
    let code = 'world_generation_request_failed';
    let retryable = false;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; error?: unknown };
      const candidate = parsed.detail ?? parsed.error;
      detail = candidate;
      if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
        const error = candidate as Record<string, unknown>;
        if (typeof error.error === 'string') code = error.error;
        if (typeof error.retryable === 'boolean') retryable = error.retryable;
        if (typeof error.message === 'string') detail = error.message;
      }
    } catch {
      // Preserve the raw response body.
    }
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    throw new RpgWorldGenerationRequestError(
      response.status,
      code,
      `Omnix API request failed with status ${response.status}${message ? `: ${message}` : ''}`,
      retryable,
    );
  }
  return (text ? JSON.parse(text) : {}) as T;
}

function jsonInit(body: Record<string, unknown>): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export const rpgWorldLibraryClient = {
  list(): Promise<RpgWorldLibraryResponse> {
    return request<RpgWorldLibraryResponse>('/api/rpg/world-library');
  },

  detail(worldId: string): Promise<RpgWorldDetailResponse> {
    return request<RpgWorldDetailResponse>(`/api/rpg/worlds/${encodeURIComponent(worldId)}/library`);
  },

  createWorld(body: Record<string, unknown>): Promise<{ ok: boolean; world: RpgWorldSummary }> {
    return request('/api/rpg/worlds', jsonInit(body));
  },

  saveTopic(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; topic: RpgWorldTopic }> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/topics`, jsonInit(body));
  },

  saveMapBlueprint(
    worldId: string,
    mapId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; map_blueprint: RpgMapBlueprintRevision }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/map-blueprints/${encodeURIComponent(mapId)}`,
      jsonInit(body),
    );
  },

  materializeMapBlueprints(
    worldId: string,
  ): Promise<{ ok: boolean; created: RpgMapBlueprintRevision[]; created_count: number }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/map-blueprints/materialize`,
      jsonInit({}),
    );
  },

  archiveWorld(worldId: string): Promise<{ ok: boolean; world: RpgWorldSummary; idempotent: boolean }> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/archive`, jsonInit({}));
  },

  restoreWorld(worldId: string): Promise<{ ok: boolean; world: RpgWorldSummary; idempotent: boolean }> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/restore`, jsonInit({}));
  },

  archiveScenario(
    scenarioId: string,
  ): Promise<{ ok: boolean; scenario: RpgScenarioSummary; idempotent: boolean }> {
    return request(`/api/rpg/scenarios/${encodeURIComponent(scenarioId)}/archive`, jsonInit({}));
  },

  restoreScenario(
    scenarioId: string,
  ): Promise<{ ok: boolean; scenario: RpgScenarioSummary; idempotent: boolean }> {
    return request(`/api/rpg/scenarios/${encodeURIComponent(scenarioId)}/restore`, jsonInit({}));
  },

  startGeneration(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<RpgWorldGenerationMutationResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/generation`, jsonInit(body));
  },

  retryFailedGeneration(runId: string): Promise<RpgWorldGenerationMutationResponse> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/retry-failed`,
      jsonInit({}),
    );
  },

  continueGeneration(runId: string): Promise<RpgWorldGenerationMutationResponse> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/continue`,
      jsonInit({}),
    );
  },

  generationDiagnostics(): Promise<{
    ok: boolean;
    path: string;
    format: string;
    contains_generated_content: boolean;
  }> {
    return request('/api/rpg/world-generation/diagnostics');
  },

  generation(runId: string): Promise<{ ok: boolean; run: RpgWorldGenerationRun }> {
    return request(`/api/rpg/world-generation/${encodeURIComponent(runId)}`);
  },

  publishGeneration(
    runId: string,
  ): Promise<{ ok: boolean; status: string; run: RpgWorldGenerationRun; publication: Record<string, unknown> }> {
    return request(`/api/rpg/world-generation/${encodeURIComponent(runId)}/publish`, jsonInit({}));
  },

  previewStarterBubble(
    worldId: string,
    sourceWorldRevision: number,
    startingLocationId: string,
    neighboringLocationId?: string,
  ): Promise<RpgStarterBubbleResponse> {
    const query = new URLSearchParams({
      source_world_revision: String(sourceWorldRevision),
      starting_location_id: startingLocationId,
    });
    if (neighboringLocationId) query.set('neighboring_location_id', neighboringLocationId);
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/starter-bubble/preview?${query.toString()}`,
    );
  },

  promoteStarterBubble(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<RpgStarterBubblePromotionResponse> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/starter-bubble/promote`,
      jsonInit(body),
    );
  },

  repairWorldForLaunch(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<RpgWorldLaunchRepairResponse> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/repair-for-launch`,
      jsonInit(body),
    );
  },

  prepareOpeningScenariosForLaunch(
    worldId: string,
  ): Promise<{
    ok: boolean;
    world_id: string;
    status?: 'generating' | 'ready' | 'review_required';
    generation_run_id?: string;
    prepared: Array<{ scenario_id: string; title: string }>;
  }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/prepare-openings-for-launch`,
      jsonInit({}),
    );
  },

  materializeDeferredLocation(
    worldId: string,
    locationId: string,
    sourceWorldRevision: number,
  ): Promise<RpgDeferredMaterializationResponse> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/deferred-locations/${encodeURIComponent(locationId)}/materialize`,
      jsonInit({ source_world_revision: sourceWorldRevision }),
    );
  },

  createScenario(body: Record<string, unknown>): Promise<{ ok: boolean; scenario: RpgScenarioSummary }> {
    return request('/api/rpg/scenarios', jsonInit(body));
  },

  publishScenario(
    scenarioId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; scenario_revision: RpgScenarioRevision }> {
    return request(`/api/rpg/scenarios/${encodeURIComponent(scenarioId)}/revisions`, jsonInit(body));
  },

  launchScenario(
    scenarioId: string,
    scenarioRevision: number,
    body: Record<string, unknown>,
  ): Promise<RpgPublishedLaunchResponse> {
    return request(
      `/api/rpg/scenarios/${encodeURIComponent(scenarioId)}/revisions/${scenarioRevision}/launch`,
      jsonInit(body),
    );
  },
};
