import { pipelineFetch } from '../../../api/fetchPipeline';
import type { components } from './generated';
export type RpgWorldProfilePresentation = components['schemas']['RpgWorldProfilePresentation'];

export type RpgWorldProfileTargetRange = components['schemas']['RpgWorldProfileTargetRange'];

export type RpgWorldProfileDomain = components['schemas']['RpgWorldProfileDomain'];

export type RpgWorldGenreProfile = components['schemas']['RpgWorldGenreProfile'];

export type RpgWorldProfileReview = components['schemas']['RpgWorldProfileReview'];

type RpgWorldProfileResponse = components['schemas']['RpgWorldProfileResponse'];

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

function json(method: 'PATCH' | 'POST', body: Record<string, unknown>): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function profilePath(worldId: string): string {
  return `/api/rpg/worlds/${encodeURIComponent(worldId)}/genre-profile`;
}

export const rpgWorldProfileClient = {
  read(worldId: string): Promise<RpgWorldProfileResponse> {
    return request(profilePath(worldId));
  },

  update(
    worldId: string,
    expectedProfileRevision: number,
    profile: RpgWorldGenreProfile,
  ): Promise<RpgWorldProfileResponse> {
    return request(profilePath(worldId), json('PATCH', {
      expected_profile_revision: expectedProfileRevision,
      profile,
    }));
  },

  approve(
    worldId: string,
    expectedProfileRevision: number,
  ): Promise<RpgWorldProfileResponse> {
    return request(`${profilePath(worldId)}/approve`, json('POST', {
      expected_profile_revision: expectedProfileRevision,
    }));
  },

  retry(worldId: string): Promise<RpgWorldProfileResponse> {
    return request(`${profilePath(worldId)}/retry`, { method: 'POST' });
  },
};
