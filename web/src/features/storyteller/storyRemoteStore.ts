import type { StoryDocument } from './storyDocument';

export function validateStoryDocumentForRemote(document: StoryDocument): string[] {
  const issues: string[] = [];
  if (!document.id) issues.push('Story id is missing.');
  if (!document.title) issues.push('Story title is missing.');
  if (!document.cast.some((character) => character.id === 'narrator')) issues.push('Narrator cast member is missing.');
  for (const chapter of document.chapters) {
    if (!chapter.id) issues.push('Chapter id is missing.');
    for (const block of chapter.blocks) {
      if (!document.cast.some((character) => character.id === block.speakerId)) issues.push(`Block ${block.id} references an unknown speaker.`);
    }
  }
  return issues;
}
