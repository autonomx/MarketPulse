import { pipelineFetch } from '../../../api/fetchPipeline';
import type { components } from './generated';
export type RpgWorldDeletionBlocker = components['schemas']['RpgWorldDeletionBlocker'];

export type RpgWorldDeletionEligibility = components['schemas']['RpgWorldDeletionEligibility'];

type RpgWorldDeletionEligibilityResponse = components['schemas']['RpgWorldDeletionEligibilityResponse'];

export type RpgWorldDeletionResponse = components['schemas']['RpgWorldDeletionResponse'];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await pipelineFetch(path, init);
  const text = await response.text();
  if (!response.ok) {
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; error?: unknown };
      const candidate = parsed.detail ?? parsed.error;
      if (candidate && typeof candidate === 'object' && 'error' in candidate) {
        detail = String((candidate as { error: unknown }).error);
      } else {
        detail = typeof candidate === 'string' ? candidate : JSON.stringify(candidate);
      }
    } catch {
      // Preserve the raw response.
    }
    throw new Error(`Omnix API request failed with status ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

export const rpgWorldDeletionClient = {
  eligibility(worldId: string): Promise<RpgWorldDeletionEligibilityResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/deletion-eligibility`);
  },

  delete(worldId: string, confirmationTitle: string): Promise<RpgWorldDeletionResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirmation_title: confirmationTitle,
        acknowledge_permanent: true,
      }),
    });
  },
};
