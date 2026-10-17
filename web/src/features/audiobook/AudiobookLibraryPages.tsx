import { base, formatCount, formatDuration, formatStorage, libraryCardStatus } from './audiobookWorkspaceModel';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

/** The library page: every audiobook, searchable and filterable. */
export function AudiobookEbookLibraryPage({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    ebookLibraryFilter, ebookLibraryFormat, ebookLibraryOpen, ebookLibrarySearch, ebookLibrarySort, ebookLibraryView,
    filteredEbookProjects, libraryInProductionCount, libraryProjectQueries, libraryProjects, libraryReadyCount,
    libraryRuntimeSeconds, libraryStorageBytes, openProject, projectId, projectsQuery,
    selectedLibraryProject, setEbookLibraryFilter, setEbookLibraryFormat, setEbookLibraryOpen, setEbookLibrarySearch,
    setEbookLibrarySort, setEbookLibraryView, setLibrarySection, setProjectSettingsOpen, setSelectedLibraryProjectId,
    } = ws;
  return (
    <>
      {!projectId && ebookLibraryOpen && <section className="audiobook-card audiobook-ebook-library-page">
        <div className="audiobook-library-hero">
          <div><p className="eyebrow">Audiobook library</p><h1>Audiobook Library</h1><p>All your audiobook projects in one place.</p></div>
          <div className="audiobook-library-hero-art" aria-hidden="true"><div className="audiobook-library-books"><i /><i /><i /><i /><i /></div><span>“Great stories<br />sound even better here.”</span></div>
          <button type="button" className="audiobook-primary-action" onClick={() => { setEbookLibraryOpen(false); setLibrarySection('projects'); }}>＋ New audiobook</button>
        </div>
        <div role="group" className="audiobook-library-stats" aria-label="Library summary">
          <article><span className="library-stat-icon">▣</span><div><strong>{libraryProjects.length}</strong><small>Total Audiobooks</small></div></article>
          <article><span className="library-stat-icon">▶</span><div><strong>{libraryInProductionCount}</strong><small>In Production</small></div></article>
          <article><span className="library-stat-icon">✓</span><div><strong>{libraryReadyCount}</strong><small>Ready to Export</small></div></article>
          <article><span className="library-stat-icon">◷</span><div><strong>{formatDuration(libraryRuntimeSeconds)}</strong><small>Est. Runtime</small></div></article>
          <article><span className="library-stat-icon">▤</span><div><strong>{formatStorage(libraryStorageBytes)}</strong><small>Source Storage</small></div></article>
        </div>
        <div className="audiobook-library-toolbar">
          <label className="audiobook-search-field">Search audiobooks<input aria-label="Search audiobooks" value={ebookLibrarySearch} onChange={(event) => setEbookLibrarySearch(event.target.value)} placeholder="Search by title, author, language, or format..." /></label>
          <label><span>Format</span><select aria-label="Filter by format" value={ebookLibraryFormat} onChange={(event) => setEbookLibraryFormat(event.target.value)}><option value="all">All formats</option><option value="pdf">PDF</option><option value="epub">EPUB</option><option value="docx">DOCX</option><option value="html">HTML</option><option value="htm">HTM</option><option value="txt">TXT</option><option value="text">Text</option><option value="md">MD</option><option value="markdown">Markdown</option></select></label>
          <label><span>Status</span><select aria-label="Filter by status" value={ebookLibraryFilter} onChange={(event) => setEbookLibraryFilter(event.target.value)}><option value="all">All statuses</option><option value="in-progress">In production</option><option value="ready">Ready to export</option><option value="ready-to-render">Ready to render</option><option value="completed">Completed</option><option value="review">In review</option><option value="attention">Needs attention</option><option value="draft">Draft</option></select></label>
          <label><span>Sort</span><select aria-label="Sort library" value={ebookLibrarySort} onChange={(event) => setEbookLibrarySort(event.target.value as typeof ebookLibrarySort)}><option value="recent">Last edited</option><option value="title">Title</option><option value="author">Author</option></select></label>
          <button type="button" className={ebookLibraryView === 'grid' ? 'selected' : ''} aria-label="Grid view" onClick={() => setEbookLibraryView('grid')}>▦</button>
          <button type="button" className={ebookLibraryView === 'list' ? 'selected' : ''} aria-label="List view" onClick={() => setEbookLibraryView('list')}>☷</button>
        </div>
        <p className="audiobook-library-count">Showing {filteredEbookProjects.length} of {libraryProjects.length} audiobooks</p>
        {(projectsQuery.isLoading || libraryProjectQueries.some((query) => query.isLoading)) && <p role="status">Loading audiobook library...</p>}
        {projectsQuery.isError && <p role="alert">Could not load the audiobook library.</p>}
        {ebookLibraryView === 'grid' ? <div className="audiobook-ebook-grid">
          {filteredEbookProjects.map((item) => {
            const status = libraryCardStatus(item);
            return <article className={`audiobook-ebook-card${selectedLibraryProject?.id === item.id ? ' selected' : ''}`} key={item.id}>
              <div className="audiobook-ebook-cover">{item.cover_asset_id ? <img src={`${base}/projects/${encodeURIComponent(item.id)}/cover`} alt={`Cover of ${item.title}`} /> : <span>✦</span>}</div>
              <div className="audiobook-ebook-card-copy">
                <h2 title={item.title}><button type="button" className="audiobook-library-select-title" aria-label={`Select ${item.title}`} aria-pressed={selectedLibraryProject?.id === item.id} onClick={() => setSelectedLibraryProjectId(item.id)}>{item.title}</button></h2>
                <p>{item.author || 'Unknown author'} · {item.language === 'en' ? 'English' : item.language}</p>
                <p className="audiobook-ebook-description">{item.source_filename || 'No source uploaded yet'}</p>
                <div className="audiobook-library-card-tags">{item.source_format && <span>{item.source_format.toUpperCase()} source</span>}<span className={`audiobook-status-pill library-status-${status.toLowerCase().replaceAll(' ', '-')}`}>{status === 'In Production' ? '◖ ' : status === 'Completed' ? '✓ ' : '● '}{status}</span></div>
              </div>
                <div className="audiobook-ebook-card-stats"><span><strong>{formatCount(item.word_count)}</strong><small>words</small></span><span><strong>{formatCount(item.chapters?.length)}</strong><small>chapters</small></span><span><strong>{formatDuration(item.estimated_runtime_seconds)}</strong><small>runtime</small></span></div>
                <div className="audiobook-ebook-card-actions"><button type="button" className="audiobook-primary-action" onClick={() => openProject(item)}>{status === 'In Production' ? 'Continue' : 'Open'}</button>{item.current_source_revision_id && <a className="audiobook-button-link" href={`${base}/projects/${encodeURIComponent(item.id)}/source/download`} download aria-label={`Download ${item.title} source`}>{item.source_format === 'pdf' ? 'Download PDF' : 'Download source'}</a>}<button type="button" className="audiobook-library-more" aria-label={`More actions for ${item.title}`} onClick={() => { openProject(item); setProjectSettingsOpen(true); }}>•••</button></div>
            </article>;
          })}
        </div> : <div className="audiobook-ebook-list" role="table" aria-label="All audiobooks">
          {filteredEbookProjects.map((item) => <div className="audiobook-ebook-list-row" role="row" key={item.id}><div className="audiobook-ebook-list-cover">{item.cover_asset_id ? <img src={`${base}/projects/${encodeURIComponent(item.id)}/cover`} alt="" /> : <span>✦</span>}</div><div><button type="button" className="audiobook-library-list-select" aria-label={`Select ${item.title}`} aria-pressed={selectedLibraryProject?.id === item.id} onClick={() => setSelectedLibraryProjectId(item.id)}>{item.title}</button><small>{item.author || 'Unknown author'} · {libraryCardStatus(item)}</small></div><span>{formatCount(item.chapters?.length)} chapters</span><span>{formatCount(item.word_count)} words</span><button type="button" className="audiobook-primary-action" onClick={() => openProject(item)}>Open</button>{item.current_source_revision_id && <a className="audiobook-button-link" href={`${base}/projects/${encodeURIComponent(item.id)}/source/download`} download aria-label={`Download ${item.title} source`}>{item.source_format === 'pdf' ? 'Download PDF' : 'Download source'}</a>}</div>)}
        </div>}
        {!projectsQuery.isLoading && !filteredEbookProjects.length && <div className="audiobook-library-empty"><strong>No audiobooks match this view.</strong><p>Try another search or create your first audiobook project.</p><button type="button" onClick={() => { setEbookLibraryOpen(false); setLibrarySection('projects'); }}>Create audiobook</button></div>}
      </section>}
    </>
  );
}

