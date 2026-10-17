import { pipelineFetch } from '../../../api/fetchPipeline';

import type { components } from './generated';
export type RpgWorldImageAttempt = components['schemas']['RpgWorldImageAttempt'];

export type RpgWorldImageTarget = components['schemas']['RpgWorldImageTarget'];

type RpgWorldImageTargetsResponse = components['schemas']['RpgWorldImageTargetsResponse'];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await pipelineFetch(path, init);
  const text = await response.text();
  if (!response.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; error?: unknown };
      const candidate = parsed.detail ?? parsed.error;
      detail = typeof candidate === 'string' ? candidate : JSON.stringify(candidate);
    } catch {
      // Preserve raw response.
    }
    throw new Error(`Omnix API request failed with status ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

function json(method: string, body: Record<string, unknown>): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export const rpgWorldImageClient = {
  list(worldId: string): Promise<RpgWorldImageTargetsResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/image-targets`);
  },

  generate(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; world_id: string; jobs: Array<Record<string, unknown>> }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/image-generation`,
      json('POST', body),
    );
  },

  regeneratePrompts(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; world_id: string; targets: RpgWorldImageTarget[] }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/image-prompts/regenerate`,
      json('POST', body),
    );
  },

  update(
    worldId: string,
    targetId: string,
    body: Record<string, unknown>,
  ): Promise<RpgWorldImageTargetsResponse> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/image-targets/${encodeURIComponent(targetId)}`,
      json('PATCH', body),
    );
  },

  regenerate(
    worldId: string,
    targetId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; world_id: string; jobs: Array<Record<string, unknown>> }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/image-targets/${encodeURIComponent(targetId)}/regenerate`,
      json('POST', body),
    );
  },
};
