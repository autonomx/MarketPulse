import type { ConversationProduction, ProductionAsset } from '../conversation-production';

export type PodcastFormat = 'debate' | 'interview' | 'speech' | 'roundtable';

export type PodcastOutputAssetKind = 'mp3' | 'wav' | 'transcript' | 'show_notes' | 'citations' | 'chapters' | 'bundle';

export type PodcastOutputAssetStatus = 'pending' | 'generating' | 'available' | 'failed';

export interface PodcastOutputAsset {
  id: string;
  kind: PodcastOutputAssetKind;
  label: string;
  description: string;
  status: PodcastOutputAssetStatus;
  downloadUrl?: string;
}

export interface PodcastRendererConfig {
  format: PodcastFormat;
  includeTranscript: boolean;
  includeShowNotes: boolean;
  includeCitations: boolean;
  includeChapters: boolean;
  exportFormats: PodcastOutputAssetKind[];
}

export interface PodcastDownloadAsset {
  kind: PodcastOutputAssetKind;
  label: string;
  metadata: string;
  icon: string;
}

export interface PodcastEpisode {
  id: string;
  productionId: string;
  title: string;
  brief: string;
  format: PodcastFormat;
  audience: string;
  durationSeconds: number;
  voiceCount: number;
  status: 'draft' | 'live' | 'completed' | 'failed';
  description: string;
  tags: string[];
  production: ConversationProduction;
  productionAssets: ProductionAsset[];
  outputAssets: PodcastOutputAsset[];
}

export const podcastFormatLabels: Record<PodcastFormat, string> = {
  debate: 'Debate',
  interview: 'Interview',
  speech: 'Speech',
  roundtable: 'Roundtable',
};

export const podcastDownloadAssets: PodcastDownloadAsset[] = [
  { kind: 'mp3', label: 'MP3', metadata: 'Audio file', icon: '♫' },
  { kind: 'wav', label: 'WAV', metadata: 'High quality', icon: '▥' },
  { kind: 'transcript', label: 'Transcript', metadata: 'TXT file', icon: '▤' },
  { kind: 'show_notes', label: 'Show Notes', metadata: 'Markdown', icon: '▣' },
  { kind: 'citations', label: 'Citations', metadata: 'BibTeX', icon: '⌁' },
  { kind: 'chapters', label: 'Chapters', metadata: 'JSON', icon: '§' },
];
