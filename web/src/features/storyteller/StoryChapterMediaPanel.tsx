import { useMemo } from 'react';
import { buildChapterAudioStates, documentFromCurrentStory } from './storyAudioManifest';
import { useStorySnapshot } from './storySnapshotStore';

export function StoryChapterMediaPanel() {
  const snapshot = useStorySnapshot();
  const storyDoc = useMemo(() => documentFromCurrentStory(snapshot.title, snapshot.text), [snapshot.title, snapshot.text]);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the revision marks newly saved audio manifests
  const chapterStates = useMemo(() => buildChapterAudioStates(storyDoc), [storyDoc, snapshot.revision]);
  const readyCount = chapterStates.filter((chapter) => chapter.status === 'ready').length;

  return (
    <section className="storyteller-cast-panel" aria-label="Story chapter media">
      <div className="storyteller-cast-heading">
        <div><p className="eyebrow">Chapter media</p><h3>Player queue</h3></div>
        <strong>{readyCount}/{chapterStates.length} ready</strong>
      </div>
      <p>Generated chapter outputs are listed here so the story can be played in sequence and packaged after chapter media is ready.</p>
      <div className="storyteller-voice-cast-table">
        {chapterStates.map((chapter, index) => (
          <article className="storyteller-voice-row" key={chapter.chapterId}>
            <div><strong>{chapter.chapterTitle || `Chapter ${index + 1}`}</strong><span>{chapter.chapterId}</span></div>
            <div><strong>{chapter.status}</strong><span>{chapter.manifest?.downloadFilename ?? 'No file yet'}</span></div>
            <button type="button" disabled={chapter.status !== 'ready'}>Play</button>
          </article>
        ))}
      </div>
    </section>
  );
}
