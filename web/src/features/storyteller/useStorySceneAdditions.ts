import { useState } from 'react';
import { StorySceneAddition } from './storyModel';
import { persistStorySceneAdditions, readStorySceneAdditions, upsertStorySceneAddition } from './storyTextModel';

/** Scenes and chapters added to stories in this browser (kept until the story is saved again). */
export function useStorySceneAdditions() {
  const [storySceneAdditions, setStorySceneAdditions] = useState<StorySceneAddition[]>(() => readStorySceneAdditions());

  function addStorySceneAddition(addition: StorySceneAddition): void {
    setStorySceneAdditions((current) => {
      const next = upsertStorySceneAddition(current, addition);
      persistStorySceneAdditions(next);
      return next;
    });
  }

  return { storySceneAdditions, addStorySceneAddition };
}
