import type { AssetListResponse, JobListResponse, JobRecord } from '../../api/client';
import type { components } from './api/generated';
import { unwrap } from '../../api/http';
import { toImageModelStatusView, type ImageModelStatusView } from './ImageModelSelectorControl';
import type { WorkerHealthPayload } from './imageReadinessModel';
import { api } from './api/gateway';

type ImageModelDownloadRequest = components['schemas']['ImageModelDownloadRequest'];
export type ImageAssetDeleteResponse = components['schemas']['ImageAssetDeleteResponse'];

type ModelStatusCall = Promise<{ data?: components['schemas']['ImageModelStatusResponse']; error?: unknown; response: Response }>;
const modelStatus = async (call: ModelStatusCall): Promise<ImageModelStatusView> =>
  toImageModelStatusView(await unwrap(call));

/** The image workspace's gateway calls (WP-9.3). */
export const imageApi = {
  workerHealth: (): Promise<WorkerHealthPayload> => unwrap(api.GET('/api/workers/health')),
  modelStatus: (provider: string) =>
    modelStatus(api.GET('/api/image-generation/model/status', { params: { query: { provider } } })),
  downloadModel: (request: ImageModelDownloadRequest) =>
    modelStatus(api.POST('/api/image-generation/model/download', { body: request })),
  loadModel: (provider: string) => modelStatus(api.POST('/api/image-generation/model/load', { body: { provider } })),
  unloadModel: (provider: string) => modelStatus(api.POST('/api/image-generation/model/unload', { body: { provider } })),
  jobs: (): Promise<JobListResponse> => unwrap(api.GET('/api/image-generation/jobs')),
  retryJob: (jobId: string): Promise<JobRecord> =>
    unwrap(api.POST('/api/image-generation/jobs/{job_id}/retry', { params: { path: { job_id: jobId } } })),
  assets: (): Promise<AssetListResponse> => unwrap(api.GET('/api/image-generation/assets')),
  deleteAsset: (assetId: string): Promise<ImageAssetDeleteResponse> =>
    unwrap(api.POST('/api/image-generation/assets/{asset_id}/delete', { params: { path: { asset_id: assetId } } })),
};
