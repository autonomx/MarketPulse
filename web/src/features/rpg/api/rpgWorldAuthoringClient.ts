import type { RpgWorldSummary } from './rpgWorldLibraryClient';
import { pipelineFetch } from '../../../api/fetchPipeline';

import type { components } from './generated';
export type RpgAuthoringGroup = 'workspace' | 'world' | 'lore' | 'game-master';
export type RpgAuthoringPageKind = 'document' | 'collection';

export type RpgAuthoringSection = components['schemas']['RpgAuthoringSection'];

export type RpgWorldTokenUsage = components['schemas']['RpgWorldTokenUsage'];

export type RpgAuthoringManifestResponse = components['schemas']['RpgAuthoringManifestResponse'];

export type RpgAuthoringCardHighlight = components['schemas']['RpgAuthoringCardHighlight'];

export type RpgAuthoringCardGroup = components['schemas']['RpgAuthoringCardGroup'];

export type RpgAuthoringCardPresentation = components['schemas']['RpgAuthoringCardPresentation'];

export type RpgAuthoringDossierQuote = components['schemas']['RpgAuthoringDossierQuote'];

export type RpgAuthoringDossierQuickFact = components['schemas']['RpgAuthoringDossierQuickFact'];

export type RpgAuthoringDossierSection = components['schemas']['RpgAuthoringDossierSection'];

export type RpgAuthoringEntityDossier = components['schemas']['RpgAuthoringEntityDossier'];

export type RpgAuthoringEntityCard = components['schemas']['RpgAuthoringEntityCard'];

export type RpgAuthoringDocumentBlock = components['schemas']['RpgAuthoringDocumentBlock'];

export type RpgAuthoringTopic = components['schemas']['RpgAuthoringTopic'];

export interface RpgAuthoringTopicHistory extends RpgAuthoringTopic {
  history_sequence: number;
  captured_at: string;
  topic_updated_at: string;
}

