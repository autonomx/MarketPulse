import { useState } from 'react';
import { LibrarySection, WorkspaceAsset } from './audiobookWorkspaceModel';
import { useSearchParam } from '../../shared/useSearchParam';

/** The workspace's UI state: selection, open panels, filters, form fields and status. */
export function useAudiobookState() {
  // The open project is in the URL (?project=), so it can be linked to (WP-9.6).
  const [projectId, setProjectId] = useSearchParam('project');
  const [ebookLibraryOpen, setEbookLibraryOpen] = useState(false);
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  const [expandedSpanId, setExpandedSpanId] = useState<string | null>(null);
  const [speakerFilter, setSpeakerFilter] = useState('all');
  const [selectedPronunciation, setSelectedPronunciation] = useState<{ spanId: string } | null>(null);
  const [selectedRemoval, setSelectedRemoval] = useState<{ spanId: string; start_offset: number; end_offset: number; source_text: string } | null>(null);
  const [workspaceMode, setWorkspaceMode] = useState<'review' | 'production'>('review');
  const [librarySection, setLibrarySection] = useState<LibrarySection>('projects');
  const [projectSettingsOpen, setProjectSettingsOpen] = useState(false);
  const [sourceUploadProjectId, setSourceUploadProjectId] = useState<string | null>(null);
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);
  const [assetDeleteConfirmation, setAssetDeleteConfirmation] = useState<WorkspaceAsset | null>(null);
  const [mobileRail, setMobileRail] = useState<'library' | 'outline' | null>(null);
  const [title, setTitle] = useState('');
  const [author, setAuthor] = useState('');
  const [language, setLanguage] = useState('en');
  const [projectTitle, setProjectTitle] = useState('');
  const [projectAuthor, setProjectAuthor] = useState('');
  const [speakerName, setSpeakerName] = useState('');
  const [classificationRules, setClassificationRules] = useState<Record<string, string>>({});
  const [createClassificationRules, setCreateClassificationRules] = useState('');
  const [sourceTerm, setSourceTerm] = useState('');
  const [spokenTerm, setSpokenTerm] = useState('');
  const [excludePageRanges, setExcludePageRanges] = useState('');
  const [createExcludePageRanges, setCreateExcludePageRanges] = useState('');
  const [pendingSource, setPendingSource] = useState<File | null>(null);
  const [sourceText, setSourceText] = useState('');
  const [sourceIntent, setSourceIntent] = useState<'file' | 'paste' | 'blank'>('file');
  const [sourceLibraryFilename, setSourceLibraryFilename] = useState('');
  const [sourceLibraryOpen, setSourceLibraryOpen] = useState(false);
  const [exportFormat, setExportFormat] = useState('m4b');
  const [reviewSpeakers, setReviewSpeakers] = useState<Record<string, string>>({});
  const [spanEdits, setSpanEdits] = useState<Record<string, { speaker_id: string; role: string; delivery: string }>>({});
  const [aliasNames, setAliasNames] = useState<Record<string, string>>({});
  const [voiceTargetSpeakerId, setVoiceTargetSpeakerId] = useState('');
  const [selectedVoiceId, setSelectedVoiceId] = useState('');
  const [bookSearch, setBookSearch] = useState('');
  const [ebookLibrarySearch, setEbookLibrarySearch] = useState('');
  const [ebookLibraryFormat, setEbookLibraryFormat] = useState('all');
  const [ebookLibraryFilter, setEbookLibraryFilter] = useState('all');
  const [ebookLibrarySort, setEbookLibrarySort] = useState<'recent' | 'title' | 'author'>('recent');
  const [ebookLibraryView, setEbookLibraryView] = useState<'grid' | 'list'>('grid');
  const [selectedLibraryProjectId, setSelectedLibraryProjectId] = useState<string | null>(null);
  const [chapterStatusFilter, setChapterStatusFilter] = useState('all');
  const [chapterView, setChapterView] = useState<'list' | 'grid'>('list');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetFilter, setAssetFilter] = useState('all');
  const [assetSort, setAssetSort] = useState<'recent' | 'name' | 'type'>('recent');
  const [selectedAssetId, setSelectedAssetId] = useState('');
  const [documentSearch, setDocumentSearch] = useState('');
  const [documentTypeFilter, setDocumentTypeFilter] = useState('all');
  const [documentView, setDocumentView] = useState<'list' | 'grid'>('list');
  const [selectedDocumentId, setSelectedDocumentId] = useState('manuscript');
  const [documentDetailTab, setDocumentDetailTab] = useState<'preview' | 'details' | 'versions'>('preview');
  const [pronunciationSearch, setPronunciationSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return {
    projectId, setProjectId, ebookLibraryOpen, setEbookLibraryOpen, chapterId, setChapterId, selectedSpanId,
    setSelectedSpanId, expandedSpanId, setExpandedSpanId, speakerFilter, setSpeakerFilter, selectedPronunciation,
    setSelectedPronunciation, selectedRemoval, setSelectedRemoval, workspaceMode, setWorkspaceMode, librarySection,
    setLibrarySection, projectSettingsOpen, setProjectSettingsOpen, sourceUploadProjectId, setSourceUploadProjectId,
    deleteConfirmationOpen, setDeleteConfirmationOpen, assetDeleteConfirmation, setAssetDeleteConfirmation,
    mobileRail, setMobileRail, title, setTitle, author, setAuthor, language, setLanguage, projectTitle,
    setProjectTitle, projectAuthor, setProjectAuthor, speakerName, setSpeakerName, classificationRules,
    setClassificationRules, createClassificationRules, setCreateClassificationRules, sourceTerm, setSourceTerm,
    spokenTerm, setSpokenTerm, excludePageRanges, setExcludePageRanges, createExcludePageRanges,
    setCreateExcludePageRanges, pendingSource, setPendingSource, sourceText, setSourceText, sourceIntent,
    setSourceIntent, sourceLibraryFilename, setSourceLibraryFilename, sourceLibraryOpen, setSourceLibraryOpen,
    exportFormat, setExportFormat, reviewSpeakers, setReviewSpeakers, spanEdits, setSpanEdits, aliasNames,
    setAliasNames, voiceTargetSpeakerId, setVoiceTargetSpeakerId, selectedVoiceId, setSelectedVoiceId, bookSearch,
    setBookSearch, ebookLibrarySearch, setEbookLibrarySearch, ebookLibraryFormat, setEbookLibraryFormat,
    ebookLibraryFilter, setEbookLibraryFilter, ebookLibrarySort, setEbookLibrarySort, ebookLibraryView,
    setEbookLibraryView, selectedLibraryProjectId, setSelectedLibraryProjectId, chapterStatusFilter,
    setChapterStatusFilter, chapterView, setChapterView, assetSearch, setAssetSearch, assetFilter, setAssetFilter,
    assetSort, setAssetSort, selectedAssetId, setSelectedAssetId, documentSearch, setDocumentSearch,
    documentTypeFilter, setDocumentTypeFilter, documentView, setDocumentView, selectedDocumentId,
    setSelectedDocumentId, documentDetailTab, setDocumentDetailTab, pronunciationSearch, setPronunciationSearch,
    busy, setBusy, error, setError, notice, setNotice,
  };
}
