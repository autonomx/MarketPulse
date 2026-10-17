import type { components, paths } from './generated/core';
import { ApiError, ApiTimeoutError } from './errors';
import { createGatewayClient, sendGatewayCall, type GatewayClient } from './http';
import { pipelineFetch } from './fetchPipeline';

export { ApiError, ApiTimeoutError } from './errors';

export type GatewayApiPaths = paths;
export type GatewayApiPath = keyof GatewayApiPaths & string;
export type AssetLegacyImportDryRun = components['schemas']['PublicAssetLegacyImportDryRun'];
export type AssetListResponse = components['schemas']['PublicAssetListResponse'];

export type CancelJobRequest = components['schemas']['CancelJobRequest'];
export type ChatSession = components['schemas']['ChatSession'];
export type ChatSessionListResponse = components['schemas']['ChatSessionListResponse'];
export type ChatSessionAttachments = Record<string, string[]>;
export type CheckpointEnvelope = components['schemas']['CheckpointEnvelope'];
export type CodexAuthStatus = components['schemas']['CodexAuthStatus'];
export type CreateChatSessionRequest = components['schemas']['CreateChatSessionRequest'];
export type CreateJobRequest = components['schemas']['CreateJobRequest'];
export type DiagnosticsPayload = components['schemas']['DiagnosticsPayload'];
export type JobListResponse = components['schemas']['JobListResponse'];
export type ListJobsOptions = { limit?: number; full?: boolean; cursor?: string | null };
type AssetListQuery = NonNullable<paths['/api/assets']['get']['parameters']['query']>;
export type ListAssetsOptions = { type?: AssetListQuery['type']; module?: string; limit?: number; cursor?: string | null };
export type JobRecord = components['schemas']['JobRecord'];
export type ModelResidencyDiagnostics = components['schemas']['ModelResidencyDiagnostics'];
export type ModelResidencyRecord = components['schemas']['ModelResidencyRecord'];
export type PersistenceInventory = components['schemas']['PersistenceInventory'];
export type ProviderFacadePayload = components['schemas']['ProviderFacadePayload'];
export type ProviderModelRefreshRequest = components['schemas']['ProviderModelRefreshRequest'];
export type ReportListResponse = components['schemas']['ReportListResponse'];
export type SendChatMessageRequest = components['schemas']['SendChatMessageRequest'];
export type CodingApprovalPolicy = NonNullable<SendChatMessageRequest['coding_approval_policy']>;
export type SendChatMessageResponse = components['schemas']['SendChatMessageResponse'];
export type SettingsPayload = components['schemas']['SettingsPayload'];
export type SettingsSaveResponse = components['schemas']['SettingsSaveResponse'];

export type DeleteChatSessionResponse = components['schemas']['DeleteChatSessionResponse'];
export type AssetContentResponse = components['schemas']['AssetContentResponse'];

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface ApiRequestOptions {
  timeoutMessage?: string;
  timeoutMs?: number;
}

const ASSET_PAGE_SIZE = 200;
// Stops a runaway loop; 200 pages hold 40,000 assets.
const MAX_ASSET_PAGES = 200;

