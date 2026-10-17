/** Presentational parts of the story stage: mode switch, story mode, project header, actions, versions, outline and text. */
import { Button } from '@mantine/core';
import { useMemo, useState } from 'react';
import { type JobRecord } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixStatusPill } from '../../design/primitives';
import { SaveFeedback, StoryAssetSummary, StoryOutlineChapter, StoryQuickActionMode, StoryWorkspaceMode, quickActions, storyVersionTitle } from './storyModel';
import { lastStoryPage, storyParagraphs, storyTextBlocks } from './storyTextModel';

export function StoryModeSwitch({ mode, onChange }: { mode: StoryWorkspaceMode; onChange: (mode: StoryWorkspaceMode) => void }) {
  return (
    <section className="storyteller-mode-switch" aria-label="Storyteller mode">
      <button className={mode === 'writing' ? 'active' : ''} type="button" onClick={() => onChange('writing')}>
        <strong>Writing Mode</strong>
        <span>Draft, revise, save, and export manuscripts.</span>
      </button>
      <button className={mode === 'story' ? 'active' : ''} type="button" onClick={() => onChange('story')}>
        <strong>Interactive Story Mode</strong>
        <span>Read a page, make a move, and let AI continue.</span>
      </button>
    </section>
  );
}

export function StoryModePanel({
  activeAsset,
  activeChapterLabel,
  activeStoryText,
  assetError,
  isAssetLoading,
  module,
  onMove,
  pending,
  response,
  setResponse,
  storyTitle,
  suggestedMoves,
}: {
  activeAsset: StoryAssetSummary | null;
  activeChapterLabel: string;
  activeStoryText: string | null;
  assetError: Error | null;
  isAssetLoading: boolean;
  module: OmnixModuleDefinition;
  onMove: (moveText: string, suggestedChoice?: string | null) => void;
  pending: boolean;
  response: string;
  setResponse: (value: string) => void;
  storyTitle: string;
  suggestedMoves: string[];
}) {
  const latestPage = activeStoryText ? lastStoryPage(activeStoryText) : null;
  return (
    <section className="story-mode-panel" aria-label="Interactive story mode">
      <div className="story-mode-reader">
        <div className="storyteller-manuscript-meta">
          <span>{activeChapterLabel}</span>
          <span>Interactive page</span>
        </div>
        <h2>{storyTitle}</h2>
        <div className="storyteller-flourish" aria-hidden="true"><span /><strong>◇</strong><span /></div>
        {latestPage ? (
          <div className="story-mode-page">
            {storyParagraphs(latestPage).map((paragraph, index) => (
              <p key={`${paragraph.slice(0, 24)}-${index}`}>{paragraph}</p>
            ))}
          </div>
        ) : (
          <StoryEmptyState
            activeAsset={activeAsset}
            assetError={assetError}
            isAssetLoading={isAssetLoading}
            module={module}
            storyTitle={storyTitle}
          />
        )}
      </div>
      <aside className="story-mode-controls" aria-label="Interactive story mode controls">
        <div className="storyteller-panel-heading">
          <div><p className="eyebrow">Interactive story mode</p><h3>Your next move</h3></div>
          <OmnixStatusPill>{pending ? 'continuing' : 'ready'}</OmnixStatusPill>
        </div>
        <label>
          Write your response
          <textarea
            aria-label="Interactive story mode response"
            onChange={(event) => setResponse(event.target.value)}
            placeholder="I examine the glowing door, but keep one hand on the charm."
            rows={5}
            value={response}
          />
        </label>
        <Button disabled={pending || !response.trim()} loading={pending} onClick={() => onMove(response)} type="button">
          Continue with my response
        </Button>
        <div className="story-mode-suggestions">
          <p className="eyebrow">Suggested next moves</p>
          {suggestedMoves.map((move) => (
            <button disabled={pending} key={move} type="button" onClick={() => onMove(move, move)}>{move}</button>
          ))}
        </div>
      </aside>
    </section>
  );
}

export function StoryEmptyState({ activeAsset, assetError, isAssetLoading, module, storyTitle }: {
  activeAsset: StoryAssetSummary | null;
  assetError: Error | null;
  isAssetLoading: boolean;
  module: OmnixModuleDefinition;
  storyTitle: string;
}) {
  if (activeAsset) {
    const message = assetError
      ? 'This story asset could not be loaded as readable text.'
      : isAssetLoading
        ? 'Loading story asset content…'
        : 'This story asset is selected but has no readable manuscript text.';
    return (
      <div className="storyteller-empty-manuscript" role="status">
        <p className="eyebrow">Story asset</p>
        <h3>{storyTitle}</h3>
        <p>{message}</p>
      </div>
    );
  }
  return (
    <div className="storyteller-empty-manuscript" role="status">
      <p className="eyebrow">Feature module</p>
      <h3>{module.label}</h3>
      <p>{module.summary}</p>
      <p>Start with a premise, choose a tone, then generate the first scene.</p>
    </div>
  );
}

