import { audiobookApi } from './audiobookApi';
import { LibrarySection, base, documentRoleOptions, formatAssetCreationDate, formatCount, formatDuration, formatStorage, libraryCardStatus, libraryProjectStatus, setDocumentBlockAction } from './audiobookWorkspaceModel';
import { reviewReasonLabel } from './AudiobookReviewQueue';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

/** The library rail: sections, a new audiobook, and the projects. */
export function AudiobookLibraryRail({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    ebookLibraryOpen, librarySection, mobileRail, navigateLibrary, openEbookLibrary, openProject, projectId,
    projectsQuery, setMobileRail, startNewProject,
  } = ws;
  return (
    <>
      <aside id="audiobook-library-rail" className={`audiobook-rail audiobook-library${mobileRail === 'library' ? ' mobile-open' : ''}`} aria-label="Audiobook projects">
        <button className="audiobook-mobile-close" type="button" onClick={() => setMobileRail(null)}>Close library</button>
        <div className="audiobook-panel-heading"><p className="eyebrow">Library</p><h2>Audiobooks</h2></div>
        <nav className="audiobook-library-nav" aria-label="Audiobook sections">
          {([['library', 'Library'], ['projects', 'Projects'], ['books', 'Books'], ['characters', 'Characters'], ['voices', 'Voices'], ['pronunciations', 'Pronunciations'], ['exports', 'Exports']] as [LibrarySection, string][]).map(([section, label]) => {
            const activeSideSection = ebookLibraryOpen ? 'library' : ['projects', 'chapters', 'assets', 'documents', 'exports'].includes(librarySection) ? 'projects' : librarySection;
            return <button key={section} type="button" className={activeSideSection === section ? 'selected' : ''}
              aria-current={activeSideSection === section ? 'page' : undefined} onClick={() => section === 'library' || (section === 'books' && !projectId) ? openEbookLibrary() : navigateLibrary(section)}>{label}</button>;
          })}
        </nav>
        <section className="audiobook-drafts">
          <p className="eyebrow">Create</p>
          <div className="audiobook-draft-card"><strong>New audiobook</strong><p>Create a project and upload a source book.</p><button type="button" onClick={startNewProject}>Start new project</button></div>
        </section>
        <div className="audiobook-project-list" id="audiobook-projects">
          <p className="eyebrow">Projects</p>
          {projectsQuery.isLoading && <p>Loading projects…</p>}
          {projectsQuery.isError && <p role="alert">Could not load projects.</p>}
          {projectsQuery.data?.projects.map((item) => (
            <button type="button" key={item.id} className={item.id === projectId ? 'selected' : ''}
              onClick={() => openProject(item)}>
              <strong>{item.title}</strong><small>{item.author || 'Unknown author'} · {item.state.replaceAll('_', ' ')}</small>
            </button>
          ))}
          {projectsQuery.data?.projects.length === 0 && <p>Start with a source book.</p>}
        </div>
      </aside>
    </>
  );
}

