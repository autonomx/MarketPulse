import type { ApiRequestOptions } from '../../api/client';
import { unwrap } from '../../api/http';
import { api } from './api/gateway';
import type { components } from './api/generated';
import type { RpgLaunchResponse } from './api/rpgSessionClient';

/** The compatibility route's envelope: a launch response with the action's own fields. */
export type RpgItemCompatResponse = components['schemas']['RpgCompatibilityResponse'] & RpgLaunchResponse;

export interface RpgItemApiClient {
  post<TRequest, TResponse>(path: `/api/${string}`, body: TRequest, options?: ApiRequestOptions): Promise<TResponse>;
}

/** Item actions go through the compatibility route; this sends them with the typed gateway client. */
export const rpgItemGatewayClient: RpgItemApiClient = {
  async post<TRequest, TResponse>(path: `/api/${string}`, body: TRequest): Promise<TResponse> {
    if (path !== '/api/rpg/session/get') {
      throw new Error(`RPG item actions use /api/rpg/session/get, not ${path}`);
    }
    return await unwrap(api.POST('/api/rpg/session/get', {
      body: body as components['schemas']['RpgCompatibilityRequest'],
    })) as TResponse;
  },
};

export interface RpgItemResolveOptions {
  input?: Record<string, unknown>;
  command?: string;
  source?: string;
  diagnosticsInterval?: number;
  maintenanceInterval?: number;
  reportInterval?: number;
  objectiveLimit?: number;
}

export interface RpgItemPlanningOptions {
  station?: string;
  genre?: string;
  limit?: number;
  objectiveLimit?: number;
  scenarioLimit?: number;
  includeStatusSteps?: boolean;
  run?: boolean;
  steps?: Record<string, unknown>[];
  source?: string;
}

export interface RpgItemDiagnosticsOptions extends RpgItemPlanningOptions {
  record?: boolean;
  recordTrace?: boolean;
  dryRun?: boolean;
  bucketLimit?: number;
  compactionThreshold?: number;
  recordReport?: boolean;
}

export interface RpgMerchantCommandOptions {
  command?: string;
  merchantId?: string;
  itemName?: string;
  action?: 'menu' | 'buy' | 'sell';
  quantity?: number;
  source?: string;
}

export interface RpgItemDetailOptions {
  itemName: string;
  itemCount?: string | number;
  source?: string;
  context?: Record<string, unknown>;
}

export interface RpgAbilityDetailOptions {
  abilityName: string;
  abilityId?: string;
  source?: string;
}

export function applyRpgItemResolve(
  sessionId: string,
  options: RpgItemResolveOptions,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_resolve',
      session_id: sessionId,
      command: options.command,
      input: options.input,
      source: options.source,
      diagnostics_interval: options.diagnosticsInterval,
      maintenance_interval: options.maintenanceInterval,
      report_interval: options.reportInterval,
      objective_limit: options.objectiveLimit,
    },
    client,
  );
}

export function applyRpgItemCommand(
  sessionId: string,
  command: string,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet({ action: 'item_command', session_id: sessionId, command }, client);
}

export function applyRpgStructuredItemAction(
  sessionId: string,
  itemAction: Record<string, unknown>,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet({ action: 'item_action', session_id: sessionId, item_action: itemAction }, client);
}

export function fetchRpgItemDetails(
  sessionId: string,
  options: RpgItemDetailOptions,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_detail',
      session_id: sessionId,
      item_name: options.itemName,
      item_count: options.itemCount,
      source: options.source ?? 'rpg-item-panel',
      context: options.context,
    },
    client,
  );
}

export function fetchRpgAbilityDetails(
  sessionId: string,
  options: RpgAbilityDetailOptions,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'ability_detail',
      session_id: sessionId,
      ability_name: options.abilityName,
      ability_id: options.abilityId,
      source: options.source ?? 'rpg-hotbar-tooltip',
    },
    client,
  );
}

export function fetchRpgItemObjectives(
  sessionId: string,
  options: RpgItemPlanningOptions = {},
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_objectives',
      session_id: sessionId,
      station: options.station,
      genre: options.genre,
      limit: options.limit,
      objective_limit: options.objectiveLimit,
    },
    client,
  );
}

export function runRpgItemScenario(
  sessionId: string,
  options: RpgItemPlanningOptions = {},
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_scenario',
      session_id: sessionId,
      station: options.station,
      genre: options.genre,
      scenario_limit: options.scenarioLimit ?? options.limit,
      include_status_steps: options.includeStatusSteps,
      run: options.run,
      steps: options.steps,
      source: options.source,
    },
    client,
  );
}

export function fetchRpgItemDiagnostics(
  sessionId: string,
  options: RpgItemDiagnosticsOptions = {},
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_diagnostics',
      session_id: sessionId,
      station: options.station,
      genre: options.genre,
      scenario_limit: options.scenarioLimit,
      objective_limit: options.objectiveLimit,
      record: options.record,
      record_trace: options.recordTrace,
    },
    client,
  );
}

export function runRpgItemMaintenance(
  sessionId: string,
  options: RpgItemDiagnosticsOptions = {},
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'item_maintenance',
      session_id: sessionId,
      dry_run: options.dryRun,
      bucket_limit: options.bucketLimit,
      compaction_threshold: options.compactionThreshold,
      record_report: options.recordReport,
    },
    client,
  );
}

export function applyRpgMerchantCommand(
  sessionId: string,
  options: RpgMerchantCommandOptions,
  client: RpgItemApiClient = rpgItemGatewayClient,
): Promise<RpgItemCompatResponse> {
  return postRpgSessionGet(
    {
      action: 'merchant_command',
      session_id: sessionId,
      command: options.command,
      merchant_id: options.merchantId,
      item_name: options.itemName,
      merchant_action: options.action,
      quantity: options.quantity,
      source: options.source,
    },
    client,
  );
}

function postRpgSessionGet(
  body: Record<string, unknown>,
  client: RpgItemApiClient,
): Promise<RpgItemCompatResponse> {
  return client.post<Record<string, unknown>, RpgItemCompatResponse>('/api/rpg/session/get', stripUndefined(body));
}

function stripUndefined(body: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined));
}
