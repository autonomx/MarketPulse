import type { components } from './api/generated';
import { unwrap } from '../../api/http';
import type { Chapter, ExportRecord, ProjectDetail, ProjectSummary, SourceLibrary, VoiceRecord } from './audiobookTypes';
import { api } from './api/gateway';

/**
 * The audiobook routes (WP-9.3). Paths, parameters and bodies are checked
 * against the gateway contract; responses are untyped objects there, so each
 * call returns the view type the workspace reads.
 */
type Schemas = components['schemas'];
type Result<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

async function view<T>(call: Result<unknown>): Promise<T> {
  return (await unwrap(call)) as T;
}

const project = (projectId: string) => ({ project_id: projectId });

export type AudiobookModel = { provider_id: string; model_id: string; model_revision: string };
export type AudiobookJobAction = 'pause' | 'resume' | 'cancel' | 'retry';
export type AudiobookRenderAction = 'pause' | 'resume' | 'stop';

export const audiobookApi = {
  projects: (offset = 0) =>
    view<{ projects: ProjectSummary[] }>(api.GET('/api/audiobook/projects', { params: { query: offset ? { offset } : {} } })),
  createProject: (body: Schemas['CreateAudiobookProject']) =>
    view<ProjectSummary>(api.POST('/api/audiobook/projects', { body })),
  project: (projectId: string) =>
    view<ProjectDetail>(api.GET('/api/audiobook/projects/{project_id}', { params: { path: project(projectId) } })),
  chapter: (projectId: string, chapterId: string) =>
    view<Chapter>(api.GET('/api/audiobook/projects/{project_id}/chapters/{chapter_id}', {
      params: { path: { project_id: projectId, chapter_id: chapterId } },
    })),
  voices: () => view<{ voices: VoiceRecord[] }>(api.GET('/api/audiobook/voices')),
  currentModel: () => view<AudiobookModel>(api.GET('/api/audiobook/models/current')),
  exports: (projectId: string) =>
    view<{ exports: ExportRecord[] }>(api.GET('/api/audiobook/projects/{project_id}/exports', { params: { path: project(projectId) } })),
  startExport: (projectId: string, body: Schemas['StartExport']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/exports', { params: { path: project(projectId) }, body })),
  sourceLibrary: () => view<SourceLibrary>(api.GET('/api/audiobook/source-library')),
  saveClassificationRules: (projectId: string, customRules: string) =>
    view<{ classification_rules: string }>(api.POST('/api/audiobook/projects/{project_id}/classification-rules', {
      params: { path: project(projectId) },
      body: { custom_rules: customRules },
    })),
  reclassify: (projectId: string, customRules: string) =>
    view<{ job_id: string }>(api.POST('/api/audiobook/projects/{project_id}/reclassify', {
      params: { path: project(projectId) },
      body: { custom_rules: customRules },
    })),
  extractQuotes: (projectId: string, customRules: string) =>
    view<{ job_id: string }>(api.POST('/api/audiobook/projects/{project_id}/extract-quotes', {
      params: { path: project(projectId) },
      body: { custom_rules: customRules },
    })),
  preview: (projectId: string, body: Schemas['StartPreview']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/preview', { params: { path: project(projectId) }, body })),
  startRender: (projectId: string, body: Schemas['StartRender']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/render', { params: { path: project(projectId) }, body })),
  render: (projectId: string, action: AudiobookRenderAction) => {
    const params = { params: { path: project(projectId) } };
    if (action === 'pause') return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/render/pause', params));
    if (action === 'resume') return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/render/resume', params));
    return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/render/stop', params));
  },
  /** Render these waiting chapters first, in this order; the others follow in their current order. */
  reorderRender: (projectId: string, chapterIds: string[]) =>
    view<{ render_run_id: string; chapter_ids: string[] }>(api.POST('/api/audiobook/projects/{project_id}/render/order', {
      params: { path: project(projectId) }, body: { chapter_ids: chapterIds },
    })),
  /** Where a finished chapter's audio streams from (seekable). */
  chapterAudioUrl: (projectId: string, chapterId: string) =>
    `/api/audiobook/projects/${encodeURIComponent(projectId)}/chapters/${encodeURIComponent(chapterId)}/audio`,
  job: (projectId: string, jobId: string, action: AudiobookJobAction) => {
    const params = { params: { path: { project_id: projectId, job_id: jobId } } };
    if (action === 'pause') return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/jobs/{job_id}/pause', params));
    if (action === 'resume') return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/jobs/{job_id}/resume', params));
    if (action === 'cancel') return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/jobs/{job_id}/cancel', params));
    return view<unknown>(api.POST('/api/audiobook/projects/{project_id}/jobs/{job_id}/retry', params));
  },
  createSpeaker: (projectId: string, body: Schemas['CreateSpeaker']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/speakers', { params: { path: project(projectId) }, body })),
  rejectSpeaker: (projectId: string, speakerId: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/speakers/{speaker_id}/reject', {
      params: { path: { project_id: projectId, speaker_id: speakerId } },
    })),
  addAlias: (projectId: string, speakerId: string, alias: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/speakers/{speaker_id}/aliases', {
      params: { path: { project_id: projectId, speaker_id: speakerId } },
      body: { alias },
    })),
  unconfirmAlias: (projectId: string, speakerId: string, alias: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/speakers/{speaker_id}/aliases/unconfirm', {
      params: { path: { project_id: projectId, speaker_id: speakerId } },
      body: { alias },
    })),
  confirmSuggestedAliases: (projectId: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/aliases/confirm-suggested', {
      params: { path: project(projectId) },
    })),
  assignVoice: (projectId: string, speakerId: string, voiceProfileId: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/speakers/{speaker_id}/casting', {
      params: { path: { project_id: projectId, speaker_id: speakerId } },
      body: { voice_profile_id: voiceProfileId },
    })),
  setPerformance: (projectId: string, body: Schemas['BookPerformance-Input']) =>
    view<{ performance: Schemas['BookPerformance']; changed: boolean }>(api.PUT('/api/audiobook/projects/{project_id}/performance', {
      params: { path: project(projectId) }, body,
    })),
  setAutoDirect: (projectId: string, policy: Schemas['SetAutoDirect']['policy']) =>
    view<unknown>(api.PUT('/api/audiobook/projects/{project_id}/auto-direct', {
      params: { path: project(projectId) }, body: { policy },
    })),
  directPerformance: (projectId: string, chapterIds: string[] = []) =>
    view<{ job_id: string }>(api.POST('/api/audiobook/projects/{project_id}/direct-performance', {
      params: { path: project(projectId) }, body: { chapter_ids: chapterIds },
    })),
  setPronunciation: (projectId: string, body: Schemas['SetPronunciation']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/pronunciations', { params: { path: project(projectId) }, body })),
  excludeSpeech: (projectId: string, spanId: string, body: Schemas['ExcludeSpanText']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/spans/{span_id}/speech-exclusions', {
      params: { path: { project_id: projectId, span_id: spanId } },
      body,
    })),
  removeChapterFromAudio: (projectId: string, chapterId: string) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/chapters/{chapter_id}/speech-exclusion', {
      params: { path: { project_id: projectId, chapter_id: chapterId } },
    })),
  restoreSpeech: (projectId: string, exclusionId: string) =>
    view<unknown>(api.DELETE('/api/audiobook/projects/{project_id}/speech-exclusions/{exclusion_id}', {
      params: { path: { project_id: projectId, exclusion_id: exclusionId } },
    })),
  reviseSpan: (projectId: string, spanId: string, body: Schemas['ReviseSpanAnnotation']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/spans/{span_id}/annotation', {
      params: { path: { project_id: projectId, span_id: spanId } },
      body,
    })),
  resolveReview: (projectId: string, issueId: string, body: Schemas['ResolveReviewIssue']) =>
    view<unknown>(api.POST('/api/audiobook/projects/{project_id}/review/{issue_id}', {
      params: { path: { project_id: projectId, issue_id: issueId } },
      body,
    })),
};
