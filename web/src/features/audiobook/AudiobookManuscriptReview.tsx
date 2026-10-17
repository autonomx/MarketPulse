import { audiobookApi } from './audiobookApi';
import { audiobook, base, reclassifyAudiobook } from './audiobookWorkspaceModel';
import type { ChapterSummary, Span } from './audiobookTypes';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';
import { api } from './api/gateway';

/** Pronunciation changes worth showing; page-layout whitespace folded into a line break is not one. */
function wordChanges(transformations: Span['speech_plan']['transformations']) {
  return transformations.filter((item) => item.rule !== 'layout_whitespace');
}

/** Leaves the whole chapter out of the audiobook, or brings it back. */
function ChapterAudioToggle({ ws, chapter }: { ws: AudiobookWorkspaceModel; chapter: ChapterSummary }) {
  const { action, busy, project } = ws;
  if (!project) return null;
  const removalId = chapter.audio_removal_id;
  return removalId
    ? <button type="button" disabled={busy} onClick={() => void action(() => audiobookApi.restoreSpeech(project.id, removalId), `“${chapter.title}” is back in the audiobook. Regenerate audio to hear it.`)}>Restore chapter</button>
    : <button type="button" disabled={busy} onClick={() => void action(() => audiobookApi.removeChapterFromAudio(project.id, chapter.id), `“${chapter.title}” removed from the audiobook. Its text stays in the source.`)}>Remove chapter from audio</button>;
}

