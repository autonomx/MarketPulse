import { StoryAudioPanel } from './StoryAudioPanel';
import { StoryCastPanel } from './StoryCastPanel';
import { StoryChapterAudioPanel } from './StoryChapterAudioPanel';
import { StoryDocumentPanel } from './StoryDocumentPanel';
import { StoryVoiceCastPanel } from './StoryVoiceCastPanel';

/** Narration, voices, cast, chapter audio and document tools under the project header. */
export function StorytellerToolbox() {
  return (
    <details className="storyteller-toolbox storyteller-toolbox-primary">
      <summary>
        <span>
          <strong>Audio &amp; cast tools</strong>
          <small>Narration, voices, character registry, chapter audio, structured JSON</small>
        </span>
        <em>Open tools</em>
      </summary>
      <div className="storyteller-toolbox-content storyteller-toolbox-grid">
        <StoryAudioPanel />
        <StoryVoiceCastPanel />
        <StoryCastPanel />
        <StoryChapterAudioPanel />
        <StoryDocumentPanel />
      </div>
    </details>
  );
}
