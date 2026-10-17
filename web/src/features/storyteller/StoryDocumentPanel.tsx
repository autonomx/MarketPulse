import { useEffect, useMemo, useState } from 'react';
import { validateDialogueAttribution } from './storyAttribution';
import { allStoryBlocks, buildStoryDocumentFromText, loadStoryDocument, saveStoryDocument, storyDocumentFingerprint, type StoryDocument } from './storyDocument';
import { useStorySnapshot } from './storySnapshotStore';

export function StoryDocumentPanel() {
  const snapshot = useStorySnapshot();
  const [document, setDocument] = useState<StoryDocument>(() => (
    buildStoryDocumentFromText({ title: snapshot.title, text: snapshot.text, existing: loadStoryDocument(`${snapshot.title}:${snapshot.fingerprint}`) })
  ));
  const blocks = useMemo(() => allStoryBlocks(document), [document]);
  const attribution = useMemo(() => validateDialogueAttribution(document), [document]);
  const dialogueCount = useMemo(() => blocks.filter((block) => block.kind === 'dialogue').length, [blocks]);

  useEffect(() => {
    setDocument((current) => {
      const next = buildStoryDocumentFromText({ title: snapshot.title, text: snapshot.text, existing: loadStoryDocument(current.id) });
      saveStoryDocument(next);
      return next;
    });
  }, [snapshot.title, snapshot.text]);

  return (
    <section className="storyteller-cast-panel" aria-label="Structured story document">
      <div className="storyteller-cast-heading">
        <div><p className="eyebrow">Structured document</p><h3>Generation contract preview</h3></div>
        <strong>{blocks.length} blocks</strong>
      </div>
      <p>Storyteller keeps a structured document with chapters, narration blocks, dialogue blocks, cast, and audio metadata. Dialogue speaker ids are validated against the cast before they are used for audio.</p>
      <div className="storyteller-cast-list">
        <article className="storyteller-cast-card"><div><strong>{document.chapters.length}</strong><span>chapters</span></div><small>{document.title}</small></article>
        <article className="storyteller-cast-card"><div><strong>{dialogueCount}</strong><span>dialogue blocks</span></div><small>{attribution.highConfidence} high-confidence attributions</small></article>
        <article className="storyteller-cast-card"><div><strong>{attribution.narratorFallbacks}</strong><span>narrator fallbacks</span></div><small>{attribution.warnings.length} attribution notes</small></article>
        <article className="storyteller-cast-card"><div><strong>{document.cast.length}</strong><span>cast records</span></div><small>{storyDocumentFingerprint(document)}</small></article>
      </div>
    </section>
  );
}
