import { describe, expect, it } from 'vitest';
import { allStoryBlocks, buildStoryDocumentFromText, stripStorySpeakerMarkers } from './storyDocument';

function dialogueSpeaker(text: string, fragment: string): string | undefined {
  const document = buildStoryDocumentFromText({ title: 'Barn Mystery', text });
  const block = allStoryBlocks(document).find((candidate) => candidate.kind === 'dialogue' && candidate.text.includes(fragment));
  return block?.kind === 'dialogue' ? block.speakerName : undefined;
}

describe('Storyteller dialogue attribution', () => {
  it('uses explicit speaker markers as the source of truth', () => {
    const text = [
      'Farmer Giles had been losing patience. Clara leaned against the barn while young Thomas peered through a knothole.',
      '"Or perhaps it\'s just old Silas settling in for his evening nap," [speaker: Clara] Clara suggested, though her voice lacked conviction. Barnaby, perched on Giles’ shoulder, twitched an ear.',
      'Barnaby watched Clara and Thomas from the rafters. Giles muttered to himself while Clara frowned.',
    ].join('\n\n');

    expect(dialogueSpeaker(text, 'old Silas')).toBe('Clara');
  });

  it('can map otherwise ambiguous pronoun dialogue through speaker markers', () => {
    const text = [
      'Farmer Giles had been losing patience. Clara leaned against the barn while young Thomas peered through a knothole.',
      'And thus, he said "How is it going?" [speaker: Barnaby]. They all turned toward the rafters.',
      'Barnaby watched Clara and Thomas from above. Giles muttered to himself while Clara frowned.',
    ].join('\n\n');

    expect(dialogueSpeaker(text, 'How is it going')).toBe('Barnaby');
  });

  it('strips explicit speaker markers from readable and speakable text', () => {
    expect(stripStorySpeakerMarkers('"Hello." [speaker: Clara] Clara smiled.')).toBe('"Hello." Clara smiled.');
    const document = buildStoryDocumentFromText({ title: 'Barn Mystery', text: '"Hello." [speaker: Clara] Clara smiled. Clara waved again.' });
    const dialogue = allStoryBlocks(document).find((candidate) => candidate.kind === 'dialogue');
    expect(dialogue?.text).toBe('Hello.');
  });

  it('keeps trailing name attribution as a fallback for legacy prose without markers', () => {
    const text = [
      'Giles stared at the barn door while Barnaby, Clara, and Thomas clustered behind him.',
      '"What in tarnation is that?" Giles boomed, dropping his pitchfork.',
      'Barnaby watched Clara and Thomas from the rafters. Giles muttered to himself while Clara frowned.',
    ].join('\n\n');

    expect(dialogueSpeaker(text, 'tarnation')).toBe('Giles');
  });
});