export class OmnixApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  private readonly api: GatewayClient;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? '';
    this.fetchImpl = options.fetchImpl ?? ((input, init) => pipelineFetch(input, init));
    this.api = createGatewayClient({ baseUrl: options.baseUrl || undefined, fetchImpl: this.fetchImpl });
  }

  /** Sends one typed call, optionally with a timeout, and returns its body (WP-9.3). */
  private async call<T>(
    send: (signal?: AbortSignal) => Promise<{ data?: T; error?: unknown; response: Response }>,
    options: ApiRequestOptions = {},
  ): Promise<T> {
    return sendGatewayCall(send, options);
  }

  async get<T>(path: `/api/${string}`): Promise<T> {
    return this.request<T>(path, { method: 'GET' });
  }

  async post<TRequest, TResponse>(path: `/api/${string}`, body: TRequest, options: ApiRequestOptions = {}): Promise<TResponse> {
    return this.request<TResponse>(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      options
    );
  }

  async listChatSessions(): Promise<ChatSessionListResponse> {
    const sessions: ChatSessionListResponse['sessions'] = [];
    const cursors = new Set<string>();
    let cursor: string | null = null;
    do {
      const query: { limit?: number; cursor?: string } = cursor ? { limit: 100, cursor } : {};
      const page: ChatSessionListResponse = await this.call(() => this.api.GET('/api/chat/sessions', { params: { query } }));
      sessions.push(...page.sessions);
      cursor = page.next_cursor ?? null;
      if (cursor && cursors.has(cursor)) {
        throw new Error('Chat session pagination did not advance');
      }
      if (cursor) cursors.add(cursor);
    } while (cursor);
    return { sessions, next_cursor: null };
  }

  async createChatSession(request: CreateChatSessionRequest): Promise<ChatSession> {
    return this.call(() => this.api.POST('/api/chat/sessions', { body: request }));
  }

  async getChatSession(sessionId: string, options?: { includeAttachments?: boolean }): Promise<ChatSession> {
    const query = options?.includeAttachments === false ? { include_attachments: false } : {};
    return this.call(() => this.api.GET('/api/chat/sessions/{session_id}', { params: { path: { session_id: sessionId }, query } }));
  }

  async getChatSessionAttachments(sessionId: string): Promise<ChatSessionAttachments> {
    return this.call(() => this.api.GET('/api/chat/sessions/{session_id}/attachments', { params: { path: { session_id: sessionId } } }));
  }

  async deleteChatSession(sessionId: string): Promise<DeleteChatSessionResponse> {
    return this.call(() => this.api.DELETE('/api/chat/sessions/{session_id}', { params: { path: { session_id: sessionId } } }));
  }

  async sendChatMessage(sessionId: string, request: SendChatMessageRequest): Promise<SendChatMessageResponse> {
    return this.call(
      (signal) => this.api.POST('/api/chat/sessions/{session_id}/messages', { params: { path: { session_id: sessionId } }, body: request, signal }),
      { timeoutMs: 15_000, timeoutMessage: 'Chat request was not accepted by the gateway within 15s.' },
    );
  }

  async listProviders(): Promise<ProviderFacadePayload> {
    return this.call(() => this.api.GET('/api/providers'));
  }

  async listModels(): Promise<ProviderFacadePayload> {
    return this.call(() => this.api.GET('/api/models'));
  }

  async getCodexAuthStatus(): Promise<CodexAuthStatus> {
    return this.call(() => this.api.GET('/api/providers/chatgpt-codex/auth'));
  }

  async startCodexLogin(): Promise<CodexAuthStatus> {
    return this.call(() => this.api.POST('/api/providers/chatgpt-codex/login'));
  }

  async refreshProviders(request: ProviderModelRefreshRequest = { scope: 'all', priority: 0 }): Promise<JobRecord> {
    return this.call(() => this.api.POST('/api/providers/refresh', { body: request }));
  }

  async refreshModels(request: ProviderModelRefreshRequest = { scope: 'models', priority: 0 }): Promise<JobRecord> {
    return this.call(() => this.api.POST('/api/models/refresh', { body: request }));
  }

  // openapi-fetch reads the policy's model pairs as string[][]; the schema says pairs.
  async getModelResidency(): Promise<ModelResidencyDiagnostics> {
    return this.call(() => this.api.GET('/api/model-residency')) as Promise<ModelResidencyDiagnostics>;
  }

  async reportModelResidency(request: ModelResidencyRecord): Promise<ModelResidencyDiagnostics> {
    return this.call(() => this.api.POST('/api/model-residency', { body: request })) as Promise<ModelResidencyDiagnostics>;
  }

  async deleteModelResidency(modelId: string): Promise<ModelResidencyDiagnostics> {
    return this.call(() => this.api.DELETE('/api/model-residency/{model_id}', { params: { path: { model_id: modelId } } })) as Promise<ModelResidencyDiagnostics>;
  }

  async listJobs(options: ListJobsOptions = {}): Promise<JobListResponse> {
    const query: { limit?: number; full?: boolean; cursor?: string } = {};
    if (options.limit !== undefined) query.limit = options.limit;
    if (options.full !== undefined) query.full = options.full;
    if (options.cursor) query.cursor = options.cursor;
    return this.call(() => this.api.GET('/api/jobs', { params: { query } }));
  }

  async createJob(request: CreateJobRequest, options: ApiRequestOptions = {}): Promise<JobRecord> {
    return this.call((signal) => this.api.POST('/api/jobs', { body: request, signal }), options);
  }

  async getJob(jobId: string): Promise<JobRecord> {
    return this.call(() => this.api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } }));
  }

  async cancelJob(jobId: string, reason: string): Promise<JobRecord> {
    return this.call(() => this.api.POST('/api/jobs/{job_id}/cancel', { params: { path: { job_id: jobId } }, body: { reason } }));
  }

  /**
   * Every asset matching the filter, newest first. The gateway returns pages
   * of at most 200; this follows `next_cursor` until the last page.
   */
  async listAssets(filter: Pick<ListAssetsOptions, 'type' | 'module'> = {}): Promise<AssetListResponse> {
    const assets: AssetListResponse['assets'] = [];
    let cursor: string | null | undefined;
    for (let page = 0; page < MAX_ASSET_PAGES; page += 1) {
      const response = await this.listAssetPage({ ...filter, limit: ASSET_PAGE_SIZE, cursor });
      assets.push(...response.assets);
      if (!response.has_more || !response.next_cursor) {
        break;
      }
      cursor = response.next_cursor;
    }
    return { assets, next_cursor: null, has_more: false };
  }

  /** One page of assets, newest first; pass the previous page's `next_cursor` for the next one. */
  async listAssetPage(options: ListAssetsOptions = {}): Promise<AssetListResponse> {
    const query: AssetListQuery = { limit: options.limit ?? ASSET_PAGE_SIZE };
    if (options.type) query.type = options.type;
    if (options.module) query.module = options.module;
    if (options.cursor) query.cursor = options.cursor;
    return this.call(() => this.api.GET('/api/assets', { params: { query } }));
  }

  async getAssetContent(assetId: string): Promise<AssetContentResponse> {
    return this.call(() => this.api.GET('/api/assets/{asset_id}/content', { params: { path: { asset_id: assetId } } }));
  }

  async deleteVoiceAsset(assetId: string): Promise<{ ok: boolean; asset_id: string; deleted: boolean; file_deleted: boolean }> {
    const result = await this.call(() => this.api.DELETE('/api/voice-cloning/assets/{asset_id}', { params: { path: { asset_id: assetId } } }));
    return result as unknown as { ok: boolean; asset_id: string; deleted: boolean; file_deleted: boolean };
  }

  async previewLegacyNonImageAssetImport(): Promise<AssetLegacyImportDryRun> {
    return this.call(() => this.api.POST('/api/assets/migrations/legacy-non-image/dry-run'));
  }

  async listReports(): Promise<ReportListResponse> {
    return this.call(() => this.api.GET('/api/reports'));
  }

  async createReplayCheckpoint(request: Record<string, unknown>): Promise<CheckpointEnvelope> {
    return this.post<Record<string, unknown>, CheckpointEnvelope>('/api/replay/checkpoints', request);
  }

  async getSettings(): Promise<SettingsPayload> {
    return this.get<SettingsPayload>('/api/settings');
  }

  async saveSettings(request: Record<string, unknown>): Promise<SettingsSaveResponse> {
    return this.post<Record<string, unknown>, SettingsSaveResponse>('/api/settings', request);
  }

  async getDiagnostics(): Promise<DiagnosticsPayload> {
    return this.get<DiagnosticsPayload>('/api/diagnostics');
  }

  private async request<T>(path: `/api/${string}`, init: RequestInit, options: ApiRequestOptions = {}): Promise<T> {
    let didTimeout = false;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    const controller = options.timeoutMs ? new AbortController() : undefined;

    if (controller && options.timeoutMs) {
      timeoutId = setTimeout(() => {
        didTimeout = true;
        controller.abort();
      }, options.timeoutMs);
    }

    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        signal: controller?.signal ?? init.signal,
      });
      const text = await response.text();

      if (!response.ok) {
        // Test doubles may omit headers.
        throw new ApiError(response.status, text, response.headers?.get('x-request-id') ?? undefined);
      }

      if (!text) {
        return undefined as T;
      }

      return JSON.parse(text) as T;
    } catch (error) {
      if (didTimeout && options.timeoutMs) {
        throw new ApiTimeoutError(options.timeoutMs, options.timeoutMessage);
      }
      throw error;
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }
  }
}

export const omnixApiClient = new OmnixApiClient();