/** The manuscript review: source text and characterization spans. */
export function AudiobookManuscriptReview({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    action, activeChapterId, busy, capturePronunciation, changeSpanEdit, chapterQuery, classificationRules,
    expandedSpanId, extractionRunning, getInstalledModelRevision, librarySection, project,
    reclassificationRunning, rulesNeedExtraction, selectedChapter, selectedEdit, selectedPronunciation,
    selectedRemoval, selectedSpan, setClassificationRules, setExpandedSpanId, setSelectedPronunciation,
    setSelectedRemoval, setSelectedSpanId, setSpanEdits, setSpeakerFilter, setSpokenTerm, sourceTerm, speakerFilter,
    spokenTerm, submitPronunciation, visibleSpans, workspaceMode,
  } = ws;
  if (!project) return null;
  const activeChapter = project.chapters.find((chapter) => chapter.id === activeChapterId);
  return (
    <>
      {workspaceMode === 'review' && librarySection === 'projects' &&
      <div className="audiobook-manuscript" id="audiobook-manuscript">
        {selectedChapter && <section className="audiobook-card audiobook-classify-section" aria-labelledby="audiobook-classify-title">
          <div className="audiobook-section-title"><div><p className="eyebrow">Phase 3</p><h2 id="audiobook-classify-title">Text classification</h2><p className="audiobook-subtitle">Review the quote spans below first, then classify text to assign each span to a speaker.</p></div>
            <button type="button" disabled={busy || extractionRunning || reclassificationRunning || rulesNeedExtraction || !project.current_source_revision_id}
              onClick={() => void action(() => reclassifyAudiobook(project.id, classificationRules[project.id] ?? project.classification_rules ?? ''), 'Text classification queued.')}>
              {reclassificationRunning ? 'Classifying…' : 'Classify text'}
            </button>
          </div>
          <label className="audiobook-classification-rules">Custom classification rules (optional)
            <textarea value={classificationRules[project.id] ?? project.classification_rules ?? ''} maxLength={4000} rows={3}
              disabled={busy || reclassificationRunning}
              onChange={(event) => setClassificationRules((current) => ({ ...current, [project.id]: event.target.value }))}
              placeholder="Character quotes can also be in speaker: quote format, e.g. Ehsan: It’s working again."
              aria-describedby="classification-rules-help" />
            <small id="classification-rules-help">Review the extracted quote spans before clicking Classify text. Classification assigns speakers to these spans and does not re-extract quotes.</small>
            {rulesNeedExtraction && <small>Rules changed. Open Book source, extract quotes again, and review the spans first.</small>}
          </label>
        </section>}
        <section className="audiobook-card audiobook-source-panel" aria-labelledby="audiobook-source-text-title">
        <div className="audiobook-section-title"><div><p className="eyebrow">Canonical source</p><h2 id="audiobook-source-text-title">Source text</h2><p className="audiobook-subtitle">{selectedChapter?.title ?? activeChapter?.title ?? 'Awaiting extraction'} · Select a passage to open its span below.</p></div>
          <div className="audiobook-source-chapter-actions"><span>{project.chapters.length} chapters</span>
            {activeChapter && <ChapterAudioToggle ws={ws} chapter={activeChapter} />}
          </div></div>
        {activeChapter?.audio_removal_id && <p className="audiobook-chapter-removed-notice" role="status">This chapter is left out of the audiobook: none of it is read, and its review issues no longer block rendering. Restore the chapter to read it again.</p>}
        {selectedChapter ?
          <div role="group" className="audiobook-text" aria-label="Canonical chapter text">
            {selectedChapter.spans.map((span) => <span key={span.id} role="button" tabIndex={0}
              className={`audiobook-source-span${span.id === selectedSpan?.id ? ' selected' : ''}${span.annotation?.review_status === 'review_required' ? ' needs-review' : ''}`}
              aria-label={`Inspect ${span.annotation?.role ?? span.structural_kind} span: ${span.source_text.slice(0, 64)}`}
              title="Select text to remove it from audio, or select a word to set its pronunciation"
              onMouseUp={(event) => capturePronunciation(span.id, event.currentTarget)}
              onKeyUp={(event) => capturePronunciation(span.id, event.currentTarget)}
              onClick={() => { setSelectedSpanId(span.id); setExpandedSpanId(span.id); }}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedSpanId(span.id); setExpandedSpanId(span.id); } }}>
              {span.source_text}
            </span>)}
          </div>
          : project.chapters.length ? <p>{chapterQuery.isError ? 'Could not load this chapter.' : 'Loading chapter…'}</p> : <p>Upload a source file to extract chapters. The original text remains available throughout production.</p>}
        </section>
        {selectedChapter && <section className="audiobook-card audiobook-spans-section" aria-label="Characterization spans">
            <div className="audiobook-section-title"><div><p className="eyebrow">Speech review</p><h2>Span review</h2><p className="audiobook-subtitle">Review extracted quote spans before classifying text. Select text in a passage to remove it from audio or set a word’s pronunciation. Expand a span to edit its character and delivery.</p></div>
            </div>
            <label className="audiobook-span-filter">Filter by character
              <select aria-label="Filter spans by character" value={speakerFilter} onChange={(event) => setSpeakerFilter(event.target.value)}>
                <option value="all">All characters</option><option value="unassigned">Unassigned</option>
                {project.speakers.map((speaker) => <option key={speaker.id} value={speaker.id}>{speaker.canonical_name}</option>)}
              </select>
            </label>
            <div role="group" className="audiobook-preview-list" aria-label="Span previews">
              {visibleSpans.map((span) => {
                const preview = project.preview_jobs?.find((job) => job.span_id === span.id);
                const speaker = project.speakers.find((item) => item.id === span.annotation?.speaker_id);
                const edit = selectedSpan?.id === span.id ? selectedEdit : null;
                const unsavedInterpretation = edit && (
                  edit.speaker_id !== (span.annotation?.speaker_id ?? '')
                  || edit.role !== (span.annotation?.role ?? span.structural_kind)
                  || edit.delivery !== (span.annotation?.delivery ?? '')
                );
                const previewBlockedReason = !span.speech_plan.tts_input_text.trim()
                  ? 'Skipped by the current reading policy or removed from audio.'
                  : unsavedInterpretation
                    ? 'Save interpretation to use your selected speaker and delivery in the preview.'
                  : !span.annotation
                    ? 'Choose a speaker and save interpretation, or classify text, before previewing audio.'
                    : !speaker
                      ? 'Assign a speaker to this span before previewing audio.'
                      : !speaker.casting
                        ? `Assign a voice to ${speaker.canonical_name} in Cast & Voice Tools to preview audio.`
                        : null;
                const expanded = expandedSpanId === span.id;
                const issue = project.review_issues.find((item) => item.span_id === span.id);
                return <article className="audiobook-preview-row" key={span.id}>
                  <div className="audiobook-span-summary"><div><small>{span.annotation?.role ?? span.structural_kind} · {speaker?.canonical_name ?? span.annotation?.speaker_candidate ?? 'Unassigned'}{span.annotation?.review_status === 'review_required' ? ' · Needs review' : ''}</small>
                    <p>{span.source_text}</p></div>
                    <div className="audiobook-span-actions"><button type="button" aria-expanded={expanded} aria-label={`${expanded ? 'Collapse' : 'Expand'} span ${span.source_text.slice(0, 48)}`}
                      onClick={() => { setExpandedSpanId(expanded ? null : span.id); setSelectedSpanId(span.id); setSelectedPronunciation(null); }}>{expanded ? 'Collapse' : 'Expand'}</button>
                      <button type="button" disabled={busy || Boolean(previewBlockedReason)}
                        aria-describedby={previewBlockedReason ? `preview-help-${span.id}` : undefined}
                        title={previewBlockedReason ?? 'Preview this spoken span'}
                        onClick={() => void action(async () => {
                          const modelRevision = await getInstalledModelRevision();
                          return audiobookApi.preview(project.id, { chapter_id: selectedChapter.id, span_id: span.id, model_revision: modelRevision.trim() });
                        }, 'Span preview queued.')}>Preview</button></div></div>
                  {previewBlockedReason && <small id={`preview-help-${span.id}`}>{previewBlockedReason}</small>}
                  {preview && <small role="status">{preview.status}{preview.error?.message ? ` · ${preview.error.message}` : ''}</small>}
                  {preview?.status === 'completed' && <audio controls preload="none" src={`${base}/projects/${encodeURIComponent(project.id)}/previews/${encodeURIComponent(preview.id)}/audio`} aria-label={`Preview ${span.source_text.slice(0, 48)}`} />}
                  {expanded && <div className="audiobook-span-details">
                    <p className="audiobook-span-selectable" tabIndex={0} onMouseUp={(event) => capturePronunciation(span.id, event.currentTarget)} onKeyUp={(event) => capturePronunciation(span.id, event.currentTarget)}>{span.source_text}</p>
                    <small>Select text to remove it from this passage’s audio, or select a word to set its pronunciation throughout this book.</small>
                    {selectedRemoval?.spanId === span.id && <div className="audiobook-span-removal">
                      <span>Selected: “{selectedRemoval.source_text}”</span>
                      <button type="button" disabled={busy} onClick={() => void action(async () => {
                        await audiobookApi.excludeSpeech(project.id, span.id, {
                          start_offset: selectedRemoval.start_offset, end_offset: selectedRemoval.end_offset, source_text: selectedRemoval.source_text,
                        });
                        setSelectedRemoval(null);
                        setSelectedPronunciation(null);
                      }, 'Text removed from this passage’s audio. Regenerate audio to hear the change.')}>Remove from audio</button>
                    </div>}
                    {span.speech_exclusions?.filter((item) => item.id !== activeChapter?.audio_removal_id).map((item) => <div className="audiobook-span-removal" key={item.id}>
                      <span>Removed from audio: “{item.source_text}”</span>
                      <button type="button" disabled={busy} onClick={() => void action(async () => {
                        await audiobook(api.DELETE('/api/audiobook/projects/{project_id}/speech-exclusions/{exclusion_id}', {
                          params: { path: { project_id: project.id, exclusion_id: item.id } },
                        }));
                      }, 'Text restored. Regenerate audio to hear the change.')}>Restore text</button>
                    </div>)}
                    {selectedPronunciation?.spanId === span.id && <form className="audiobook-span-pronunciation" onSubmit={submitPronunciation}>
                      <label>Written word<input aria-label="Selected written word" value={sourceTerm} readOnly /></label>
                      <label>Spoken as<input aria-label="Spoken as" value={spokenTerm} maxLength={256} onChange={(event) => setSpokenTerm(event.target.value)} /></label>
                      <button disabled={busy || !spokenTerm.trim()}>Save for whole book</button>
                    </form>}
                    <p className="audiobook-span-spoken"><strong>Spoken text</strong> {span.speech_plan.tts_input_text}</p>
                    {wordChanges(span.speech_plan.transformations).length > 0 && <small>Speech changes: {wordChanges(span.speech_plan.transformations).map((item) => `${item.source} → ${item.spoken}`).join(', ')}</small>}
                    {issue && <p className="audiobook-review-reason"><strong>Review: {issue.reason}</strong> {JSON.stringify(issue.evidence ?? {})}</p>}
                    {edit && <div role="group" className="audiobook-annotation-controls" aria-label="Span interpretation controls">
                      <label>Speaker<select aria-label="Selected span speaker" value={edit.speaker_id} onChange={(event) => changeSpanEdit({ speaker_id: event.target.value })}>
                        <option value="">Choose speaker</option>{project.speakers.map((item) => <option key={item.id} value={item.id}>{item.canonical_name}</option>)}
                      </select></label>
                      <label>Role<select aria-label="Selected span role" value={edit.role} onChange={(event) => changeSpanEdit({ role: event.target.value })}>
                        {['narration', 'dialogue', 'heading', 'other'].map((role) => <option key={role} value={role}>{role}</option>)}
                      </select></label>
                      <label>Delivery or emotion<input aria-label="Selected span delivery" value={edit.delivery} maxLength={512} placeholder="e.g. softly, excited, hesitant" onChange={(event) => changeSpanEdit({ delivery: event.target.value })} /></label>
                      <button type="button" disabled={busy || !edit.speaker_id} onClick={() => void action(async () => {
                        await (issue
                          ? audiobookApi.resolveReview(project.id, issue.id, edit)
                          : audiobookApi.reviseSpan(project.id, span.id, edit));
                        setSpanEdits((previous) => { const next = { ...previous }; delete next[span.id]; return next; });
                      }, issue ? 'Review decision saved.' : 'Span interpretation saved. Render again to update affected audio.')}>Save interpretation</button>
                    </div>}
                  </div>}
                </article>;
              })}
              {visibleSpans.length === 0 && <p>No spans match this character.</p>}
            </div>
          </section>}
      </div>}
    </>
  );
}
