import { pipelineFetch } from '../../../api/fetchPipeline';
import type { components } from './generated';
export type RpgWorldBundleImportResponse = components['schemas']['RpgWorldBundleImportResponse'];

export interface RpgWorldBundleDownload {
  blob: Blob;
  filename: string;
}

async function errorFromResponse(response: Response): Promise<Error> {
  const text = await response.text();
  let detail = text;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; error?: unknown };
    const candidate = parsed.detail ?? parsed.error;
    detail = typeof candidate === 'string' ? candidate : JSON.stringify(candidate);
  } catch {
    // Preserve the raw response body.
  }
  return new Error(
    `Omnix API request failed with status ${response.status}${detail ? `: ${detail}` : ''}`,
  );
}

function filenameFromDisposition(value: string | null, fallback: string): string {
  const match = value?.match(/filename="?([^";]+)"?/i);
  return match?.[1]?.trim() || fallback;
}

export const rpgWorldBundleClient = {
  async exportWorld(worldId: string): Promise<RpgWorldBundleDownload> {
    const response = await pipelineFetch(`/api/rpg/worlds/${encodeURIComponent(worldId)}/export`);
    if (!response.ok) throw await errorFromResponse(response);
    return {
      blob: await response.blob(),
      filename: filenameFromDisposition(
        response.headers.get('content-disposition'),
        `${worldId.replace(/[^a-zA-Z0-9._-]+/g, '-')}.omnix-world.zip`,
      ),
    };
  },

  async importWorld(file: File, targetWorldId?: string): Promise<RpgWorldBundleImportResponse> {
    const query = new URLSearchParams();
    if (targetWorldId?.trim()) query.set('target_world_id', targetWorldId.trim());
    const queryString = query.toString();
    const suffix = queryString ? `?${queryString}` : '';
    const response = await pipelineFetch(`/api/rpg/worlds/import${suffix}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    if (!response.ok) throw await errorFromResponse(response);
    return response.json() as Promise<RpgWorldBundleImportResponse>;
  },
};
