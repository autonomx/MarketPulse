import { ApiError, omnixApiClient, type ApiRequestOptions, type CreateJobRequest, type JobRecord } from '../../../api/client';
import { unwrap } from '../../../api/http';
import { api } from './gateway';
import { withRpgGenesisContract } from './rpgGenesisPresentation';

import type { components } from './generated';
export type RpgPlayerOptions = components['schemas']['RpgPlayerOptions'];

export type RpgFeatureOptions = components['schemas']['RpgFeatureOptions'];

export type RpgCapability = 'combat' | 'recon' | 'influence' | 'technical' | 'survival' | 'knowledge' | 'support' | 'custom';
export type RpgPowerSource = 'mundane' | 'martial' | 'magic' | 'technology' | 'psionic' | 'divine' | 'occult' | 'mutation' | 'mythic' | 'social_power' | 'scrap' | 'custom';

export type RpgNewGameRequest = components['schemas']['RpgNewGameRequestBody'];

export type RpgPresetSummary = components['schemas']['RpgPresetSummary'];

export type RpgSessionListResponse = components['schemas']['RpgSessionListResponse'];

export type RpgPresetListResponse = components['schemas']['RpgPresetListResponse'];

/** How the client reached the launch route; added to the launch response for diagnostics, never sent by the gateway. */
export interface RpgLaunchRequestTraceEvent {
  endpoint: string;
  method: string;
  status: 'started' | 'completed' | 'failed' | 'fallback';
  elapsed_ms?: number;
  http_status?: number;
  error?: string;
}

export interface RpgLaunchRequestTrace {
  active_endpoint?: string;
  final_endpoint?: string;
  elapsed_ms?: number;
  events: RpgLaunchRequestTraceEvent[];
}

export type RpgLaunchResponse = components['schemas']['RpgLaunchResponse'] & { creation_request_trace?: RpgLaunchRequestTrace };

export type RpgTurnResponse = components['schemas']['RpgTurnResponse'];

export type RpgSessionMutationResponse = components['schemas']['RpgSessionMutationResponse'];

export type RpgLoadoutActionRequest = components['schemas']['RpgLoadoutActionRequest'];

export type RpgLoadoutActionResponse = components['schemas']['RpgLoadoutActionResponse'];

function nowMs(): number {
  if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
    return performance.now();
  }
  return Date.now();
}

function logRpgLaunchTrace(message: string, detail?: unknown): void {
  if (typeof console === 'undefined') {
    return;
  }
  console.info(`[RPG][new-game][client] ${message}`, detail ?? '');
}

function warnRpgLaunchTrace(message: string, detail?: unknown): void {
  if (typeof console === 'undefined') {
    return;
  }
  console.warn(`[RPG][new-game][client] ${message}`, detail ?? '');
}

