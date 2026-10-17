import { pipelineFetch } from '../../../api/fetchPipeline';
import type { components } from './generated';
export type RpgWorldGenerationReviewIssue = components['schemas']['RpgWorldGenerationReviewIssue'];

export type RpgWorldGenerationReviewWaiver = components['schemas']['RpgWorldGenerationReviewWaiver'];

export type RpgWorldGenerationValidationAttempt = components['schemas']['RpgWorldGenerationValidationAttempt'];

export type RpgWorldGenerationFindingFingerprint = components['schemas']['RpgWorldGenerationFindingFingerprint'];

export type RpgWorldGenerationRepairEvaluation = components['schemas']['RpgWorldGenerationRepairEvaluation'];

export type RpgWorldGenerationReviewReport = components['schemas']['RpgWorldGenerationReviewReport'];

export type RpgWorldGenerationReviewState = components['schemas']['RpgWorldGenerationReviewState'];

export type RpgWorldGenerationReviewDecision = components['schemas']['RpgWorldGenerationReviewDecision'];

export type RpgWorldGenerationFailureArtifact = components['schemas']['RpgWorldGenerationFailureArtifact'];

export type RpgWorldGenerationTopicResult = components['schemas']['RpgWorldGenerationTopicResult'];

export type RpgWorldGenerationReviewAnalytics = components['schemas']['RpgWorldGenerationReviewAnalytics'];

type ReviewListResponse = components['schemas']['RpgWorldGenerationReviewListResponse'];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await pipelineFetch(path, init);
  const body = await response.text();
  if (!response.ok) {
    let message = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown; error?: unknown };
      message = JSON.stringify(parsed.detail ?? parsed.error ?? parsed);
    } catch {
      // Preserve the raw body.
    }
    throw new Error(`World generation review request failed (${response.status}): ${message}`);
  }
  return (body ? JSON.parse(body) : {}) as T;
}

export const rpgWorldGenerationReviewClient = {
  list(runId: string): Promise<ReviewListResponse> {
    return request(`/api/rpg/world-generation/${encodeURIComponent(runId)}/results`);
  },

  topic(runId: string, topicId: string): Promise<{
    ok: boolean;
    parent_run_id?: string | null;
    topic_result: RpgWorldGenerationTopicResult;
  }> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/results/${encodeURIComponent(topicId)}`,
    );
  },

  retry(
    runId: string,
    body: {
      topic_ids?: string[];
      retry_scopes?: Record<string, Record<string, unknown>>;
    } = {},
  ): Promise<Record<string, unknown>> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/retry-review`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
  },

  accept(
    runId: string,
    topicId: string,
    body: {
      candidate?: Record<string, unknown>;
      expected_candidate_hash?: string;
      waiver_reason?: string;
    } = {},
  ): Promise<Record<string, unknown>> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/results/${encodeURIComponent(topicId)}/accept`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
  },

  acceptAll(
    runId: string,
    body: {
      topic_ids?: string[];
      waiver_reason?: string;
      waiver_reasons?: Record<string, string>;
    } = {},
  ): Promise<Record<string, unknown>> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/accept-all`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
  },

  decide(
    runId: string,
    topicId: string,
    decision: 'keep' | 'replace',
  ): Promise<Record<string, unknown>> {
    return request(
      `/api/rpg/world-generation/${encodeURIComponent(runId)}/results/${encodeURIComponent(topicId)}/decision`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      },
    );
  },
};
