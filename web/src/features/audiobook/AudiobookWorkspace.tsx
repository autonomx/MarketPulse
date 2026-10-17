import './AudiobookWorkspace.css';
import type { OmnixModuleDefinition } from '../../app/modules';
import { useAudiobookWorkspace } from './useAudiobookWorkspace';
import { AudiobookLibraryRail, AudiobookInspectorRail } from './AudiobookRails';
import { AudiobookEbookLibraryPage, AudiobookCreatePage } from './AudiobookLibraryPages';
import { AudiobookProjectHeader } from './AudiobookProjectHeader';
import { AudiobookProjectDialogs, AudiobookProjectTabs, AudiobookProduction } from './AudiobookProjectChrome';
import { AudiobookReviewPanels } from './AudiobookReviewPanels';
import { AudiobookManuscriptReview } from './AudiobookManuscriptReview';
import { AudiobookReviewQueue } from './AudiobookReviewQueue';

export function AudiobookWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const ws = useAudiobookWorkspace(module);
  const { ebookLibraryOpen, error, mobileRail, notice, project, projectId, projectQuery, setMobileRail } = ws;
  return (
    <main className="audiobook-workspace" aria-label={`${module.label} workspace`}>
      <AudiobookLibraryRail ws={ws} />

      <div className="audiobook-stage">
        <nav className="audiobook-mobile-navigation" aria-label="Mobile audiobook panels">
          <button type="button" aria-controls="audiobook-library-rail" aria-expanded={mobileRail === 'library'} onClick={() => setMobileRail('library')}>Library and projects</button>
          <button type="button" aria-controls="audiobook-outline-rail" aria-expanded={mobileRail === 'outline'} onClick={() => setMobileRail('outline')}>{ebookLibraryOpen ? 'Selected book details' : 'Outline and cast'}</button>
        </nav>
        {error && <p className="audiobook-message error" role="alert">{error}</p>}
        {notice && <p className="audiobook-message" role="status">{notice}</p>}
        <AudiobookEbookLibraryPage ws={ws} />
        <AudiobookCreatePage ws={ws} />
        {projectId && projectQuery.isLoading && <section className="audiobook-card"><p>Loading book…</p></section>}
        {projectId && projectQuery.isError && <section className="audiobook-card" role="alert"><p>Could not load this project.</p></section>}
        {project && <>
          <AudiobookProjectHeader ws={ws} />
          <AudiobookProjectDialogs ws={ws} />
          <AudiobookProjectTabs ws={ws} />
          <AudiobookReviewPanels ws={ws} />
          <AudiobookManuscriptReview ws={ws} />
          <AudiobookReviewQueue ws={ws} />
          <AudiobookProduction ws={ws} />
        </>}
      </div>

      <AudiobookInspectorRail ws={ws} />
    </main>
  );
}