/** The inspector rail: details of the selection, status, cast, review queue and pronunciations. */
export function AudiobookInspectorRail({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    action, activeChapterId, aliasNames, busy, completedRenderChapters, documentDetailTab, ebookLibraryOpen,
    exportFormat, findSpeakerSpan, getInstalledModelRevision, libraryProjects, librarySection, mobileRail,
    navigateLibrary, openDocument, openProject, openSourceUpload, project, queueChapterPreview,
    renderChapterCount, selectedAssetId, selectedChapter, selectedDocumentId, selectedLibraryProject,
    selectedVoice, setAliasNames, setAssetDeleteConfirmation, setChapterId, setDocumentDetailTab,
    setEbookLibraryOpen, setExportFormat, setLibrarySection, setMobileRail, setProjectSettingsOpen,
    setSelectedSpanId, setSourceTerm, setSpeakerName, setSpokenTerm, setWorkspaceMode,
    skippedRenderChapterCount, sourceTerm, speakerName, spokenTerm, startChapter, startNewProject,
    submitPronunciation, submitSpeaker, voiceTargetSpeakerId, voicesQuery, workspaceAssetList,
    workspaceDocumentList,
  } = ws;
  return (
    <>
      <aside id="audiobook-outline-rail" className={`audiobook-rail audiobook-inspector audiobook-inspector-${librarySection}${mobileRail === 'outline' ? ' mobile-open' : ''}`} aria-label={librarySection === 'voices' ? 'Voice details' : librarySection === 'characters' ? 'Character details' : librarySection === 'pronunciations' ? 'Pronunciation details' : 'Chapter and cast inspector'}>
        <button className="audiobook-mobile-close" type="button" onClick={() => setMobileRail(null)}>{ebookLibraryOpen ? 'Close book details' : 'Close outline'}</button>
        {ebookLibraryOpen && <section className="audiobook-library-selected-inspector" aria-label="Selected audiobook details">
          {selectedLibraryProject ? <>
            <div className="audiobook-library-selected-heading"><p className="eyebrow">Selected book</p><span className={`audiobook-status-pill library-status-${libraryCardStatus(selectedLibraryProject).toLowerCase().replaceAll(' ', '-')}`}>{libraryCardStatus(selectedLibraryProject)}</span></div>
            <div className="audiobook-library-selected-cover">{selectedLibraryProject.cover_asset_id
              ? <img src={`${base}/projects/${encodeURIComponent(selectedLibraryProject.id)}/cover`} alt={`Cover of ${selectedLibraryProject.title}`} />
              : <span aria-hidden="true">✦</span>}</div>
            <div className="audiobook-library-selected-copy"><h2>{selectedLibraryProject.title}</h2><p>{selectedLibraryProject.author || 'Author not set'}</p></div>
            <div className="audiobook-detail-list audiobook-library-selected-metadata">
              <p><strong>Source</strong><span>{selectedLibraryProject.source_filename || 'No source uploaded'}</span></p>
              <p><strong>Format</strong><span>{selectedLibraryProject.source_format?.toUpperCase() || '—'}</span></p>
              <p><strong>Chapters</strong><span>{formatCount(selectedLibraryProject.chapters?.length)}</span></p>
              <p><strong>Speakers</strong><span>{selectedLibraryProject.speakers ? formatCount(selectedLibraryProject.speakers.length) : '—'}</span></p>
              <p><strong>Runtime</strong><span>{formatDuration(selectedLibraryProject.estimated_runtime_seconds)}</span></p>
              <p><strong>Word count</strong><span>{formatCount(selectedLibraryProject.word_count)}</span></p>
              <p><strong>Source size</strong><span>{formatStorage(selectedLibraryProject.source_size_bytes ?? 0)}</span></p>
            </div>
            {selectedLibraryProject.render_progress?.total ? <div className="audiobook-library-selected-progress">
              <div><strong>Rendered units</strong><span>{selectedLibraryProject.render_progress.completed} / {selectedLibraryProject.render_progress.total}</span></div>
              <progress max={selectedLibraryProject.render_progress.total} value={selectedLibraryProject.render_progress.completed} />
            </div> : null}
            <div className="audiobook-library-selected-actions">
              <button type="button" className="audiobook-primary-action" onClick={() => openProject(selectedLibraryProject)}>Open Project</button>
              <button type="button" onClick={() => { openProject(selectedLibraryProject); setLibrarySection('exports'); setWorkspaceMode('production'); }}>Generate Audiobook</button>
              <div><button type="button" onClick={() => { openProject(selectedLibraryProject); setLibrarySection('exports'); setWorkspaceMode('production'); }}>Export</button><button type="button" onClick={() => { openProject(selectedLibraryProject); setProjectSettingsOpen(true); }}>Project Settings</button></div>
            </div>
            <section className="audiobook-library-activity" aria-label="Recent project activity">
              <div className="audiobook-library-activity-heading"><p className="eyebrow">Recent Activity</p></div>
              {selectedLibraryProject.source_created_at && <p><span aria-hidden="true">▤</span><span>Source book added<small>{formatAssetCreationDate(selectedLibraryProject.source_created_at)}</small></span></p>}
              {selectedLibraryProject.cover_created_at && <p><span aria-hidden="true">▣</span><span>Cover art updated<small>{formatAssetCreationDate(selectedLibraryProject.cover_created_at)}</small></span></p>}
              {!selectedLibraryProject.source_created_at && !selectedLibraryProject.cover_created_at && <p className="audiobook-detail-muted">No recent activity recorded.</p>}
            </section>
          </> : <>
            <div className="audiobook-library-selected-heading"><p className="eyebrow">Selected book</p></div>
            <h2>No audiobook selected</h2>
            <p className="audiobook-detail-muted">Create a project or adjust the filters to see its details here.</p>
            <button type="button" className="audiobook-primary-action" onClick={startNewProject}>＋ New audiobook</button>
          </>}
        </section>}
        {project && librarySection === 'voices' && <section className="audiobook-special-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Voice details</p><h2>{selectedVoice?.name ?? 'Choose a voice'}</h2></div><p className="audiobook-detail-muted">{selectedVoice?.language || 'Language unspecified'}</p><p className="audiobook-special-copy">Preview an installed voice and assign it to the selected speaker.</p><button type="button" className="audiobook-primary-action" disabled={busy || !voiceTargetSpeakerId || !selectedVoice} onClick={() => void action(async () => { if (selectedVoice) await audiobookApi.assignVoice(project.id, voiceTargetSpeakerId, selectedVoice.id); }, 'Voice assigned.')}>Use this voice</button></section>}
        {project && librarySection === 'characters' && <section className="audiobook-special-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Character details</p><h2>{project.speakers[0]?.canonical_name ?? 'No characters yet'}</h2></div>{project.speakers[0] ? <><p className="audiobook-detail-muted">{project.speakers[0].kind} · {project.speakers[0].casting ? 'Voice assigned' : 'Needs review'}</p><p className="audiobook-special-copy">Character registry and casting for your audiobook. Use the main cast view to assign voices, aliases, and auditions.</p><div className="audiobook-detail-list"><p><strong>Voice assignment</strong><span>{project.speakers[0].casting?.voice_profile_id ?? 'Unassigned'}</span></p><p><strong>Aliases</strong><span>{project.speakers[0].aliases?.length ?? 0}</span></p><p><strong>Review issues</strong><span>{project.review_issues.length}</span></p></div><button type="button" onClick={() => navigateLibrary('voices')}>Manage voice assignment</button></> : <p>No speakers have been detected yet.</p>}</section>}
        {project && librarySection === 'pronunciations' && <section className="audiobook-special-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Pronunciation details</p><h2>{sourceTerm || 'Select a term'}</h2></div><form className="audiobook-form" onSubmit={submitPronunciation}><label>Source term<input value={sourceTerm} onChange={(event) => setSourceTerm(event.target.value)} placeholder="Hollow Bay" /></label><label>Spoken term<input value={spokenTerm} onChange={(event) => setSpokenTerm(event.target.value)} placeholder="Hollow Bay" /></label><button className="audiobook-primary-action" disabled={busy || !sourceTerm.trim() || !spokenTerm.trim()}>Save changes</button></form><p className="audiobook-detail-muted">Leave affected chapters blank to apply this pronunciation throughout the book.</p></section>}
         {project ? <section className="audiobook-outline-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Outline</p><h2>Chapters</h2></div>
          <div className="audiobook-outline">{project.chapters.map((chapter) => {
            const issues = project.review_issues.filter((issue) => issue.chapter_id === chapter.id).length;
            const render = project.render_jobs.find((job) => job.chapter_id === chapter.id);
            const removed = Boolean(chapter.audio_removal_id);
            return <button type="button" key={chapter.id} className={[chapter.id === activeChapterId ? 'selected' : '', removed ? 'removed' : ''].filter(Boolean).join(' ')} onClick={() => { setChapterId(chapter.id); setLibrarySection('projects'); setWorkspaceMode('review'); }}><small>{String(chapter.ordinal + 1).padStart(2, '0')} · {removed ? 'removed from audio' : issues ? `${issues} to review` : render?.status || 'ready'}</small><span className="audiobook-outline-title">{chapter.title}</span></button>;
          })}
            {project.chapters.length === 0 && <p>No chapters yet.</p>}</div><button className="audiobook-outline-add" type="button" onClick={() => openSourceUpload(project.id)}>Open source upload</button></section> : ebookLibraryOpen ? <section className="audiobook-library-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Library summary</p><h2>Your audiobooks</h2></div><div className="audiobook-detail-list"><p><strong>Total audiobooks</strong><span>{libraryProjects.length}</span></p><p><strong>Ready to export</strong><span>{libraryProjects.filter((item) => libraryProjectStatus(item) === 'Ready').length}</span></p><p><strong>In progress</strong><span>{libraryProjects.filter((item) => libraryProjectStatus(item) === 'In progress').length}</span></p><p><strong>Needs review</strong><span>{libraryProjects.filter((item) => libraryProjectStatus(item) === 'Review required').length}</span></p></div><p className="audiobook-detail-muted">Select an audiobook to open its manuscript, chapters, voices, assets, documents, and delivery tools.</p><button type="button" className="audiobook-primary-action" onClick={() => { setEbookLibraryOpen(false); setLibrarySection('projects'); }}>＋ Create audiobook</button></section> : <section className="audiobook-get-started"><div className="audiobook-panel-heading"><p className="eyebrow">Get started</p><h2>Stories sound better here.</h2></div><ol><li><strong>Add your content</strong><small>Import a file, paste text, or start from a blank project.</small></li><li><strong>Set project details</strong><small>Choose title, author, language, and voice settings.</small></li><li><strong>Create and edit</strong><small>Review your manuscript, fine-tune narration, and make edits.</small></li><li><strong>Generate your audiobook</strong><small>Render and export in M4B, MP3, or other formats.</small></li></ol></section>}
         {project && librarySection === 'books' && <section className="audiobook-book-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Book details</p><h2>{project.title}</h2></div><div className="audiobook-detail-list"><p><strong>Title</strong><span>{project.title}</span></p><p><strong>Author</strong><span>{project.author || 'Unknown author'}</span></p><p><strong>Language</strong><span>{project.language === 'en' ? 'English' : project.language}</span></p><p><strong>Total words</strong><span>{formatCount(project.word_count)}</span></p><p><strong>Estimated runtime</strong><span>{formatDuration(project.estimated_runtime_seconds)}</span></p></div><div className="audiobook-quick-actions"><button type="button" className="audiobook-primary-action" onClick={() => navigateLibrary('exports')}>Open Render &amp; Export</button><button type="button" onClick={() => navigateLibrary('exports')}>Open exports</button><button type="button" onClick={() => setProjectSettingsOpen(true)}>⚙ Project Settings</button></div></section>}
         {project && librarySection === 'chapters' && <section className="audiobook-chapter-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Chapter summary</p><h2>{project.chapters.find((chapter) => chapter.id === activeChapterId)?.title ?? 'No chapter selected'}</h2></div><p>{project.chapters.find((chapter) => chapter.id === activeChapterId)?.character_count === undefined ? '—' : project.chapters.find((chapter) => chapter.id === activeChapterId)?.character_count.toLocaleString()} characters</p><p>{project.review_issues.filter((issue) => issue.chapter_id === activeChapterId).length} review issues</p><button type="button" className="audiobook-primary-action" onClick={() => startChapter(project.chapters.find((chapter) => chapter.id === activeChapterId) ?? project.chapters[0])} disabled={!activeChapterId}>▣ Open in editor</button><div className="audiobook-quick-actions"><button type="button" onClick={() => setProjectSettingsOpen(true)}>Replace source</button><button type="button" disabled={busy || !activeChapterId} onClick={() => { const chapter = project.chapters.find((item) => item.id === activeChapterId); if (chapter) void action(() => queueChapterPreview(chapter), 'Chapter preview queued.'); }}>Preview first span</button></div>{selectedChapter?.document_blocks?.some((block) => block.effective_role !== 'story_text' || block.render_action !== 'READ') && <div role="group" className="audiobook-detail-list" aria-label="Audiobook structural content"><p><strong>Reading policy overrides</strong><span>{selectedChapter.document_blocks.filter((block) => block.effective_role !== 'story_text' || block.render_action !== 'READ').length}</span></p>{selectedChapter.document_blocks.filter((block) => block.effective_role !== 'story_text' || block.render_action !== 'READ').slice(0, 12).map((block) => <p key={block.id} title={block.provenance.map((item) => `${item.source ?? 'rule'}:${item.signal ?? 'evidence'}`).join(', ')}><strong>{block.original_text || '(blank block)'}</strong><span>{block.effective_role.replaceAll('_', ' ')} · {block.render_action.replaceAll('_', ' ').toLowerCase()} · {Math.round(block.confidence * 100)}%</span><label>Role<select aria-label={`Role for ${block.original_text}`} value={block.effective_role} disabled={busy} onChange={(event) => void action(() => setDocumentBlockAction(project.id, block.id, 'DEFAULT', event.target.value), `Block role changed to ${event.target.value.replaceAll('_', ' ')}.`)}>{documentRoleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="button" disabled={busy || block.render_action === 'READ'} aria-label={`Read block ${block.original_text}`} onClick={() => void action(() => setDocumentBlockAction(project.id, block.id, 'READ'), 'Block will be read.')}>Read</button><button type="button" disabled={busy || block.render_action === 'SKIP'} aria-label={`Skip block ${block.original_text}`} onClick={() => void action(() => setDocumentBlockAction(project.id, block.id, 'SKIP'), 'Block will be skipped.')}>Skip</button><button type="button" disabled={busy || block.render_action === 'READ_ONCE'} aria-label={`Read block once ${block.original_text}`} onClick={() => void action(() => setDocumentBlockAction(project.id, block.id, 'READ_ONCE'), 'Block will be read once.')}>Read once</button><button type="button" disabled={busy} aria-label={`Use automatic policy for ${block.original_text}`} onClick={() => void action(() => setDocumentBlockAction(project.id, block.id, 'DEFAULT'), 'Block returned to automatic policy.')}>Default</button></p>)}</div>}</section>}
         {project && librarySection === 'assets' && <section className="audiobook-asset-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Asset details</p><h2>{workspaceAssetList().find((asset) => asset.id === selectedAssetId)?.name ?? workspaceAssetList()[0]?.name ?? 'No assets yet'}</h2></div>{(() => { const asset = workspaceAssetList().find((item) => item.id === selectedAssetId) ?? workspaceAssetList()[0]; return asset ? <><div className="audiobook-asset-detail-preview">{asset.image && project.cover_asset_id ? <img src={`${base}/projects/${encodeURIComponent(project.id)}/cover`} alt="" /> : <span>{asset.type}</span>}</div><p className="audiobook-detail-muted">{asset.detail}</p><div className="audiobook-detail-list"><p><strong>Created</strong><time dateTime={asset.createdAt ?? undefined}>{formatAssetCreationDate(asset.createdAt)}</time></p><p><strong>Usage</strong><span>{asset.status} · {asset.chapters ?? 'Not linked'}</span></p><p><strong>Project</strong><span>{project.title}</span></p><p><strong>Format</strong><span>{asset.format ?? '—'}</span></p></div><div className="audiobook-card-actions">{asset.href && <a className="audiobook-button-link" href={asset.href} download>Download</a>}{asset.deletable && <button type="button" className="audiobook-danger-action" disabled={busy} onClick={() => setAssetDeleteConfirmation(asset)}>Delete</button>}</div></> : <p>No assets are associated with this project yet.</p>; })()}</section>}
         {project && librarySection === 'documents' && <section className="audiobook-document-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Document details</p><h2>{workspaceDocumentList().find((document) => document.id === selectedDocumentId)?.title ?? workspaceDocumentList()[0]?.title ?? 'No documents yet'}</h2></div>{(() => { const document = workspaceDocumentList().find((item) => item.id === selectedDocumentId) ?? workspaceDocumentList()[0]; return document ? <><div className="audiobook-detail-tabs">{(['preview', 'details', 'versions'] as const).map((tab) => <button key={tab} type="button" className={documentDetailTab === tab ? 'selected' : ''} onClick={() => setDocumentDetailTab(tab)}>{tab}</button>)}</div>{documentDetailTab === 'preview' && <div className="audiobook-document-preview"><h3>{project.title}</h3><p>{document.preview}</p></div>}{documentDetailTab === 'details' && <div className="audiobook-detail-list"><p><strong>Type</strong><span>{document.type}</span></p><p><strong>Linked chapters</strong><span>{document.linked}</span></p><p><strong>Author / source</strong><span>{document.author}</span></p><p><strong>Status</strong><span>{document.status}</span></p></div>}{documentDetailTab === 'versions' && <p className="audiobook-detail-muted">The source is immutable. Uploading a replacement creates a new source revision; generated views reflect current project state.</p>}<button type="button" className="audiobook-primary-action" onClick={() => { openDocument(document); }}>↗ Open section</button><div className="audiobook-card-actions">{document.href && <a className="audiobook-button-link" href={document.href}>{document.id === 'manuscript' ? 'Download source' : 'View report'}</a>}</div></> : <p>Select a document to inspect it.</p>; })()}</section>}
         {project && librarySection === 'exports' && <section className="audiobook-export-inspector"><div className="audiobook-panel-heading"><p className="eyebrow">Export summary</p><h2>Delivery</h2></div><p>Audiobook chapters <strong>{renderChapterCount}</strong></p><p>Skipped by reading policy <strong>{skippedRenderChapterCount}</strong></p><p>Completed renders <strong>{completedRenderChapters}</strong></p><p>In progress <strong>{project.render_jobs.filter((job) => ['running', 'leased', 'retrying'].includes(job.status)).length}</strong></p><p>Queued <strong>{project.render_jobs.filter((job) => ['queued', 'waiting'].includes(job.status)).length}</strong></p><p>Failed <strong className="review-pending">{project.render_jobs.filter((job) => ['failed', 'dead_letter'].includes(job.status)).length}</strong></p><hr /><h3>Export Formats</h3><p className="audiobook-detail-muted">Select one or more export formats.</p>{(['m4b', 'flac', 'wav', 'mp3'] as const).map((format) => <label className="audiobook-format-check" key={format}><input type="radio" name="export-format" checked={exportFormat === format} onChange={() => setExportFormat(format)} />{format.toUpperCase()} <small>{format === 'm4b' ? 'Audiobook Standard' : format === 'flac' ? 'Lossless Archive' : format === 'wav' ? 'Uncompressed' : 'Wide Compatibility'}</small></label>)}<button type="button" className="audiobook-primary-action" disabled={busy || !['ready_to_export', 'exported'].includes(project.state)} onClick={() => void action(() => audiobookApi.startExport(project.id, { format: exportFormat }), `${exportFormat.toUpperCase()} export queued.`)}>⇧ Generate Export Manifest</button><button type="button" disabled={busy || !['ready_to_export', 'exported'].includes(project.state)} onClick={() => void action(() => audiobookApi.startExport(project.id, { format: exportFormat }), `${exportFormat.toUpperCase()} export queued.`)}>⇧ Export Now</button><button type="button" onClick={() => setProjectSettingsOpen(true)}>⚙ Project Settings</button></section>}
         {project && <><section className="audiobook-project-status" aria-label="Project status"><div className="audiobook-panel-heading"><p className="eyebrow">Status</p><h2>Project status</h2></div>
          <p>Canonical source: {project.current_source_revision_id ? 'extracted' : 'awaiting import'}</p>
          <p>Review issues: {project.review_issues.length}</p>
          <p>Rendered coverage: {completedRenderChapters} / {renderChapterCount} audiobook chapters{skippedRenderChapterCount ? ` · ${skippedRenderChapterCount} source chapters skipped by policy` : ''}</p>
          <p>Words: {formatCount(project.word_count)}</p>
          <p>Estimated runtime: {formatDuration(project.estimated_runtime_seconds)}</p>
          <p>Actual runtime: {formatDuration(project.actual_runtime_seconds)}</p>
          <p>Production: {project.state.replaceAll('_', ' ')}</p>
        </section><section id="audiobook-cast"><div className="audiobook-panel-heading"><p className="eyebrow">Characters</p><h2>Voice cast</h2></div>
          <form className="audiobook-form audiobook-inline" onSubmit={submitSpeaker}><label>Speaker name<input value={speakerName} onChange={(event) => setSpeakerName(event.target.value)} /></label><button disabled={busy || !speakerName.trim()}>Add</button></form>
          {project.speakers.map((speaker) => <div className="audiobook-cast-row" key={speaker.id}><span>{speaker.canonical_name}<small>{speaker.kind} · {speaker.casting ? `revision ${speaker.casting.revision} · ${speaker.casting.voice_revision_hash?.slice(0, 10) || 'voice hash unavailable'}` : 'uncast'}</small></span>
            <select value={speaker.casting?.voice_profile_id ?? ''} disabled={busy} aria-label={`Voice for ${speaker.canonical_name}`}
              onChange={(event) => { const voice_profile_id = event.currentTarget.value; if (voice_profile_id) void action(() => audiobookApi.assignVoice(project.id, speaker.id, voice_profile_id), `Voice assigned to ${speaker.canonical_name}.`); }}>
              <option value="">Choose voice</option>{voicesQuery.data?.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}{voice.language ? ` · ${voice.language}` : ''}</option>)}
            </select>
            <div className="audiobook-cast-actions"><button type="button" aria-label={`Find span for ${speaker.canonical_name}`} disabled={busy} onClick={() => void action(async () => {
              const location = await findSpeakerSpan(speaker.id);
              setChapterId(location.chapterId); setSelectedSpanId(location.spanId); setWorkspaceMode('review');
            }, `Located ${speaker.canonical_name}.`)}>Find span</button>
              <button type="button" aria-label={`Audition voice for ${speaker.canonical_name}`} disabled={busy} onClick={() => void action(async () => {
                const modelRevision = await getInstalledModelRevision();
                const location = await findSpeakerSpan(speaker.id);
                await audiobookApi.preview(project.id, { chapter_id: location.chapterId, span_id: location.spanId, model_revision: modelRevision.trim() });
              }, `Audition queued for ${speaker.canonical_name}.`)}>Audition voice</button></div>
            {speaker.aliases?.length > 0 && <small>Aliases: {speaker.aliases.join(', ')}</small>}
            <span className="audiobook-alias-controls"><input aria-label={`Alias for ${speaker.canonical_name}`} placeholder="Known alias" value={aliasNames[speaker.id] ?? ''} onChange={(event) => setAliasNames((previous) => ({ ...previous, [speaker.id]: event.target.value }))} />
              <button type="button" disabled={busy || !aliasNames[speaker.id]?.trim()} onClick={() => void action(async () => {
                await audiobookApi.addAlias(project.id, speaker.id, aliasNames[speaker.id].trim());
                setAliasNames((previous) => ({ ...previous, [speaker.id]: '' }));
              }, 'Speaker alias confirmed.')}>Add alias</button></span>
          </div>)}
          {voicesQuery.data?.voices.length === 0 && <p className="audiobook-hint">Add a voice profile in Voice Cloning to cast speakers.</p>}
        </section>
        <section className="audiobook-review-summary"><div className="audiobook-panel-heading"><p className="eyebrow">Human review</p><h2>Review queue <span>{project.review_issues.length}</span></h2></div>
          {project.review_issues.length ? <>{Object.entries(project.review_issues.reduce<Record<string, number>>((counts, issue) => ({ ...counts, [issue.reason]: (counts[issue.reason] ?? 0) + 1 }), {})).sort(([, left], [, right]) => right - left).map(([reason, count]) => <p key={reason}>{reviewReasonLabel(reason)}<span>{count}</span></p>)}
            <button type="button" onClick={() => navigateLibrary('review')}>Open review queue</button></> : <p>No open review issues.</p>}
        </section>
        <section id="audiobook-pronunciations"><div className="audiobook-panel-heading"><p className="eyebrow">Speech plan</p><h2>Pronunciations</h2></div>
          <form className="audiobook-form" onSubmit={submitPronunciation}>
            <label>Source term<input value={sourceTerm} onChange={(event) => setSourceTerm(event.target.value)} maxLength={128} /></label>
            <label>Spoken form<input value={spokenTerm} onChange={(event) => setSpokenTerm(event.target.value)} maxLength={256} /></label>
            <button disabled={busy || !sourceTerm.trim() || !spokenTerm.trim()}>Save pronunciation</button>
          </form>
          {project.pronunciations?.map((entry) => <p className="audiobook-pronunciation" key={entry.source_term}><strong>{entry.source_term}</strong> → {entry.spoken_term}</p>)}
        </section></>}
      </aside>
    </>
  );
}
