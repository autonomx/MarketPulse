import { StoryChapterMediaPanel } from './StoryChapterMediaPanel';
import { StoryReadPanel } from './StoryReadPanel';
import { StoryRemotePanel } from './StoryRemotePanel';

export function StoryExtraPanels() {
  return (
    <details className="storyteller-toolbox storyteller-toolbox-secondary">
      <summary>
        <span><strong>Read & delivery tools</strong><small>Pacing, chapter media queue, remote metadata checks</small></span>
        <em>Open tools</em>
      </summary>
      <div className="storyteller-toolbox-content storyteller-toolbox-grid">
        <StoryReadPanel />
        <StoryChapterMediaPanel />
        <StoryRemotePanel />
      </div>
    </details>
  );
}
