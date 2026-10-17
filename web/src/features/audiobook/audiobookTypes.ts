// View types of the audiobook routes, which return untyped objects in the
// gateway contract (WP-9.3); audiobookApi.ts casts responses to them.

export interface ProjectSummary {
  id: string;
  title: string;
  author: string;
  language: string;
  state: string;
  current_source_revision_id: string | null;
  cover_asset_id?: string | null;
  source_format?: string | null;
  source_filename?: string | null;
  source_size_bytes?: number;
  source_created_at?: string | null;
  cover_created_at?: string | null;
  chapters?: ChapterSummary[];
  speakers?: Speaker[];
  review_issues?: ReviewIssue[];
  render_progress?: { completed: number; total: number };
  word_count?: number;
  estimated_runtime_seconds?: number;
}

export interface Span {
  id: string;
  source_text: string;
  structural_kind: string;
  speech_exclusions?: { id: string; start_offset: number; end_offset: number; source_text: string }[];
  annotation: { id: string; role: string; speaker_id: string | null; speaker_candidate: string | null;
    delivery: string; review_status: string; evidence: Record<string, unknown> } | null;
  speech_plan: { tts_input_text: string; hash: string;
    transformations: { rule: string; source: string; spoken: string }[] };
}

export interface ChapterSummary {
  id: string;
  ordinal: number;
  title: string;
  character_count: number;
  span_count?: number;
  word_count?: number;
  estimated_runtime_seconds?: number;
  /** The exclusion that leaves this whole chapter out of the audiobook, when it is removed. */
  audio_removal_id?: string | null;
}

export interface DocumentBlockView {
  id: string;
  original_text: string;
  content_role?: string;
  effective_role: string;
  confidence: number;
  render_action: 'READ' | 'SKIP' | 'READ_ONCE';
  speaker_analysis_visibility: 'INCLUDE' | 'CONTEXT_ONLY' | 'EXCLUDE';
  provenance: { source?: string; signal?: string; value?: unknown }[];
}

export interface Chapter extends ChapterSummary {
  canonical_text: string;
  spans: Span[];
  document_blocks?: DocumentBlockView[];
  audiobook_mode?: 'standard' | 'story_only' | 'verbatim';
}

export interface ReviewIssue {
  id: string;
  span_id: string;
  reason: string;
  evidence: Record<string, unknown>;
  source_text: string;
  chapter_id: string;
  chapter_title: string;
  speaker_id: string | null;
  speaker_candidate: string | null;
  structural_kind: string;
}

export interface Speaker {
  id: string;
  canonical_name: string;
  kind: string;
  status?: 'active' | 'proposed' | string;
  occurrence_count?: number;
  analysis_metadata?: {
    role?: string;
    traits?: string[];
    estimated_age?: string;
    gender_presentation?: string;
  };
  casting: { voice_profile_id: string; voice_revision_hash: string; revision: number } | null;
  aliases: string[];
  proposed_aliases?: string[];
}

export interface AudiobookJobView {
  id: string;
  status: string;
  chapter_id?: string;
  progress?: {
    current?: number;
    total?: number;
    unit_current?: number;
    unit_total?: number;
    message?: string;
    cache_hits?: number;
    generated?: number;
    /** Direct performance: lines given a direction, lines changed, user notes kept. */
    directed?: number;
    changed?: number;
    kept_user_delivery?: number;
    failed_windows?: number;
  };
  error?: { message?: string; code?: string; retryable?: boolean } | null;
  attempts?: number;
  max_attempts?: number;
  can_retry?: boolean;
  pause_requested?: boolean;
  paused?: boolean;
  format?: string;
  span_id?: string;
  type?: string;
  reason?: string;
  migration?: Record<string, unknown> | null;
  /** A waiting render job's place in the render order, 1 first; absent once it starts. */
  queue_position?: number | null;
  /** A rendered chapter's assembled audio, once it can be played. */
  audio?: { duration_seconds: number } | null;
}

/** Stability and Style Exaggeration (0–1) for the book, and characters' own levels. */
export interface BookPerformance {
  stability: number;
  style: number;
  speakers: Record<string, { stability?: number; style?: number }>;
}

export interface ProjectDetail extends ProjectSummary {
  performance?: BookPerformance;
  /** After classifying: direct the dialogue always, only while Fish S2 Pro is the TTS engine, or never. */
  auto_direct_performance?: 'always' | 'fish' | 'never';
  render_readiness?: { ready: boolean; blockers: string[] };
  classification_rules?: string;
  quote_extraction_rules?: string | null;
  cover_asset_id: string | null;
  cover_created_at?: string | null;
  source_created_at?: string | null;
  chapters: ChapterSummary[];
  review_issues: ReviewIssue[];
  speakers: Speaker[];
  render_jobs: AudiobookJobView[];
  pipeline_jobs?: AudiobookJobView[];
  preview_jobs: AudiobookJobView[];
  export_jobs: AudiobookJobView[];
  render_progress: { completed: number; total: number };
  pronunciations: { source_term: string; spoken_term: string; revision: number }[];
  word_count?: number;
  estimated_runtime_seconds?: number;
  actual_runtime_seconds?: number;
  audiobook_mode?: 'standard' | 'story_only' | 'verbatim';
  document_structure_version?: string;
  render_policy_version?: string;
  analysis_policy_version?: string;
}

export interface ExportRecord {
  id: string;
  format: string;
  manifest_hash: string;
  asset_id: string;
  created_at: string;
  asset_created_at?: string;
  byte_size: number;
}

export interface VoiceRecord { id: string; name: string; language: string }

export interface SourceLibraryFile { name: string; source_format: string; size_bytes: number }

export interface SourceLibrary { directory: string; files: SourceLibraryFile[] }