function errorLabel(error: unknown): string {
  if (error instanceof ApiError) {
    return `HTTP ${error.status}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'request_failed';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

type CompatibilityRequest = components['schemas']['RpgCompatibilityRequest'];

/** The compatibility route answers for gateways without the session routes; its envelope is the session route's. */
async function compatibility<T>(body: CompatibilityRequest): Promise<T> {
  return await unwrap(api.POST('/api/rpg/session/get', { body })) as T;
}

async function listCompatibility(): Promise<RpgSessionListResponse> {
  return await unwrap(api.POST('/api/rpg/session/list')) as RpgSessionListResponse;
}

/** The RPG feature's gateway calls; each falls back to the compatibility route when the session route is missing. */
export const rpgSessionClient = {
  async listRpgPresets(): Promise<RpgPresetListResponse> {
    try {
      return await unwrap(api.GET('/api/rpg/presets')) as RpgPresetListResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      const compatibilityList = await listCompatibility();
      return { ok: compatibilityList.ok, presets: compatibilityList.presets ?? [] };
    }
  },

  async listRpgSessions(): Promise<RpgSessionListResponse> {
    try {
      const [sessions, presets] = await Promise.all([rpgSessionClient.listRpgSessionSummaries(), rpgSessionClient.listRpgPresets()]);
      return { ...sessions, presets: presets.presets };
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return listCompatibility();
    }
  },

  async listRpgSessionSummaries(): Promise<RpgSessionListResponse> {
    return await unwrap(api.GET('/api/rpg/sessions')) as RpgSessionListResponse;
  },

  async getRpgSession(sessionId: string): Promise<RpgLaunchResponse> {
    return await unwrap(api.GET('/api/rpg/sessions/{session_id}', { params: { path: { session_id: sessionId } } })) as RpgLaunchResponse;
  },

  async createRpgNewGame(request: RpgNewGameRequest = {}): Promise<RpgLaunchResponse> {
    const genesisRequest = withRpgGenesisContract(request);
    const traceStartedAt = nowMs();
    const events: RpgLaunchRequestTraceEvent[] = [{ endpoint: '/api/rpg/new-game', method: 'POST', status: 'started' }];
    logRpgLaunchTrace('starting POST /api/rpg/new-game');
    try {
      const startedAt = nowMs();
      const result = await unwrap(api.POST('/api/rpg/new-game', { body: genesisRequest })) as RpgLaunchResponse;
      const elapsed = Math.round(nowMs() - startedAt);
      events.push({ endpoint: '/api/rpg/new-game', method: 'POST', status: 'completed', elapsed_ms: elapsed });
      const tracedResult: RpgLaunchResponse = {
        ...result,
        creation_request_trace: {
          active_endpoint: '/api/rpg/new-game',
          final_endpoint: '/api/rpg/new-game',
          elapsed_ms: Math.round(nowMs() - traceStartedAt),
          events,
        },
      };
      logRpgLaunchTrace('completed POST /api/rpg/new-game', tracedResult.creation_request_trace);
      if (tracedResult.creation_server_trace) {
        logRpgLaunchTrace('server trace', tracedResult.creation_server_trace);
      }
      return tracedResult;
    } catch (error) {
      const primaryElapsed = Math.round(nowMs() - traceStartedAt);
      events.push({
        endpoint: '/api/rpg/new-game',
        method: 'POST',
        status: 'failed',
        elapsed_ms: primaryElapsed,
        http_status: error instanceof ApiError ? error.status : undefined,
        error: errorLabel(error),
      });
      warnRpgLaunchTrace('primary POST /api/rpg/new-game failed', events[events.length - 1]);
      if (!isNotFound(error)) {
        throw error;
      }
      events.push({ endpoint: '/api/rpg/session/get', method: 'POST', status: 'fallback' });
      logRpgLaunchTrace('falling back to POST /api/rpg/session/get');
      const fallbackStartedAt = nowMs();
      const result = await compatibility<RpgLaunchResponse>({ action: 'new_game', request: genesisRequest });
      events.push({ endpoint: '/api/rpg/session/get', method: 'POST', status: 'completed', elapsed_ms: Math.round(nowMs() - fallbackStartedAt) });
      const tracedResult: RpgLaunchResponse = {
        ...result,
        creation_request_trace: {
          active_endpoint: '/api/rpg/session/get',
          final_endpoint: '/api/rpg/session/get',
          elapsed_ms: Math.round(nowMs() - traceStartedAt),
          events,
        },
      };
      logRpgLaunchTrace('completed POST /api/rpg/session/get', tracedResult.creation_request_trace);
      if (tracedResult.creation_server_trace) {
        logRpgLaunchTrace('server trace', tracedResult.creation_server_trace);
      }
      return tracedResult;
    }
  },

  async startRpgPreset(presetId: string): Promise<RpgLaunchResponse> {
    try {
      return await unwrap(api.POST('/api/rpg/presets/{preset_id}/start', { params: { path: { preset_id: presetId } } })) as RpgLaunchResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return compatibility<RpgLaunchResponse>({ action: 'start_preset', preset_id: presetId });
    }
  },

  async continueRpgSession(sessionId: string): Promise<RpgLaunchResponse> {
    try {
      return await unwrap(api.POST('/api/rpg/sessions/{session_id}/continue', { params: { path: { session_id: sessionId } } })) as RpgLaunchResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return compatibility<RpgLaunchResponse>({ action: 'continue', session_id: sessionId });
    }
  },

  async renameRpgSession(sessionId: string, name: string): Promise<RpgSessionMutationResponse> {
    try {
      return await unwrap(api.POST('/api/rpg/sessions/{session_id}/rename', { params: { path: { session_id: sessionId } }, body: { name } })) as RpgSessionMutationResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return compatibility<RpgSessionMutationResponse>({ action: 'rename', session_id: sessionId, name });
    }
  },

  async deleteRpgSession(sessionId: string): Promise<RpgSessionMutationResponse> {
    try {
      return await unwrap(api.POST('/api/rpg/sessions/{session_id}/delete', { params: { path: { session_id: sessionId } } })) as RpgSessionMutationResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return compatibility<RpgSessionMutationResponse>({ action: 'delete', session_id: sessionId });
    }
  },

  async applyRpgLoadoutAction(sessionId: string, request: RpgLoadoutActionRequest): Promise<RpgLoadoutActionResponse> {
    try {
      return await unwrap(api.POST('/api/rpg/sessions/{session_id}/loadout-action', {
        params: { path: { session_id: sessionId } },
        body: request,
      })) as RpgLoadoutActionResponse;
    } catch (error) {
      if (!isNotFound(error)) {
        throw error;
      }
      return compatibility<RpgLoadoutActionResponse>({ action: 'loadout_action', session_id: sessionId, loadout: request });
    }
  },

  /**
   * Applies an `rpg.turn` through the foreground session route and returns the
   * job the gateway completed with it; without that route (or a session and
   * command) the request becomes an ordinary queued job.
   */
  async submitTurnJob(request: CreateJobRequest, options: ApiRequestOptions = {}): Promise<JobRecord> {
    const foregroundTurn = await createForegroundTurnJob(request);
    return foregroundTurn ?? omnixApiClient.createJob(request, options);
  },
};

/** The turn's `rpg.turn.foreground_record` job, which the gateway completes in the turn's transaction. */
async function createForegroundTurnJob(request: CreateJobRequest): Promise<JobRecord | null> {
  const requestRecord = request as Record<string, unknown>;
  if (requestRecord.module !== 'rpg' || requestRecord.type !== 'rpg.turn') {
    return null;
  }
  const sessionId = stringValue(asRecord(requestRecord.input_ref).session_id);
  const command = stringValue(asRecord(requestRecord.input_payload).command);
  if (!sessionId || !command) {
    return null;
  }
  let result: RpgTurnResponse;
  try {
    result = await unwrap(api.POST('/api/rpg/sessions/{session_id}/turn', {
      params: { path: { session_id: sessionId } },
      body: { command },
    })) as RpgTurnResponse;
  } catch (error) {
    if (isNotFound(error)) {
      return null;
    }
    throw error;
  }
  if (!result.job_id) {
    // The turn was applied; queueing it again would apply it twice.
    throw new Error('The RPG turn was applied, but the gateway did not return its job.');
  }
  return omnixApiClient.getJob(result.job_id);
}
