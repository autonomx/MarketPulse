import { useMemo } from 'react';
import { buildStoryDocumentFromText } from './storyDocument';
import { validateStoryDocumentForRemote } from './storyRemoteStore';
import { useStorySnapshot } from './storySnapshotStore';

export function StoryRemotePanel() {
  const snapshot = useStorySnapshot();
  const storyDoc = useMemo(() => buildStoryDocumentFromText({ title: snapshot.title, text: snapshot.text }), [snapshot.title, snapshot.text]);
  const issues = useMemo(() => validateStoryDocumentForRemote(storyDoc), [storyDoc]);

  return (
    <section className="storyteller-cast-panel" aria-label="Story remote status">
      <div className="storyteller-cast-heading">
        <div><p className="eyebrow">Remote status</p><h3>Metadata checks</h3></div>
        <strong>{issues.length ? `${issues.length} issues` : 'valid'}</strong>
      </div>
      <p>Story metadata is stored with the story asset; these checks run locally.</p>
      <div className="storyteller-cast-list">
        <article className="storyteller-cast-card"><div><strong>{storyDoc.chapters.length}</strong><span>chapters</span></div><small>{storyDoc.id}</small></article>
        <article className="storyteller-cast-card"><div><strong>{storyDoc.cast.length}</strong><span>cast members</span></div><small>{issues[0] ?? 'Ready for validation'}</small></article>
      </div>
    </section>
  );
}