/** Creating an audiobook: the source, then the project details. */
export function AudiobookCreatePage({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    author, busy, createClassificationRules, createExcludePageRanges, ebookLibraryOpen, language, pendingSource,
    projectId, setAuthor, setCreateClassificationRules, setCreateExcludePageRanges, setLanguage,
    setPendingSource, setSourceIntent, setSourceText, setTitle, sourceIntent, sourceText, submitProject, title,
  } = ws;
  return (
    <>
      {!projectId && !ebookLibraryOpen && <section className="audiobook-card audiobook-empty audiobook-create-page">
        <div className="audiobook-create-hero">
          <div><p className="eyebrow">Audiobook project</p><h1>Create a New Audiobook Project</h1><p>Turn your story, document, or ideas into a professional audiobook with AI.</p>
            <div className="audiobook-value-points"><span>◉ Natural voices</span><span>▣ Local &amp; private</span><span>✦ Full creative control</span><span>⇧ Export anywhere</span></div>
          </div>
          <div className="audiobook-hero-art" aria-hidden="true"><span>Stories<br />sound better<br />here.</span></div>
        </div>
        <h2>1. Choose your content source</h2><p className="audiobook-subtitle">Start with a file, paste your text, or begin with a blank project.</p>
        <div className="audiobook-source-options">
          <label className={`audiobook-source-option${pendingSource && /\.(epub|pdf)$/i.test(pendingSource.name) ? ' selected' : ''}`}>
            <input type="file" accept=".epub,.pdf" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) { setPendingSource(file); setSourceIntent('file'); } }} />
            <strong>▥<br />Import EPUB or PDF</strong><small>Import a book file with chapters automatically detected.</small>
          </label>
          <label className={`audiobook-source-option${pendingSource && /\.(md|markdown)$/i.test(pendingSource.name) ? ' selected' : ''}`}>
            <input type="file" accept=".md,.markdown" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) { setPendingSource(file); setSourceIntent('file'); } }} />
            <strong>▤<br />Import Markdown</strong><small>Import a .md file with clean formatting and chapter support.</small>
          </label>
          <button type="button" className={`audiobook-source-option${sourceIntent === 'paste' ? ' selected' : ''}`} onClick={() => setSourceIntent('paste')}>
            <strong>▣<br />Paste Text</strong><small>Paste your story or text content directly into the editor.</small>
          </button>
          <label className={`audiobook-source-option${pendingSource && /\.(txt|text)$/i.test(pendingSource.name) ? ' selected' : ''}`}>
            <input type="file" accept=".txt,.text" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) { setPendingSource(file); setSourceIntent('file'); } }} />
            <strong>⇧<br />Upload TXT</strong><small>Upload a plain text file to get started quickly.</small>
          </label>
          <button type="button" className={`audiobook-source-option${sourceIntent === 'blank' ? ' selected' : ''}`} onClick={() => { setSourceIntent('blank'); setPendingSource(null); setSourceText(''); }}>
            <strong>＋<br />Start Blank Project</strong><small>Create a project now and upload a source later.</small>
          </button>
        </div>
        {sourceIntent === 'paste' && <label className="audiobook-paste-source">Paste source text<textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder="Paste a short story or manuscript here…" rows={5} /></label>}
        {(pendingSource || sourceText.trim()) && <p className="audiobook-selected-source" role="status">Source ready: {pendingSource?.name ?? 'pasted-story.txt'}. It will be queued immediately after the project is created.</p>}
        <form className="audiobook-create-details" onSubmit={submitProject}>
          <div><h2>2. Project details</h2><p className="audiobook-subtitle">Set up your audiobook project. You can change these later.</p></div>
          <label>Project title *<input id="audiobook-create-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. The Lantern at Hollow Bay" required /></label>
          <label>Author<input value={author} onChange={(event) => setAuthor(event.target.value)} placeholder="e.g. Mira Vale" /></label>
          <label>Language<input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="en" required /></label>
          <details className="audiobook-create-advanced"><summary>Advanced settings</summary><label>Exclude PDF pages<input inputMode="text" placeholder="e.g. 1-3, 42-45" value={createExcludePageRanges} onChange={(event) => setCreateExcludePageRanges(event.target.value)} /><small>Applied when the selected PDF is queued.</small></label>
            <label className="audiobook-classification-rules">Quote extraction and classification rules (optional)
              <textarea value={createClassificationRules} onChange={(event) => setCreateClassificationRules(event.target.value)} maxLength={4000} rows={3}
                placeholder="Character quotes can also be in speaker: quote format." />
              <small>Saved with the book and used to extract quotes. Review the quote spans before starting speaker classification.</small>
            </label>
          </details>
          <div className="audiobook-create-details-actions"><span>Local-first processing · source files stay in Omnix storage.</span><button type="submit" disabled={busy || !title.trim()}>✦ Create project&nbsp; →</button></div>
        </form>
      </section>}
    </>
  );
}