export interface RpgAuthoringEntityHistory {
  history_sequence: number;
  operation: 'manual_edit' | 'regenerate' | 'manual_dossier_edit' | 'regenerate_dossier' | 'restore' | string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  topic_content_hash: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface RpgAuthoringDocumentPage {
  ok: boolean;
  section_id: string;
  page_kind: 'document';
  title: string;
  summary?: string;
  body: RpgAuthoringDocumentBlock[];
  related_entities: Array<Record<string, unknown>>;
  topic?: RpgAuthoringTopic;
}

export interface RpgAuthoringCollectionPage {
  ok: boolean;
  section_id: string;
  page_kind: 'collection';
  title: string;
  entities: RpgAuthoringEntityCard[];
  filters: Array<Record<string, unknown>>;
  sort_options: string[];
  topic?: RpgAuthoringTopic;
}

export type RpgAuthoringEntityMutationResponse = components['schemas']['RpgAuthoringEntityMutationResponse'];

export type RpgAuthoringDossierPreviewResponse = components['schemas']['RpgAuthoringDossierPreviewResponse'];

export type RpgDossierQualityMetrics = components['schemas']['RpgDossierQualityMetrics'];

export type RpgDossierQualityTopicMetrics = components['schemas']['RpgDossierQualityTopicMetrics'];

export type RpgDossierEnrichmentCandidate = components['schemas']['RpgDossierEnrichmentCandidate'];

// The route checks these fields itself; the client sends them in this shape.
export type RpgDossierEnrichmentRequest = components['schemas']['RpgEnrichWorldDossiersRequestBody'] & {
  candidates?: Array<Pick<RpgDossierEnrichmentCandidate, 'topic_id' | 'entity_id'>>;
  directives?: Record<string, unknown>;
  limit?: number;
};

export type RpgWorldDossierQualityResponse = components['schemas']['RpgWorldDossierQualityResponse'];

export type RpgWorldDossierEnrichmentResponse = components['schemas']['RpgWorldDossierEnrichmentResponse'];

export type RpgAuthoringPage = RpgAuthoringDocumentPage | RpgAuthoringCollectionPage;

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
      // Preserve the raw response.
    }
    throw new Error(`Omnix API request failed with status ${response.status}${detail ? `: ${detail}` : ''}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

function jsonPatch(body: Record<string, unknown>): RequestInit {
  return {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function jsonPost(body: object): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

function entityPath(worldId: string, topicId: string, entityId: string): string {
  return `/api/rpg/worlds/${encodeURIComponent(worldId)}/topics/${encodeURIComponent(topicId)}/entities/${encodeURIComponent(entityId)}`;
}

export const rpgWorldAuthoringClient = {
  manifest(worldId: string): Promise<RpgAuthoringManifestResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/authoring-manifest`);
  },

  section(worldId: string, sectionId: string): Promise<RpgAuthoringPage> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/authoring-sections/${encodeURIComponent(sectionId)}`,
    );
  },

  dossierQuality(worldId: string): Promise<RpgWorldDossierQualityResponse> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/dossier-quality`);
  },

  enrichDossiers(
    worldId: string,
    body: RpgDossierEnrichmentRequest,
  ): Promise<RpgWorldDossierEnrichmentResponse> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/enrich-dossiers`,
      jsonPost(body),
    );
  },

  updateWorld(
    worldId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; world: RpgWorldSummary }> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}`, jsonPatch(body));
  },

  topic(worldId: string, topicId: string): Promise<{
    ok: boolean;
    world: RpgWorldSummary;
    topic: RpgAuthoringTopic;
    history: RpgAuthoringTopicHistory[];
  }> {
    return request(`/api/rpg/worlds/${encodeURIComponent(worldId)}/topics/${encodeURIComponent(topicId)}`);
  },

  updateTopic(
    worldId: string,
    topicId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; topic: RpgAuthoringTopic; stale_topic_ids: string[] }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/topics/${encodeURIComponent(topicId)}`,
      jsonPatch(body),
    );
  },

  restoreTopic(
    worldId: string,
    topicId: string,
    body: Record<string, unknown>,
  ): Promise<{ ok: boolean; topic: RpgAuthoringTopic; stale_topic_ids: string[] }> {
    return request(
      `/api/rpg/worlds/${encodeURIComponent(worldId)}/topics/${encodeURIComponent(topicId)}/restore`,
      jsonPost(body),
    );
  },

  entity(worldId: string, topicId: string, entityId: string): Promise<{
    ok: boolean;
    world: RpgWorldSummary;
    topic: RpgAuthoringTopic;
    entity: Record<string, unknown>;
    history: RpgAuthoringEntityHistory[];
  }> {
    return request(entityPath(worldId, topicId, entityId));
  },

  updateEntity(
    worldId: string,
    topicId: string,
    entityId: string,
    body: Record<string, unknown>,
  ): Promise<RpgAuthoringEntityMutationResponse> {
    return request(entityPath(worldId, topicId, entityId), jsonPatch(body));
  },

  regenerateEntity(
    worldId: string,
    topicId: string,
    entityId: string,
    body: Record<string, unknown>,
  ): Promise<RpgAuthoringEntityMutationResponse> {
    return request(`${entityPath(worldId, topicId, entityId)}/regenerate`, jsonPost(body));
  },

  updateEntityDossier(
    worldId: string,
    topicId: string,
    entityId: string,
    body: Record<string, unknown>,
  ): Promise<RpgAuthoringEntityMutationResponse> {
    return request(`${entityPath(worldId, topicId, entityId)}/dossier`, jsonPatch(body));
  },

  previewEntityDossier(
    worldId: string,
    topicId: string,
    entityId: string,
    body: Record<string, unknown>,
  ): Promise<RpgAuthoringDossierPreviewResponse> {
    return request(
      `${entityPath(worldId, topicId, entityId)}/regenerate-dossier-preview`,
      jsonPost(body),
    );
  },

  regenerateEntityDossier(
    worldId: string,
    topicId: string,
    entityId: string,
    body: Record<string, unknown>,
  ): Promise<RpgAuthoringEntityMutationResponse> {
    return request(`${entityPath(worldId, topicId, entityId)}/regenerate-dossier`, jsonPost(body));
  },
};