export function StoryProjectHeader({ title, premise, providerLabel, wordCount, chapterCount, moduleRoute, canPersistStory, saveFeedback, onSave, onExport }: {
  title: string;
  premise: string;
  providerLabel: string;
  wordCount: number;
  chapterCount: number;
  moduleRoute: string;
  canPersistStory: boolean;
  saveFeedback: SaveFeedback | null;
  onSave: () => void;
  onExport: () => void;
}) {
  return (
    <header className="storyteller-project-header">
      <div className="storyteller-cover" aria-hidden="true" />
      <div className="storyteller-project-copy">
        <p className="eyebrow">{moduleRoute}</p>
        <h1>{title}</h1>
        <p>{premise || 'A new local-first story draft.'}</p>
        <div className="storyteller-tags"><span>Fantasy</span><span>Cozy</span><span>Mystery</span><span>Slice of Life</span></div>
        {saveFeedback ? <p className={`storyteller-persist-feedback ${saveFeedback.kind}`} role="status">{saveFeedback.message}</p> : null}
      </div>
      <div className="storyteller-project-stats">
        <div><strong>{wordCount.toLocaleString()}</strong><span>Words</span></div>
        <div><strong>{chapterCount}</strong><span>Chapters</span></div>
        <div><strong>{providerLabel}</strong><span>Default provider</span></div>
      </div>
      <div className="storyteller-project-actions">
        <button disabled={!canPersistStory} type="button" onClick={onSave}>Save story</button>
        <button disabled={!canPersistStory} type="button" onClick={onExport}>Export Markdown</button>
      </div>
    </header>
  );
}

export function StoryActionBar({ disabled, onAction }: { disabled: boolean; onAction: (action: StoryQuickActionMode) => void }) {
  return (
    <section className="storyteller-action-bar" aria-label="Story actions">
      {quickActions.map((action) => (
        <button disabled={disabled} key={action.mode} type="button" onClick={() => onAction(action.mode)}>
          <strong>{action.label}</strong>
          <span>{action.description}</span>
        </button>
      ))}
    </section>
  );
}

export function StoryVersions({ activeJobId, jobs, onSelect }: { activeJobId: string | null; jobs: JobRecord[]; onSelect: (jobId: string) => void }) {
  const visibleJobs = jobs.slice(0, 5);
  return (
    <section className="storyteller-versions" aria-label="Recent versions">
      <div><p className="eyebrow">Recent versions</p></div>
      {visibleJobs.length ? visibleJobs.map((job, index) => (
        <button
          aria-pressed={job.id === activeJobId}
          className={job.id === activeJobId ? 'active' : ''}
          key={job.id}
          type="button"
          onClick={() => onSelect(job.id)}
        >
          <strong>{index === 0 ? 'v7' : `v${Math.max(1, 7 - index)}`}</strong>
          <span>{storyVersionTitle(job, index)}</span>
        </button>
      )) : <span>No versions yet</span>}
    </section>
  );
}

export function StoryOutline({ chapters, selectedChapter, onAddChapter, onSelect }: {
  chapters: StoryOutlineChapter[];
  selectedChapter: number;
  onAddChapter: () => void;
  onSelect: (chapterNumber: number, targetId: string) => void;
}) {
  const [showScenes, setShowScenes] = useState(true);
  return (
    <aside className="storyteller-outline" aria-label="Story outline">
      <div className="storyteller-panel-heading compact">
        <p className="eyebrow">Outline</p>
        <button
          aria-expanded={showScenes}
          aria-label={showScenes ? 'Collapse outline scenes' : 'Expand outline scenes'}
          type="button"
          onClick={() => setShowScenes((current) => !current)}
        >
          ☰
        </button>
      </div>
      {chapters.map((chapter) => (
        <article className={selectedChapter === chapter.number ? 'active' : ''} key={chapter.id}>
          <button type="button" onClick={() => onSelect(chapter.number, chapter.id)}>
            <strong>{chapter.label}</strong>
            <span>{chapter.title}</span>
          </button>
          {showScenes ? <ol>
            {chapter.scenes.map((scene, sceneIndex) => (
              <li className={selectedChapter === chapter.number && sceneIndex === 0 ? 'active' : ''} key={scene.id}>
                <button type="button" onClick={() => onSelect(chapter.number, scene.id)}>
                  <span>{scene.label}</span>
                  <strong>{scene.title}</strong>
                </button>
              </li>
            ))}
          </ol> : null}
        </article>
      ))}
      <button className="storyteller-add-chapter" type="button" onClick={onAddChapter}>Add chapter</button>
    </aside>
  );
}

export function StoryText({ text, outline }: { text: string; outline: StoryOutlineChapter[] }) {
  const blocks = useMemo(() => storyTextBlocks(text, outline), [text, outline]);
  return (
    <div className="storyteller-prose">
      {blocks.map((block, index) => {
        const key = `${block.kind}-${block.id ?? block.text.slice(0, 18)}-${index}`;
        if (block.kind === 'chapter') return <h3 id={block.id} key={key} tabIndex={-1}>{block.text}</h3>;
        if (block.kind === 'scene') return <h4 id={block.id} key={key} tabIndex={-1}>{block.text}</h4>;
        return <p key={key}>{block.text}</p>;
      })}
    </div>
  );
}
